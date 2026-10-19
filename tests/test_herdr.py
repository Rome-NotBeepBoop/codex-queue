import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from codex_queue.backends import resolve, BackendUnavailable
from codex_queue.herdr import HerdrBackend, HerdrClient, private_json, reconcile, interrupt
from codex_queue.herdr_runner import run
from codex_queue.store import QueueError, WriterBusy, lock, git
from codex_queue.worker import Worker
from codex_queue.foreground import Foreground
from codex_queue.execution import runner_lease, write_control
from codex_queue.tui import QueueApp, QueueView, Details, Settings
from codex_queue.navigation import JobWizard
from codex_queue.extensions_ui import Extensions, HerdrScreen, ShortcutHelp, Confirmation
from textual.widgets import Input, OptionList, TextArea, Select, Button
from test_interactive import Fixture, SESSION


class FakeHerdr:
    session='default'
    def __init__(self): self.calls=[];self.workspaces=[];self.panes={};self.disconnect=False;self.ambiguous=False;self.busy=False
    def capabilities(self):
        if self.disconnect: raise BackendUnavailable('Disconnected')
        return 'herdr 0.9.3'
    def call(self,*args):
        self.calls.append(args)
        if args[:2]==('workspace','list'):return {'workspaces':self.workspaces}
        if args[:2]==('workspace','get'):return {'workspace':next(w for w in self.workspaces if w['workspace_id']==args[2])}
        if args[:2]==('workspace','report-metadata'):
            token=args[args.index('--token')+1].split('=',1)
            next(w for w in self.workspaces if w['workspace_id']==args[2]).setdefault('tokens',{})[token[0]]=token[1]
            return {}
        if args[:2] in (('workspace','create'),('tab','create')):
            n=len(self.panes)+1;workspace='w1'
            if args[0]=='workspace':self.workspaces=[{'workspace_id':workspace,'label':args[args.index('--label')+1]}]
            pane={'pane_id':f'w1:p{n}','tab_id':f'w1:t{n}','workspace_id':workspace,'cwd':args[args.index('--cwd')+1]}
            self.panes[pane['pane_id']]=pane
            return {'workspace':{'workspace_id':workspace},'tab':{'tab_id':pane['tab_id']},'root_pane':pane}
        if args[:2]==('pane','get'):return {'pane':self.panes[args[2]]}
        if args[:2]==('pane','process-info'):return {'process_info':{'shell_pid':10,'foreground_processes':[{'pid':20 if self.busy else 10}]}}
        if args[:2]==('pane','run') and self.ambiguous:raise BackendUnavailable('Outcome unknown')
        return {}


class ClientTests(unittest.TestCase):
    def test_missing_executable_and_disconnected_server(self):
        def missing(*a,**kw):raise FileNotFoundError()
        with self.assertRaises(BackendUnavailable):HerdrClient({},missing).capabilities()
        with self.assertRaises(BackendUnavailable):HerdrClient({},missing).call('workspace','list')
    def test_incompatible_schema(self):
        def old(*a,**kw):return subprocess.CompletedProcess(a[0],0,'{}','')
        with self.assertRaises(BackendUnavailable):HerdrClient({},old).capabilities()
    def test_json_errors_no_retry_or_shell_interpolation(self):
        calls=[]
        def fail(args,**kw):calls.append(args);return subprocess.CompletedProcess(args,0,json.dumps({'error':{'code':'unavailable'}}),'')
        with self.assertRaises(BackendUnavailable):HerdrClient({'herdr_session':'name;echo nope'},fail).call('workspace','list')
        self.assertEqual(len(calls),1);self.assertIn('name;echo nope',calls[0])
    def test_capability_check_against_protocol_and_methods(self):
        methods=['workspace.list','workspace.create','workspace.report_metadata','tab.create','pane.send_input','pane.get','pane.process_info']
        schema={'protocol':22,'schemas':{'request':{'oneOf':[{'properties':{'method':{'const':m}}} for m in methods]}}}
        def response(args,**kw):return subprocess.CompletedProcess(args,0,'herdr 0.9.3' if '--version' in args else json.dumps(schema),'')
        if os.name=='nt':
            with self.assertRaises(BackendUnavailable):HerdrClient({},response).capabilities()
        else:self.assertEqual(HerdrClient({},response).capabilities(),'herdr 0.9.3')


