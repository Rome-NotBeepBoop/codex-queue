"""Exact-session continuation; no global history scan and no new-session fallback."""
from pathlib import Path
import subprocess
import uuid
from .store import QueueError


def identifier(value):
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise QueueError('session ID must be a UUID, not --last or a name') from error
    if canonical != value.lower():
        raise QueueError('use a complete hyphenated session UUID')
    return canonical


def validate_metadata(metadata, job):
    if metadata.get('id') != job['session_id']:
        raise QueueError('session missing or identifier mismatch; no new session will be started')
    if metadata.get('status', {}).get('type') not in ('idle', 'notLoaded'):
        raise QueueError('session active or status unknown; release ownership before continuing')
    if Path(metadata.get('cwd', '')).resolve() != Path(job['session_cwd']).resolve():
        raise QueueError('session working directory differs; preserve its original checkout')
    if not job['ownership_confirmed']:
        raise QueueError('exclusive ownership must be confirmed by the previous session owner')


def check_compatibility(codex):
    result = subprocess.run([codex, 'exec', 'resume', '--help'], capture_output=True, text=True, timeout=10)
    if result.returncode or '[SESSION_ID]' not in result.stdout or '--json' not in result.stdout:
        raise QueueError('Codex CLI lacks the verified non-interactive resume interface; update it')


def command(codex, session_id, final):
    # Parent exec options supply the same sandbox as a new queue job.
    return [codex, '--ask-for-approval', 'never', 'exec', '--ignore-user-config',
            '--sandbox', 'workspace-write', '-c', 'sandbox_workspace_write.network_access=false',
            'resume', identifier(session_id), '--json', '-o', str(final), '-']
