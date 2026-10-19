"""Offline basic tracked-file and history audit. Never prints matched secrets."""
import os
from pathlib import Path
import re
import subprocess
import sys

working_only = "--working-only" in sys.argv

credentials = re.compile(r'(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:api_key|access_token|refresh_token)\s*[=:]\s*["\x27][A-Za-z0-9_./+-]{16,})', re.I)
private = re.compile(r'/Users/(?!example/|username/)[A-Za-z0-9._-]+/|(?:universe|place)[ _-]*(?:id)?\s*[`:=]*\s*\d{9,}', re.I)
findings = set()

git_env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}

if working_only:
    refs = [None]
else:
    try:
        all_refs = subprocess.check_output(['git', 'rev-list', '--all'], text=True, env=git_env).splitlines()
    except (subprocess.SubprocessError, OSError):
        all_refs = []
    refs = [None, *all_refs]

for ref in refs:
    try:
        cmd = ['git', 'ls-files', '--cached', '--others', '--exclude-standard'] if ref is None else ['git', 'ls-tree', '-r', '--name-only', ref]
        names = subprocess.check_output(cmd, text=True, env=git_env).splitlines()
    except (subprocess.SubprocessError, OSError):
        continue
    for name in names:
        try:
            data = Path(name).read_bytes() if ref is None else subprocess.check_output(['git', 'show', f'{ref}:{name}'], env=git_env)
            data = data.decode()
        except (OSError, UnicodeError, subprocess.SubprocessError):
            continue
        if credentials.search(data):
            findings.add((name, 'credential pattern', 'working tree' if ref is None else 'history'))
        # Exclude this audit's pattern definitions from personal-path checks.
        if name != 'scripts/security-check.py' and private.search(data):
            findings.add((name, 'private identifier/path', 'working tree' if ref is None else 'history'))

for name, kind, scope in sorted(findings):
    print(f'{scope}: {name}: {kind}')
if findings:
    print('Publication blocked: privately review findings; do not rewrite history automatically.')
else:
    print('Basic audit found no configured patterns. Manual review remains necessary.')
sys.exit(bool(findings))
