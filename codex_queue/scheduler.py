"""Optional per-user native scheduler. Only exact, verified owned objects mutate."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from .execution import control, write_control
from .store import QueueError, lock


class NativeScheduler:
    def __init__(self, state, config, platform=None, home=None, run=None):
        self.state = Path(state).expanduser().resolve()
        self.config = Path(config).expanduser().resolve()
        self.platform = platform or sys.platform
        self.home = Path(home or Path.home())
        self.run = run or subprocess.run
        self.directory = self.state / 'scheduler'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest = hashlib.sha256(str(self.state).encode()).hexdigest()[:16]
        self.name = 'local.codex-queue.' + digest
        self.manifest = self.directory / 'manifest.json'
    def command(self, *args, check=True):
        result = self.run(list(args), capture_output=True, text=True, timeout=30)
        if check and result.returncode:
            raise QueueError('Native scheduler refused the operation; check user permissions. ' + (result.stderr or result.stdout)[-1000:])
        return result
    def files(self):
        if self.platform == 'darwin': return [self.home / 'Library/LaunchAgents' / (self.name+'.plist')]
        if self.platform.startswith('linux'):
            root = Path(os.environ.get('XDG_CONFIG_HOME', self.home / '.config')) / 'systemd/user'
            return [root/(self.name+'.service'), root/(self.name+'.timer')]
        if self.platform == 'win32': return [self.directory/(self.name+'.xml')]
        raise QueueError('No native scheduler integration on this platform')
    def target(self): return f'gui/{getattr(os, "getuid", lambda: 0)()}/{self.name}'
    def owned(self):
        if not self.manifest.exists(): raise QueueError('Install this queue’s scheduler first')
        data = json.loads(self.manifest.read_text())
        expected = [str(p) for p in self.files()]
        if data.get('name') != self.name or list(data.get('files',{})) != expected:
            raise QueueError('Scheduler ownership record differs; no tasks changed')
        for path,digest in data['files'].items():
            file = Path(path)
            if file.is_symlink() or not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
                raise QueueError('Managed scheduler definition changed; no tasks changed')
        if self.platform == 'win32':
            result = self.command('schtasks','/Query','/TN',self.name,'/XML')
            tree = ET.fromstring(result.stdout)
            description = next((e.text for e in tree.iter() if e.tag.endswith('}Description')),None)
            if description != self.name: raise QueueError('Native task ownership differs; no tasks changed')
        return data
    def native_owned(self):
        """Verify loaded definitions too; a matching name alone is not ownership."""
        if self.platform=='darwin':
            result=self.command('launchctl','print',self.target(),check=False)
            if result.returncode:
                if result.returncode==113: return False
                raise QueueError('Cannot verify loaded launchd task; no native tasks changed')
            match=re.search(r'^\s*path = (.+)$',result.stdout,re.M)
            if not match or Path(match.group(1).strip())!=self.files()[0]:
                raise QueueError('Loaded launchd task has a different origin; no native tasks changed')
            return True
        if self.platform.startswith('linux'):
            for target in self.files():
                result=self.command('systemctl','--user','show',target.name,'--property=FragmentPath')
                value=result.stdout.strip().removeprefix('FragmentPath=')
                if not value or Path(value)!=target:raise QueueError('Loaded systemd unit has a different origin; no native tasks changed')
            return True
        result=self.command('schtasks','/Query','/TN',self.name,'/XML')
        actual=ET.fromstring(result.stdout)
        expected=ET.fromstring(self.files()[0].read_bytes())
        def execution(root):
            item=next((e for e in root.iter() if e.tag.endswith('}Exec')),None)
            return [(e.tag.split('}')[-1],e.text or '') for e in item] if item is not None else []
        if execution(actual)!=execution(expected):raise QueueError('Native task action differs; no tasks changed')
        return True

    def render(self, interval):
        if type(interval) is not int or not 10 <= interval <= 3600:
            raise QueueError('check interval must be 10–3600 seconds')
        args = [sys.executable, '-m', 'codex_queue.cli', '--state', str(self.state), '--config', str(self.config), 'scheduled-check']
        if self.platform == 'darwin':
            return [plistlib.dumps({'Label':self.name,'Disabled':True,'RunAtLoad':False,'KeepAlive':False,'StartInterval':interval,'ProgramArguments':args,'WorkingDirectory':str(self.state),'EnvironmentVariables':{'PATH':os.environ.get('PATH','/usr/bin:/bin')},'StandardOutPath':str(self.directory/'native.log'),'StandardErrorPath':str(self.directory/'native-error.log'),'ExitTimeOut':30,'Umask':0o077})]
        if self.platform.startswith('linux'):
            def quote(v): return '"'+str(v).replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'
            service = f'[Unit]\nDescription={self.name}\n[Service]\nType=oneshot\nExecStart={" ".join(map(quote,args))}\nWorkingDirectory={quote(self.state)}\nEnvironment={quote("PATH="+os.environ.get("PATH","/usr/bin:/bin"))}\nUMask=0077\nTimeoutStartSec=infinity\nTimeoutStopSec=30\nKillMode=control-group\n'
            timer = f'[Unit]\nDescription={self.name}\n[Timer]\nOnActiveSec={interval}s\nOnUnitInactiveSec={interval}s\nAccuracySec=1s\nUnit={self.name}.service\n[Install]\nWantedBy=timers.target\n'
            return [service.encode(),timer.encode()]
        if self.platform == 'win32':
            if interval < 60: raise QueueError('Windows Task Scheduler requires at least 60 seconds')
            ns='http://schemas.microsoft.com/windows/2004/02/mit/task'
            ET.register_namespace('',ns)
            def child(parent,name,text=None):
                e=ET.SubElement(parent,'{'+ns+'}'+name)
                if text is not None:e.text=str(text)
                return e
            root=ET.Element('{'+ns+'}Task',{'version':'1.2'})
            child(child(root,'RegistrationInfo'),'Description',self.name)
            trigger=child(child(root,'Triggers'),'TimeTrigger')
            child(trigger,'StartBoundary','2020-01-01T00:00:00');child(trigger,'Enabled','true')
            repeat=child(trigger,'Repetition');child(repeat,'Interval',f'PT{interval}S');child(repeat,'StopAtDurationEnd','false')
            principal=child(child(root,'Principals'),'Principal');principal.set('id','Author')
            # Interactive token, least privilege, no stored passwords or SYSTEM.
            child(principal,'UserId',getpass.getuser());child(principal,'LogonType','InteractiveToken');child(principal,'RunLevel','LeastPrivilege')
            settings=child(root,'Settings');child(settings,'MultipleInstancesPolicy','IgnoreNew');child(settings,'Enabled','false');child(settings,'ExecutionTimeLimit','PT0S')
            actions=child(root,'Actions');actions.set('Context','Author');execution=child(actions,'Exec')
            child(execution,'Command',args[0]);child(execution,'Arguments',subprocess.list2cmdline(args[1:]));child(execution,'WorkingDirectory',self.state)
            return [ET.tostring(root,encoding='utf-8',xml_declaration=True)]
        raise QueueError('Unsupported native scheduler platform')
    def configure(self, interval):
        # Staging is inert; installed definitions only change via explicit Install.
        payloads=self.render(interval)
        for index,payload in enumerate(payloads):
            target=self.directory/f'prepared-{index}'
            target.write_bytes(payload);target.chmod(0o600)
        (self.directory/'prepared.json').write_text(json.dumps({'interval':interval}))
        return {'status':'configured, not installed or enabled','interval':interval}
    def install(self):
        with lock(self.state/'mode.lock'), lock(self.state/'execution.lock'):
            if control(self.state)['scheduler_enabled']: raise QueueError('Disable the scheduler before installing changes')
            prepared=self.directory/'prepared.json'
            if not prepared.exists(): raise QueueError('Configure an interval first')
            interval=json.loads(prepared.read_text())['interval'];payloads=self.render(interval);targets=self.files()
            if self.manifest.exists():
                self.owned(); self.native_owned()
            elif any(p.exists() or p.is_symlink() for p in targets): raise QueueError('Existing scheduler files are not owned; no files changed')
            previous={target:target.read_bytes() if target.exists() else None for target in targets}
            try:
                for target,payload in zip(targets,payloads):
                    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    target.write_bytes(payload);target.chmod(0o600)
                if self.platform.startswith('linux'): self.command('systemctl','--user','daemon-reload')
                if self.platform=='win32':
                    # New registrations never force an overwrite. Updates require
                    # verified ownership of both the file and native task action.
                    command=['schtasks','/Create','/TN',self.name,'/XML',str(targets[0])]
                    if self.manifest.exists():command.append('/F')
                    self.command(*command)
            except BaseException:
                for target,payload in previous.items():
                    if payload is None:target.unlink(missing_ok=True)
                    else:target.write_bytes(payload)
                raise
            data={'name':self.name,'interval':interval,'files':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in targets}}
            self.manifest.write_text(json.dumps(data));self.manifest.chmod(0o600)
            from .settings import load, save
            settings=load(self.config);settings['poll_interval_seconds']=interval;save(self.config,settings)
            write_control(self.state,scheduler_enabled=False)
            return self.status()
    def status(self):
        selection=control(self.state)
        if not self.manifest.exists():
            staged=self.directory/'prepared.json'
            interval=json.loads(staged.read_text()).get('interval') if staged.exists() else None
            return {**selection,'native':'not installed','name':self.name,'interval':interval}
        try:
            data=self.owned()
            self.native_owned()
            if self.platform=='darwin':
                result=self.command('launchctl','print',self.target(),check=False)
                # A failed query is reported verbatim, never interpreted as disabled.
                native='loaded' if result.returncode==0 else 'not loaded or query unavailable'
            elif self.platform.startswith('linux'):
                result=self.command('systemctl','--user','show',self.name+'.timer','--property=ActiveState,UnitFileState',check=False)
                native=result.stdout.strip() if result.returncode==0 else 'query unavailable'
            else:
                result=self.command('schtasks','/Query','/TN',self.name,'/XML')
                root=ET.fromstring(result.stdout)
                settings=next((e for e in root.iter() if e.tag.endswith('}Settings')),None)
                enabled=next((e.text for e in settings if e.tag.endswith('}Enabled')),None) if settings is not None else None
                native='enabled' if enabled=='true' else 'disabled' if enabled=='false' else 'unknown'
            return {**selection,'native':native,'name':self.name,'interval':data['interval']}
        except (QueueError,OSError,ValueError,ET.ParseError) as error:
            return {**selection,'native':'unknown','error':str(error),'name':self.name}
    def enable(self, execution_enabled):
        if execution_enabled is not True: raise QueueError('Real execution is disabled; enable it explicitly in Settings first')
        with lock(self.state/'mode.lock'), lock(self.state/'execution.lock'):
            self.owned()
            loaded=self.native_owned()
            if self.platform=='darwin':
                self.command('launchctl','enable',self.target())
                if not loaded: self.command('launchctl','bootstrap',f'gui/{getattr(os, "getuid", lambda: 0)()}',str(self.files()[0]))
            elif self.platform.startswith('linux'): self.command('systemctl','--user','enable','--now',self.name+'.timer')
            else: self.command('schtasks','/Change','/TN',self.name,'/ENABLE')
            write_control(self.state,mode='os',scheduler_enabled=True)
        return self.status()
    def disable(self):
        with lock(self.state/'mode.lock'):
            # Stop future submissions even if the native control command fails.
            write_control(self.state,scheduler_enabled=False)
            if self.manifest.exists():
                self.owned()
                loaded=self.native_owned()
                if self.platform=='darwin':
                    self.command('launchctl','disable',self.target())
                    if loaded:self.command('launchctl','bootout',self.target())
                elif self.platform.startswith('linux'):
                    self.command('systemctl','--user','disable','--now',self.name+'.timer')
                    self.command('systemctl','--user','stop',self.name+'.service')
                else:self.command('schtasks','/Change','/TN',self.name,'/DISABLE')
        return self.status()
    def foreground(self):
        self.disable()
        with lock(self.state/'mode.lock'), lock(self.state/'execution.lock'):
            write_control(self.state,mode='foreground',scheduler_enabled=False)
        return self.status()
    def select_os(self):
        self.disable()
        with lock(self.state/'mode.lock'), lock(self.state/'execution.lock'):
            write_control(self.state,mode='os',scheduler_enabled=False)
        return self.status()
    def uninstall(self):
        self.disable()
        with lock(self.state/'mode.lock'), lock(self.state/'execution.lock'):
            self.owned()
            if self.platform=='win32':self.command('schtasks','/Delete','/TN',self.name,'/F')
            for file in self.files():file.unlink()
            if self.platform.startswith('linux'):self.command('systemctl','--user','daemon-reload')
            self.manifest.unlink();write_control(self.state,mode='foreground',scheduler_enabled=False)
        return self.status()
    def logs(self):
        text=[]
        for name in ('checks.jsonl','native.log','native-error.log'):
            file=self.directory/name
            if file.exists():
                with file.open('rb') as stream:
                    stream.seek(0,2);stream.seek(max(0,stream.tell()-100000));text.append(stream.read().decode(errors='replace'))
        return '\n'.join(text) or 'No managed scheduler logs yet.'
