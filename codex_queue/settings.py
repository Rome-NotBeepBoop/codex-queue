import json
import os
from pathlib import Path
from .store import QueueError

DEFAULTS = {'codex': 'codex', 'bucket': 'codex', 'execution_enabled': False,
            'poll_interval_seconds': 60, 'job_timeout_seconds': 3600,
            'default_repo': '', 'default_mode': 'manual', 'herdr_enabled': False,
            'herdr_executable': 'herdr', 'herdr_session': 'default',
            'herdr_workspace_label': 'codex-queue', 'herdr_auto_focus': False,
            'interface_mode': 'tui', 'cli_mode': False}


def is_cli_mode(config):
    if not isinstance(config, dict):
        return False
    if config.get('interface_mode') == 'cli':
        return True
    if config.get('cli_mode') in (True, 'cli'):
        return True
    return False


def load(path):
    data = {}
    if path:
        p = Path(path).expanduser()
        if p.is_file():
            try:
                data = json.loads(p.read_text())
            except json.JSONDecodeError as err:
                raise QueueError(f"Corrupted config file {p}: {err}") from err
            except OSError:
                data = {}
    return {**DEFAULTS, **data}


def toggle_cli_mode(path):
    config = load(path)
    new_cli = not is_cli_mode(config)
    config['interface_mode'] = 'cli' if new_cli else 'tui'
    config['cli_mode'] = new_cli
    if path:
        save(path, config)
    return config


def save(path, values):
    if not path:
        raise QueueError("Config path must not be empty")
    if type(values.get('poll_interval_seconds')) is not int or not 10 <= values['poll_interval_seconds'] <= 3600:
        raise QueueError('poll interval must be between 10 and 3600 seconds')
    if type(values.get('execution_enabled')) is not bool:
        raise QueueError('execution permission must be explicitly true or false')
    if values.get('default_mode') not in ('manual', 'immediate', 'next-reset'):
        raise QueueError('invalid default scheduling mode')
    for name in ('herdr_enabled', 'herdr_auto_focus'):
        if type(values.get(name, False)) is not bool:
            raise QueueError('Herdr preferences must be explicit true or false')
    if 'interface_mode' in values and values['interface_mode'] not in ('tui', 'cli'):
        raise QueueError('interface_mode must be "tui" or "cli"')
    if 'cli_mode' in values and type(values['cli_mode']) is not bool and values['cli_mode'] not in ('tui', 'cli'):
        raise QueueError('cli_mode must be boolean or "tui"/"cli"')
    if 'interface_mode' in values and 'cli_mode' not in values:
        values['cli_mode'] = (values['interface_mode'] == 'cli')
    elif 'cli_mode' in values and 'interface_mode' not in values:
        values['interface_mode'] = 'cli' if (values['cli_mode'] in (True, 'cli')) else 'tui'
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(values, indent=2) + '\n')
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except Exception:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
        raise

