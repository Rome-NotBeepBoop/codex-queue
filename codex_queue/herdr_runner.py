"""Private pane relay: holds writer locks, tees real output, writes exit receipts.

Launched by absolute path so a Herdr shell need not activate a virtualenv.
No prompt is passed through shell arguments. Never reruns a submitted request.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_queue.store import Store, QueueError, WriterBusy, git, lock
from codex_queue.herdr import private_json
from codex_queue.processes import terminate, WindowsJob
from codex_queue import sessions


def run(request_path, provider=None):
    request_path = Path(request_path).resolve()
    data = json.loads(request_path.read_text())
    folder = request_path.parent
    # Atomic forever fence. A restarted pane/duplicate shell command cannot resubmit.
    try:
        fd = os.open(folder / 'submitted', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    except FileExistsError:
        return 2
    store = Store(data['state'])
    result = {'job': data['job'], 'attempt': data['attempt'], 'token': data['token'],
              'status': 'paused', 'exit_code': -1, 'reason': 'Not submitted; review before retry.', 'session_id': None}
    process = ownership = None
    contexts = []
    try:
        row = store.get(data['job'])
        if row['status'] != 'running' or row['attempts'] != data['attempt']:
            raise QueueError('Attempt no longer owns the job.')
        common = Path(git(row['repo'], 'rev-parse', '--git-common-dir'))
        if not common.is_absolute(): common = Path(row['repo']) / common
        # Parent dispatch releases these locks before this relay acquires them.
        deadline = time.monotonic() + 30
        for path in (store.state / 'worker.lock', common.resolve() / 'codex-queue.lock'):
            while True:
                context = lock(path)
                try:
                    handle = context.__enter__(); contexts.append((context, handle)); break
                except WriterBusy:
                    if time.monotonic() >= deadline: raise
                    time.sleep(.1)
        if not data['simulation']:
            if data['config'].get('execution_enabled') is not True:
                raise QueueError('Real execution remains disabled.')
            if provider is None:
                from codex_queue.app_server import AppServer
                provider = AppServer(data['config'].get('codex', 'codex'), data['config'].get('bucket', 'codex'))
            if not getattr(provider, 'live', False): raise QueueError('Fixture cannot authorize real execution.')
            usage = provider.read()
            ok, reason = usage.eligible(time.time(), row['saved_reset'] if row['mode'] == 'next-reset' else None)
            if not ok: raise QueueError(reason)
            if row['kind'] == 'session':
                sessions.validate_metadata(provider.read_thread(row['session_id']), row)
        cwd = Path(data['cwd']).resolve()
        tree = Path(store.db.execute('SELECT worktree FROM attempts WHERE job=? AND number=?', (row['id'], row['attempts'])).fetchone()[0]).resolve()
        if not cwd.is_dir() or not cwd.is_relative_to(tree): raise QueueError('Attempt working directory is invalid.')
        if row['kind'] == 'session' and cwd != Path(row['session_cwd']).resolve(): raise QueueError('Original session directory changed.')
        if store.get(row['id'])['requested']: raise QueueError('Interrupted before submission.')
        env = {k: v for k, v in os.environ.items() if k in ('PATH','HOME','CODEX_HOME','LANG','LC_ALL','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR') or k.startswith('HERDR_')}
        env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1')
        log = Path(data['log'])
        with log.open('w') as output:
            os.chmod(log, 0o600)
            process = subprocess.Popen(data['command'], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, env=env, text=True, start_new_session=True,
                                       pass_fds=tuple(h.fileno() for _, h in contexts))
            ownership = WindowsJob(process)
            # Separate reader prevents a silent child from blocking interruption checks.
            import threading
            captured = []
            def copy_output():
                for line in process.stdout:
                    output.write(line); output.flush()
                    print(line, end='', flush=True)
                    try:
                        event = json.loads(line)
                        if event.get('type') == 'thread.started': captured.append(sessions.identifier(event.get('thread_id')))
                    except (ValueError, QueueError): pass
            reader = threading.Thread(target=copy_output, daemon=False); reader.start()
            def write_prompt():
                try: process.stdin.write(data['prompt'])
                except (BrokenPipeError, OSError): pass
                finally:
                    try: process.stdin.close()
                    except (BrokenPipeError, OSError): pass
            writer = threading.Thread(target=write_prompt, daemon=False); writer.start()
            deadline = time.monotonic() + data['timeout']
            result['status'], result['reason'] = 'completed', ''
            while process.poll() is None:
                requested = store.get(row['id'])['requested']
                marker = folder / 'interrupt.json'
                interrupt = marker.exists() and json.loads(marker.read_text()).get('token') == data['token']
                if requested or interrupt or time.monotonic() >= deadline:
                    result['status'] = 'cancelled' if requested == 'cancel' else 'paused'
                    result['reason'] = 'interrupted: partial work preserved; review before retry'
                    terminate(process); break
                time.sleep(.1)
            process.wait(); writer.join(); reader.join(); process.stdout.close()
            result['exit_code'] = process.returncode
            result['session_id'] = captured[-1] if captured else row.get('session_id')
            if result['status'] == 'completed' and process.returncode != 0:
                result.update(status='failed', reason='Runner exited unsuccessfully; review private log.')
            if row['kind'] == 'session' and any(value != row['session_id'] for value in captured):
                result.update(status='failed', reason='Session identity mismatch; no new-session fallback.')
    except Exception as error:
        result['reason'] = str(error) if isinstance(error, QueueError) else f'{type(error).__name__}: not submitted or outcome requires review.'
    finally:
        if process is not None:
            terminate(process)
            if process.stdout and not process.stdout.closed: process.stdout.close()
            if process.returncode is not None: result['exit_code'] = process.returncode
        if ownership: ownership.close()
        # Receipt is durable BEFORE locks are released; recovery never races completion.
        private_json(folder / 'result.json', result)
        for context, _ in reversed(contexts): context.__exit__(None, None, None)
        store.close()
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(run(sys.argv[1]))
