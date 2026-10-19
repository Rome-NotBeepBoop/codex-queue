"""Herdr 0.9.3 / protocol 22 CLI adapter. Never starts a Herdr server."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid
from .backends import BackendUnavailable
from .store import QueueError, lock


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as output:
        os.chmod(temporary, 0o600)
        json.dump(value, output)
    temporary.replace(path)


class HerdrClient:
    def __init__(self, config, run=None):
        self.executable = config.get('herdr_executable', 'herdr')
        self.session = config.get('herdr_session', 'default')
        self.run = run or subprocess.run

    def call(self, *args):
        try:
            response = self.run([self.executable, '--session', self.session, *args],
                                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BackendUnavailable('Herdr unavailable or request outcome unknown; no automatic retry.') from error
        if response.returncode:
            raise BackendUnavailable('Herdr request failed; check connection and diagnostics.')
        try:
            data = json.loads(response.stdout)
            if 'error' in data:
                raise BackendUnavailable('Herdr rejected the request: ' + str(data['error'].get('code', 'unknown')))
            return data['result']
        except (ValueError, KeyError, TypeError) as error:
            raise BackendUnavailable('Unsupported Herdr response; no fallback or retry.') from error

    def capabilities(self):
        try:
            version = self.run([self.executable, '--version'], capture_output=True, text=True, timeout=10)
            schema = self.run([self.executable, 'api', 'schema', '--json'], capture_output=True, text=True, timeout=10)
            data = json.loads(schema.stdout)
            methods = {entry['properties']['method']['const'] for entry in data['schemas']['request']['oneOf']}
            required = {'workspace.list', 'workspace.create', 'workspace.report_metadata', 'tab.create', 'pane.send_input', 'pane.get', 'pane.process_info'}
            if version.returncode or schema.returncode or data['protocol'] != 22 or not required <= methods:
                raise ValueError('incompatible schema')
            if os.name == 'nt':
                raise BackendUnavailable('Herdr execution adapter currently requires a POSIX local shell; Windows is unverified.')
            return version.stdout.strip()
        except BackendUnavailable:
            raise
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            raise BackendUnavailable('Herdr is missing or incompatible. Tested schema: 0.9.3, protocol 22.') from error

    def diagnostics(self):
        version = self.capabilities()
        # Explicit user connection check only. Never read terminal output or agent lists.
        result = self.call('workspace', 'list')
        integration = self.call('integration', 'status')
        hooks = next((item['state'] for item in integration.get('integrations', []) if item.get('target') == 'codex'), 'unknown')
        return {'installed': True, 'connected': True, 'version': version,
                'session': self.session, 'workspaces': result.get('workspaces', []), 'codex_hooks': hooks}


class HerdrBackend:
    name = 'herdr'
    persistent = True

    def __init__(self, store, config, client=None):
        self.store, self.config = store, config
        self.client = client or HerdrClient(config)
        self.path = store.state / 'herdr.json'

    def manifest(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def preflight(self):
        self.client.capabilities()
        data = self.client.call('workspace', 'list')
        managed = self.manifest()
        for workspace in data.get('workspaces', []):
            if workspace['workspace_id'] == managed.get('workspace'):
                if managed.get('session') != self.client.session:
                    raise BackendUnavailable('Managed workspace belongs to a different Herdr session.')
                if not managed.get('owner') or workspace.get('tokens', {}).get('queue_owner') != managed['owner']:
                    raise BackendUnavailable('Workspace ownership marker changed or is missing; no tabs will be added.')
                return
            if workspace.get('label') == self.config.get('herdr_workspace_label', 'codex-queue'):
                raise BackendUnavailable('Workspace name already exists. Explicitly adopt its ID or choose a separate workspace name in Extensions → Herdr.')
        if managed.get('workspace'):
            raise BackendUnavailable('Managed workspace is missing. Resolve ownership in Extensions → Herdr.')

    def adopt(self, workspace_id):
        with lock(self.store.state / 'herdr-management.lock'):
            if self.manifest().get('workspace'):
                raise QueueError('A workspace is already managed; do not replace it implicitly.')
            result = self.client.call('workspace', 'get', workspace_id)
            workspace = result['workspace']
            if workspace['workspace_id'] != workspace_id:
                raise QueueError('Workspace identity mismatch.')
            if workspace.get('tokens', {}).get('queue_owner'):
                raise QueueError('Workspace already has a queue ownership marker; do not adopt another queue’s workspace.')
            owner = uuid.uuid4().hex
            self.client.call('workspace', 'report-metadata', workspace_id, '--source', 'codex-queue', '--token', f'queue_owner={owner}')
            private_json(self.path, {'workspace': workspace_id, 'session': self.client.session,
                                     'owner': owner, 'adopted': True, 'jobs': {}})

    def start(self, row, cwd, command, prompt, log, timeout, simulation=False):
        self.preflight()
        with lock(self.store.state / 'herdr-management.lock'):
            managed = self.manifest()
            key = f"{row['id']}-{row['attempts']}"
            if key in managed.get('jobs', {}):
                raise QueueError('This attempt already has a pane; never resubmit an ambiguous dispatch.')
            if not managed.get('workspace'):
                result = self.client.call('workspace', 'create', '--cwd', str(cwd), '--label',
                                          self.config.get('herdr_workspace_label', 'codex-queue'), '--no-focus')
                managed = {'workspace': result['workspace']['workspace_id'], 'session': self.client.session,
                           'owner': uuid.uuid4().hex, 'jobs': {}, 'adopted': False}
                private_json(self.path, managed)
                self.client.call('workspace', 'report-metadata', managed['workspace'], '--source', 'codex-queue', '--token', f"queue_owner={managed['owner']}")
                tab, pane = result['tab']['tab_id'], result['root_pane']['pane_id']
            else:
                result = self.client.call('tab', 'create', '--workspace', managed['workspace'],
                                          '--cwd', str(cwd), '--label', f"{Path(row['repo']).name} · {row['id']}", '--no-focus')
                tab, pane = result['tab']['tab_id'], result['root_pane']['pane_id']
            self.client.call('tab', 'rename', tab, f"{Path(row['repo']).name} · {row['id']}")
            info = self.client.call('pane', 'get', pane)['pane']
            if info.get('workspace_id') != managed['workspace'] or info.get('tab_id') != tab or Path(info.get('cwd') or '').resolve() != cwd.resolve():
                raise QueueError('Managed pane identity or directory mismatch; no command submitted.')
            processes = self.client.call('pane', 'process-info', '--pane', pane)['process_info']
            shell = processes.get('shell_pid')
            foreground = processes.get('foreground_processes')
            if not shell or not foreground or any(process.get('pid') != shell for process in foreground):
                raise QueueError('New managed pane is not an available shell; no command submitted.')
            token = uuid.uuid4().hex
            folder = self.store.state / 'herdr-runs' / key
            folder.mkdir(parents=True, mode=0o700, exist_ok=True)
            request = folder / 'request.json'
            private_json(request, {'job': row['id'], 'attempt': row['attempts'], 'token': token,
                                   'state': str(self.store.state), 'cwd': str(cwd), 'command': command,
                                   'prompt': prompt, 'log': str(log), 'timeout': timeout,
                                   'simulation': simulation, 'config': self.config})
            managed.setdefault('jobs', {})[key] = {'tab': tab, 'pane': pane, 'cwd': str(cwd), 'token': token}
            private_json(self.path, managed)
            # Commit the durable no-resubmission fence BEFORE sending pane.run.
            with self.store.db:
                self.store.db.execute('INSERT INTO backend_runs(job,number,backend,request,token,tab,pane) VALUES(?,?,?,?,?,?,?)',
                                      (row['id'], row['attempts'], 'herdr', str(request), token, tab, pane))
            relay = Path(__file__).with_name('herdr_runner.py').resolve()
            shell_command = shlex.join([sys.executable, str(relay), str(request)])
            reason = 'Dispatched to Herdr; stopping monitoring leaves this job running.'
            try:
                self.client.call('pane', 'run', pane, shell_command)
            except BackendUnavailable:
                reason = 'Herdr dispatch outcome unknown; no automatic resubmission. Inspect managed pane.'
                self.store.db.execute("UPDATE jobs SET reason=? WHERE id=?", ('Herdr dispatch outcome unknown; do not retry. Inspect managed pane.', row['id']))
                self.store.db.commit()
            if self.config.get('herdr_auto_focus') is True:
                try: self.client.call('tab', 'focus', tab)
                except BackendUnavailable: pass
            return {'state': 'running', 'job': row['id'], 'backend': 'herdr', 'tab': tab, 'pane': pane,
                    'worktree': str(cwd), 'log': str(log), 'reason': reason}

    def focus(self, job=None):
        managed = self.manifest()
        if managed.get('session') != self.client.session or not managed.get('workspace'):
            raise QueueError('No managed workspace in this configured session.')
        self.preflight()
        if job:
            records = [(int(key.rsplit('-', 1)[1]), value) for key, value in managed.get('jobs', {}).items() if key.startswith(job + '-')]
            record = max(records, key=lambda item: item[0])[1] if records else None
            if not record: raise QueueError('This job has no managed Herdr tab.')
            info = self.client.call('pane', 'get', record['pane'])['pane']
            if info.get('tab_id') != record['tab'] or info.get('workspace_id') != managed['workspace']:
                raise QueueError('Managed tab identity changed; refusing to focus an unrelated tab.')
            self.client.call('tab', 'focus', record['tab'])
        else: self.client.call('workspace', 'focus', managed['workspace'])


def reconcile(store):
    """Only a private, matching runner receipt may prove completion."""
    for run in store.db.execute('SELECT * FROM backend_runs').fetchall():
        job = store.get(run['job'])
        if job['status'] != 'running' or job['attempts'] != run['number']: continue
        receipt = Path(run['request']).with_name('result.json')
        if not receipt.exists(): continue
        try:
            data = json.loads(receipt.read_text())
            if data['token'] != run['token'] or data['attempt'] != run['number'] or data['job'] != run['job']:
                continue
            if data['status'] not in ('completed', 'failed', 'paused', 'cancelled'): continue
            if type(data.get('exit_code')) is not int: continue
            if data['status'] == 'completed' and data['exit_code'] != 0: continue
            store.finish(run['job'], data['status'], data.get('reason', ''))
            with store.db:
                store.db.execute('UPDATE attempts SET session_id=? WHERE job=? AND number=?',
                                 (data.get('session_id'), run['job'], run['number']))
        except (ValueError, KeyError, TypeError):
            continue


def interrupt(store, job):
    row = store.get(job)
    run = store.db.execute('SELECT * FROM backend_runs WHERE job=? AND number=?', (job, row['attempts'])).fetchone()
    if not run or row['status'] != 'running': raise QueueError('No active managed Herdr run selected.')
    private_json(Path(run['request']).with_name('interrupt.json'), {'token': run['token']})