class BackendTests(Fixture,unittest.TestCase):
    setUp=Fixture.setup;tearDown=Fixture.cleanup
    def worker(self,client,config=None):
        return Worker(self.store,self.provider,simulation=True,config=config or {'herdr_enabled':True})
    def dispatch(self,client,session=False):
        job=self.store.add_session(SESSION,self.repo,prompt='private prompt',mode='immediate',ownership=True) if session else self.store.add('private prompt',self.repo,mode='immediate')
        with patch('codex_queue.herdr.HerdrClient',return_value=client):result=self.worker(client).once()
        return job,result
    def request(self,job):return Path(self.store.db.execute('SELECT request FROM backend_runs WHERE job=?',(job,)).fetchone()[0])
    def test_disabled_default_and_job_override(self):
        job=self.store.add('x',self.repo)
        self.assertEqual(resolve(self.store,{},self.store.get(job)).name,'direct')
        self.store.db.execute("UPDATE jobs SET backend='direct' WHERE id=?",(job,));self.store.db.commit()
        self.assertEqual(resolve(self.store,{'herdr_enabled':True},self.store.get(job)).name,'direct')
    def test_unavailable_keeps_job_queued_no_fallback(self):
        client=FakeHerdr();client.disconnect=True
        job,result=self.dispatch(client)
        self.assertEqual(result['state'],'waiting');self.assertEqual(self.store.get(job)['status'],'queued')
        self.assertFalse((self.state/'worktrees').exists())
    def test_user_workspace_conflict_then_explicit_adoption(self):
        client=FakeHerdr();client.workspaces=[{'workspace_id':'w1','label':'codex-queue'}]
        job,result=self.dispatch(client)
        self.assertEqual(self.store.get(job)['status'],'queued');self.assertNotIn(('workspace','close'),client.calls)
        backend=HerdrBackend(self.store,{},client);backend.adopt('w1')
        self.store.backoff(time.time(),reset=True)
        with patch('codex_queue.herdr.HerdrClient',return_value=client):self.worker(client).once()
        self.assertTrue(any(c[:2]==('tab','create') for c in client.calls))
        self.assertFalse(any(c[:2]==('workspace','create') for c in client.calls))
    def test_create_correct_directory_and_prompt_private(self):
        client=FakeHerdr();job,result=self.dispatch(client)
        self.assertEqual(result['state'],'running')
        data=json.loads(self.request(job).read_text());self.assertEqual(Path(data['cwd']),self.state/'worktrees'/f'{job}-1')
        command=next(c[-1] for c in client.calls if c[:2]==('pane','run'))
        self.assertNotIn('private prompt',command);self.assertEqual(len(shlex.split(command)),3)
        self.assertFalse(any('--focus' in c for c in client.calls))
        if os.name!='nt':self.assertEqual(self.request(job).stat().st_mode&0o777,0o600)
    def test_reconnect_does_not_resubmit_unknown_dispatch(self):
        client=FakeHerdr();client.ambiguous=True;job,result=self.dispatch(client)
        self.store.recover();self.assertEqual(self.store.get(job)['status'],'running')
        with patch('codex_queue.herdr.HerdrClient',return_value=client):self.worker(client).once()
        self.assertEqual(sum(c[:2]==('pane','run') for c in client.calls),1)
        with self.assertRaises(QueueError):self.store.transition(job,'retry')
    def test_busy_pane_never_receives_prompt(self):
        client=FakeHerdr();client.busy=True;job,result=self.dispatch(client)
        self.assertEqual(result['state'],'failed');self.assertFalse(any(c[:2]==('pane','run') for c in client.calls))
    def test_visual_done_not_completion_and_invalid_receipt_ignored(self):
        client=FakeHerdr();job,result=self.dispatch(client)
        request=self.request(job);data=json.loads(request.read_text())
        private_json(request.with_name('result.json'),dict(data,status='completed',exit_code=None))
        reconcile(self.store);self.assertEqual(self.store.get(job)['status'],'running')
        private_json(request.with_name('result.json'),dict(data,status='completed',exit_code=0,token='wrong'))
        reconcile(self.store);self.assertEqual(self.store.get(job)['status'],'running')
    def test_recycled_workspace_identity_blocks_dispatch_and_focus(self):
        client=FakeHerdr();job,result=self.dispatch(client)
        client.workspaces[0]['tokens']['queue_owner']='different-queue'
        backend=HerdrBackend(self.store,{},client)
        with self.assertRaises(BackendUnavailable):backend.preflight()
        with self.assertRaises(BackendUnavailable):backend.focus(job)
    def test_weekly_exhaustion_and_future_reset_do_not_dispatch(self):
        client=FakeHerdr();self.provider.weekly=100;job,result=self.dispatch(client)
        self.assertEqual(self.store.get(job)['status'],'queued');self.assertEqual(client.calls,[])
        self.provider.weekly=0;self.store.db.execute("UPDATE jobs SET mode='next-reset',saved_reset=? WHERE id=?",(int(time.time())+100,job));self.store.db.commit();self.store.backoff(time.time(),reset=True)
        with patch('codex_queue.herdr.HerdrClient',return_value=client):self.worker(client).once()
        self.assertEqual(client.calls,[])
    @unittest.skipIf(os.name=='nt','POSIX relay; Windows explicitly unsupported')
    def test_relay_completes_once_and_workspace_reused(self):
        client=FakeHerdr();job,result=self.dispatch(client)
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(run(self.request(job)),0)
        self.assertEqual(run(self.request(job)),2);reconcile(self.store)
        self.assertEqual(self.store.get(job)['status'],'completed')
        second=self.store.add('second',self.repo,mode='immediate');self.store.backoff(time.time(),reset=True)
        with patch('codex_queue.herdr.HerdrClient',return_value=client):self.worker(client).once()
        self.assertEqual(sum(c[:2]==('workspace','create') for c in client.calls),1)
        self.assertEqual(sum(c[:2]==('tab','create') for c in client.calls),1)
    @unittest.skipIf(os.name=='nt','POSIX relay')
    def test_original_session_directory_and_identity_preserved(self):
        client=FakeHerdr();(self.repo/'unfinished.txt').write_text('untouched')
        job,result=self.dispatch(client,True);data=json.loads(self.request(job).read_text())
        self.assertEqual(data['cwd'],str(self.repo));self.assertEqual(self.store.get(job)['session_id'],SESSION)
        with contextlib.redirect_stdout(io.StringIO()):run(self.request(job))
        reconcile(self.store);self.assertEqual((self.repo/'unfinished.txt').read_text(),'untouched')
        self.assertEqual(self.store.get(job)['status'],'completed')
    @unittest.skipIf(os.name=='nt','POSIX relay')
    def test_explicit_interrupt_and_repository_lock_survive_monitor(self):
        client=FakeHerdr();job,result=self.dispatch(client);request=self.request(job)
        data=json.loads(request.read_text());data['command']=[sys.executable,'-c',"import time;print('ready',flush=True);time.sleep(20)"]
        private_json(request,data)
        relay=Path(__file__).parents[1]/'codex_queue'/'herdr_runner.py'
        process=subprocess.Popen([sys.executable,str(relay),str(request)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'ready')
            with self.assertRaises(WriterBusy):
                with lock(Path(git(self.repo,'rev-parse','--absolute-git-dir'))/'codex-queue.lock'):pass
            interrupt(self.store,job);process.wait(timeout=5);reconcile(self.store)
            self.assertEqual(self.store.get(job)['status'],'paused')
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdout.close()
    def test_foreground_and_scheduled_check_share_backend(self):
        client=FakeHerdr()
        for mode in ('foreground','os'):
            self.store.add(mode,self.repo,mode='immediate');self.store.backoff(time.time(),reset=True)
            write_control(self.state,mode=mode,scheduler_enabled=mode=='os')
            with runner_lease(self.state,mode):
                with patch('codex_queue.herdr.HerdrClient',return_value=client):result=self.worker(client).once()
            self.assertEqual(result['state'],'running')
            row=self.store.rows()[-1];self.store.finish(row['id'],'paused','test fixture finished')
    def test_real_resume_command_is_prepared_but_never_executed(self):
        client=FakeHerdr();self.provider.live=True
        job=self.store.add_session(SESSION,self.repo,prompt='continue',mode='immediate',ownership=True)
        with patch('codex_queue.herdr.HerdrClient',return_value=client), patch('codex_queue.sessions.check_compatibility'):
            result=Worker(self.store,self.provider,config={'herdr_enabled':True}).once()
        data=json.loads(self.request(job).read_text())
        self.assertEqual(result['state'],'running');self.assertIn('resume',data['command']);self.assertIn(SESSION,data['command'])
        self.assertNotIn('--last',data['command']);self.assertFalse(self.request(job).with_name('submitted').exists())
    def test_active_session_refused_without_new_session(self):
        client=FakeHerdr();self.provider.live=True
        job=self.store.add_session(SESSION,self.repo,prompt='continue',mode='immediate',ownership=True)
        with patch('codex_queue.herdr.HerdrClient',return_value=client), patch('codex_queue.sessions.check_compatibility'), patch.object(self.provider,'read_thread',return_value={'id':SESSION,'cwd':str(self.repo),'status':{'type':'active'}}):
            Worker(self.store,self.provider,config={'herdr_enabled':True}).once()
        self.assertEqual(self.store.get(job)['status'],'paused');self.assertEqual(self.store.get(job)['session_id'],SESSION)
        self.assertFalse(any(c[:2]==('pane','run') for c in client.calls))
    @unittest.skipIf(os.name=='nt','POSIX relay')
    def test_failed_resume_identity_is_never_successful(self):
        client=FakeHerdr();job,result=self.dispatch(client,True);request=self.request(job)
        data=json.loads(request.read_text())
        event=json.dumps({'type':'thread.started','thread_id':'22222222-2222-4222-8222-222222222222'})
        data['command']=[sys.executable,'-c',f'print({event!r})'];private_json(request,data)
        with contextlib.redirect_stdout(io.StringIO()):run(request)
        reconcile(self.store);self.assertEqual(self.store.get(job)['status'],'failed');self.assertEqual(self.store.get(job)['session_id'],SESSION)
    @unittest.skipIf(os.name=='nt','POSIX relay')
    def test_large_prompt_cannot_block_timeout_before_stdin_is_read(self):
        client=FakeHerdr();job,result=self.dispatch(client);request=self.request(job)
        data=json.loads(request.read_text());data['prompt']='x'*262144;data['timeout']=.2
        data['command']=[sys.executable,'-c','import time;time.sleep(20)'];private_json(request,data)
        started=time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):run(request)
        self.assertLess(time.monotonic()-started,6);reconcile(self.store)
        self.assertEqual(self.store.get(job)['status'],'paused')


