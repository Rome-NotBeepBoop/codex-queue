"""Durable mode selection and crash-safe, shared process leases."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from .store import lock, QueueError


def control(state):
    path = Path(state) / 'execution.json'
    value = json.loads(path.read_text()) if path.exists() else {}
    return {'mode': 'foreground', 'scheduler_enabled': False, **value}


def write_control(state, **values):
    path = Path(state) / 'execution.json'
    data = {**control(state), **values}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n'); os.chmod(tmp, 0o600); tmp.replace(path)


@contextmanager
def runner_lease(state, mode):
    state = Path(state); state.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Hold across the entire foreground lifetime or scheduled check. No expiry
    # permits overlapping long jobs. Kernel releases it when all owners exit.
    with lock(state / 'mode.lock'):
        handle_context = lock(state / 'execution.lock')
        handle = handle_context.__enter__()
        try:
            data = control(state)
            if data['mode'] != mode or (mode == 'os' and data['scheduler_enabled'] is not True):
                raise QueueError('execution mode is inactive; use Execution Mode settings')
            if mode == 'foreground' and data['scheduler_enabled']:
                raise QueueError('disable the managed OS scheduler before watching')
        except BaseException:
            handle_context.__exit__(None, None, None)
            raise
    try:
        yield handle
    finally:
        handle_context.__exit__(None, None, None)
