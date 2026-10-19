#!/usr/bin/env python3
"""Prepare a disabled per-user launchd worker. Never loads or starts a service."""
import argparse
import json
from pathlib import Path
import plistlib


def prepare(executable, directory):
    executable = Path(executable).expanduser().resolve()
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = directory / 'state'
    logs = directory / 'logs'
    pending = directory / 'launchd'
    for path in (state, logs, pending):
        path.mkdir(exist_ok=True, mode=0o700)
    config = directory / 'config.json'
    if config.exists():
        data = json.loads(config.read_text())
        if data.get('execution_enabled') is not False:
            raise ValueError('existing execution gate must be false; no activation is performed')
    else:
        config.write_text(json.dumps({'execution_enabled': False, 'codex': 'codex',
                                     'bucket': 'codex', 'job_timeout_seconds': 3600}, indent=2) + '\n')
        config.chmod(0o600)
    payload = {
        'Label': 'local.codex-queue',
        'Disabled': True,
        'RunAtLoad': False,
        'KeepAlive': False,
        'StartInterval': 60,
        'ProgramArguments': [str(executable), '--state', str(state), '--config', str(config),
                             'worker', '--once', '--execute'],
        'WorkingDirectory': str(directory),
        'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'},
        'StandardOutPath': str(logs / 'worker.log'),
        'StandardErrorPath': str(logs / 'worker-error.log'),
        'ProcessType': 'Background',
        'ExitTimeOut': 30,
        'Umask': 0o077,
    }
    target = pending / 'local.codex-queue.plist'
    target.write_bytes(plistlib.dumps(payload))
    target.chmod(0o600)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True)
    parser.add_argument('--directory', default=str(Path.home() / 'Library/Application Support/codex-queue'))
    args = parser.parse_args()
    print(prepare(args.executable, args.directory))