class KeyboardTests(Fixture,unittest.IsolatedAsyncioTestCase):
    setUp=Fixture.setup;tearDown=Fixture.cleanup
    async def activate(self,pilot,screen,selector):
        # Reach a control through real Tab traversal, then activate with Enter.
        for _ in range(40):
            if screen.focused and screen.focused.id==selector.lstrip('#'):break
            await pilot.press('tab')
        self.assertEqual(screen.focused.id,selector.lstrip('#'));await pilot.press('enter');await pilot.pause()
    async def test_every_primary_destination_and_resizing(self):
        app=QueueApp(self.state,self.root/'config.json',provider=self.provider)
        async with app.run_test(size=(100,30)) as pilot:
            for size in ((80,24),(100,30),(120,40),(160,45)):
                await pilot.resize_terminal(*size);await pilot.pause()
                self.assertGreater(app.query_one('#primary-nav').size.height,0)
            for index in (1,2,4,5,7,8,9):
                await app.open_destination(index);await pilot.pause()
                self.assertGreater(len(app.screen_stack),1);await pilot.press('escape');await pilot.pause()
            await pilot.press('?');await pilot.pause();self.assertIsInstance(app.screen,ShortcutHelp)
            await pilot.press('escape');await pilot.press('ctrl+p');await pilot.pause();await pilot.press('escape')
            self.assertEqual(self.provider.calls,0)
    async def test_keyboard_new_job_prompt_schedule_review_back(self):
        app=QueueApp(self.state,self.root/'config.json',provider=self.provider)
        async with app.run_test(size=(100,30)) as pilot:
            app.query_one('#quick-actions',OptionList).focus();await pilot.press('down','enter');await pilot.pause();view=app.screen
            view.query_one('#manual-repo',Input).focus();await pilot.press(*str(self.repo))
            await self.activate(pilot,view,'add-repo');view.query_one('#conversations',OptionList).focus();await pilot.press('enter');await pilot.pause()
            await pilot.press('h','i','enter','?');self.assertEqual(view.query_one(TextArea).text,'hi\n?')
            await self.activate(pilot,view,'next');await self.activate(pilot,view,'next')
            await pilot.press('escape');await pilot.pause();self.assertEqual(view.step,3)
            await self.activate(pilot,view,'next');await self.activate(pilot,view,'queue-job')
            self.assertEqual(self.store.rows()[0]['prompt'],'hi\n?')
    async def test_extensions_keyboard_save_disable_and_confirmation_cancel(self):
        app=QueueApp(self.state,self.root/'config.json',provider=self.provider)
        async with app.run_test(size=(80,24)) as pilot:
            await app.open_destination(8);await pilot.pause()
            await pilot.press('enter');await pilot.pause();self.assertIsInstance(app.screen,HerdrScreen)
            await self.activate(pilot,app.screen,'herdr-save');self.assertFalse(app.config['herdr_enabled'])
            invoked=[]
            async def confirm():invoked.append(True)
            app.push_screen(Confirmation('Test interruption',confirm));await pilot.pause()
            await pilot.press('enter');await pilot.pause();self.assertEqual(invoked,[])
            app.push_screen(Confirmation('Test interruption',confirm));await pilot.pause()
            await pilot.press('tab','enter');await pilot.pause();self.assertEqual(invoked,[True])
    async def test_extension_enable_is_explicit_and_focus_runs_off_ui_thread(self):
        client=FakeHerdr()
        job=self.store.add('mock job',self.repo,mode='immediate')
        with patch('codex_queue.herdr.HerdrClient',return_value=client):
            result=Worker(self.store,self.provider,simulation=True,config={'herdr_enabled':True}).once()
        app=QueueApp(self.state,self.root/'config.json',provider=self.provider)
        async with app.run_test(size=(100,30)) as pilot:
            app.push_screen(HerdrScreen());await pilot.pause()
            app.screen.query_one('#herdr-enabled').focus();await pilot.press('space')
            with patch('codex_queue.extensions_ui.HerdrClient',return_value=client):await self.activate(pilot,app.screen,'herdr-save')
            self.assertTrue(app.config['herdr_enabled']);self.assertFalse(app.config['execution_enabled']);self.assertIsNone(app.controller)
            backend=HerdrBackend(app.store,app.config,client)
            await asyncio.to_thread(backend.focus,job)
            self.assertIn(('tab','focus',result['tab']),client.calls)
    async def test_mouse_menu_still_works(self):
        app=QueueApp(self.state,self.root/'config.json',provider=self.provider)
        async with app.run_test(size=(100,30)) as pilot:
            await pilot.click('#nav-projects');await pilot.pause()
            self.assertIsInstance(app.screen,JobWizard)
    async def test_usage_refresh_and_watcher_permission_keyboard(self):
        from codex_queue.tui import UsageView
        app=QueueApp(self.state,self.root/'config.json',provider=self.provider)
        async with app.run_test(size=(80,24)) as pilot:
            await app.open_destination(6);await pilot.pause()
            self.assertIsInstance(app.screen,UsageView);self.assertEqual(self.provider.calls,1)
            await pilot.press('escape');await pilot.pause();await pilot.press('4');await pilot.pause();self.assertIsNone(app.controller)
    async def test_keyboard_existing_session_and_queue_management(self):
        app=QueueApp(self.state,self.root/'config.json',provider=self.provider)
        async with app.run_test(size=(100,30)) as pilot:
            app.query_one('#quick-actions',OptionList).focus();await pilot.press('down','enter');await pilot.pause();view=app.screen
            view.query_one('#manual-repo',Input).focus();await pilot.press(*str(self.repo));await self.activate(pilot,view,'add-repo')
            view.query_one('#manual-session',Input).focus();await pilot.press(*SESSION);await self.activate(pilot,view,'use-session')
            await pilot.press('c','o','n','t','i','n','u','e');view.query_one('#exclusive').focus();await pilot.press('space')
            await self.activate(pilot,view,'next');view.query_one('#job-backend',Select).focus()
            await pilot.press('enter','down','enter');await pilot.pause();self.assertEqual(view.query_one('#job-backend',Select).value,'direct')
            await pilot.press('shift+tab');self.assertEqual(view.focused.id,'schedule')
            await self.activate(pilot,view,'next');await self.activate(pilot,view,'queue-job')
            row=self.store.rows()[0];self.assertEqual(row['session_id'],SESSION);self.assertEqual(row['backend'],'direct')
            await pilot.press('3');await pilot.pause()
            app.screen.query_one('#jobs').focus();await pilot.press('right','left','enter');await pilot.pause();self.assertIsInstance(app.screen,Details)
            app.screen.query_one('#detail-backend',Select).focus();await pilot.press('enter','down','enter');await pilot.pause()
            await self.activate(pilot,app.screen,'detail-save-backend');self.assertEqual(self.store.get(row['id'])['backend'],'herdr')
            await pilot.press('escape');await pilot.pause();await self.activate(pilot,app.screen,'pause')
            self.assertEqual(self.store.get(row['id'])['status'],'paused')
