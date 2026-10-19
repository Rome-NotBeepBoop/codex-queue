import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET
from codex_queue.cli import main
from codex_queue.execution import control, write_control, runner_lease
from codex_queue.foreground import Foreground
from codex_queue.navigation import JobWizard
from codex_queue.projects import Catalog
from codex_queue.scheduler import NativeScheduler
from codex_queue.scheduler_ui import SchedulerScreen
from codex_queue.store import QueueError, WriterBusy, lock
from codex_queue.tui import QueueApp, Settings
from textual.widgets import Button, Input, OptionList, TextArea, Select, Switch
from test_interactive import Fixture, Provider, SESSION


class CatalogTests(unittest.TestCase):
    def test_group_filter_source_activity_and_no_file_access(self):
        catalog=Catalog();root=str(Path(tempfile.gettempdir())/'synthetic-navigation-repo')
        with patch('codex_queue.projects.git', side_effect=AssertionError('no project probes')):
            catalog.ingest([{'id':SESSION,'cwd':str(Path(root)/'sub'),'projectPath':root,'name':'Fix search','source':'exec','updatedAt':100,'preview':'Prior task','status':{'type':'idle'}},
                            {'id':'22222222-2222-4222-8222-222222222222','cwd':root,'projectPath':root,'name':'Other','source':'cli','updatedAt':200,'status':{'type':'active'}}])
        self.assertEqual(len(catalog.projects),1)
        project=next(iter(catalog.projects.values()))
        self.assertEqual(project.activity,200);self.assertEqual(len(project.conversations),2)
        self.assertEqual(catalog.filtered(project,'EXEC')[0].preview,'Prior task')
        self.assertFalse(catalog.filtered(project,'Other')[0].available)
    def test_invalid_sessions_ignored_and_refresh_deduplicates(self):
        catalog=Catalog();root=str(Path(tempfile.gettempdir())/'synthetic-navigation-repo');record={'id':SESSION,'cwd':root,'status':{'type':'notLoaded'}}
        catalog.ingest([record,record,{'id':'--last','cwd':'/synthetic/repo'},{'id':SESSION,'cwd':'relative'}])
        self.assertEqual(len(catalog.projects[root].conversations),1)


class DiscoveryProtocolTests(unittest.TestCase):
    def test_local_metadata_rpc_never_requires_account_or_resumes_thread(self):
        from codex_queue.app_server import AppServer
        with tempfile.TemporaryDirectory() as td:
            script=Path(td)/'mock-server.py'
            script.write_text('import json,sys\nfor line in sys.stdin:\n request=json.loads(line)\n if "id" not in request:continue\n method=request["method"]\n if method=="initialize":result={}\n elif method=="thread/list":result={"data":[],"nextCursor":None}\n elif method=="thread/read":result={"thread":{"id":request["params"]["threadId"]}}\n else:raise RuntimeError("Unexpected authentication or mutation RPC")\n print(json.dumps({"id":request["id"],"result":result}),flush=True)\n')
            real=subprocess.Popen
            def runner(command,*args,**kwargs):
                self.assertEqual(command[0],'mock-local-codex')
                return real([sys.executable,str(script)],*args,**kwargs)
            with patch('codex_queue.app_server.subprocess.Popen',side_effect=runner):
                client=AppServer('mock-local-codex')
                self.assertEqual(client.list_threads()['data'],[])
                self.assertEqual(client.read_thread(SESSION)['id'],SESSION)


class ModeTests(Fixture,unittest.TestCase):
    setUp=Fixture.setup;tearDown=Fixture.cleanup
    def test_default_foreground_and_lifetime_lock(self):
        self.assertEqual(control(self.state)['mode'],'foreground')
        with runner_lease(self.state,'foreground'):
            with self.assertRaises(WriterBusy):
                with runner_lease(self.state,'foreground'):pass
            with self.assertRaises(WriterBusy):
                with runner_lease(self.state,'os'):pass
    def test_inactive_scheduler_cannot_run_even_with_execution_permission(self):
        write_control(self.state,mode='os',scheduler_enabled=False)
        with self.assertRaises(QueueError):
            with runner_lease(self.state,'os'):pass
        with self.assertRaises(QueueError):
            with runner_lease(self.state,'foreground'):pass
        write_control(self.state,mode='os',scheduler_enabled=True)
        with runner_lease(self.state,'os'):pass
    def test_foreground_rejected_before_provider_in_os_mode(self):
        write_control(self.state,mode='os',scheduler_enabled=True)
        controller=Foreground(self.state,self.provider,{},simulation=True);controller.start();controller.join()
        self.assertEqual(self.provider.calls,0)
        events=[]
        while not controller.events.empty():events.append(controller.events.get())
        self.assertTrue(any(e['state']=='blocked' for e in events))
    def test_idle_foreground_refreshes_usage_without_job(self):
        controller=Foreground(self.state,self.provider,{},simulation=True);controller.start()
        limit=time.monotonic()+2
        while self.provider.calls==0 and time.monotonic()<limit:time.sleep(.01)
        controller.stop();controller.join()
        self.assertEqual(self.provider.calls,1)
    def test_crashed_process_releases_lease(self):
        script='from codex_queue.execution import runner_lease;import time,sys\nwith runner_lease(sys.argv[1],"foreground"):\n print("ready",flush=True);time.sleep(20)'
        process=subprocess.Popen([sys.executable,'-c',script,str(self.state)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'ready')
            with self.assertRaises(WriterBusy):
                with runner_lease(self.state,'foreground'):pass
            process.kill();process.wait(timeout=3)
            with runner_lease(self.state,'foreground'):pass
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdout.close()
    def test_scheduled_check_uses_worker_once_and_records_log(self):
        write_control(self.state,mode='os',scheduler_enabled=True)
        config=self.root/'external.json';config.write_text(json.dumps({'execution_enabled':True}))
        worker=Mock();worker.once.return_value={'state':'idle'};worker.stopping=False
        with patch('codex_queue.cli.Worker',return_value=worker),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--state',str(self.state),'--config',str(config),'scheduled-check']),0)
        worker.once.assert_called_once_with(None,False)
        self.assertIn('idle',(self.state/'scheduler/checks.jsonl').read_text())
    def test_disabled_scheduled_check_never_constructs_worker(self):
        with patch('codex_queue.cli.Worker') as worker,contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--state',str(self.state),'--config',str(self.root/'missing.json'),'scheduled-check']),1)
        worker.assert_not_called()


class SchedulerTests(Fixture,unittest.TestCase):
    setUp=Fixture.setup;tearDown=Fixture.cleanup
    def manager(self,platform='darwin'):
        self.calls=[]
        def run(args,**kwargs):
            self.calls.append(args)
            if args[:2]==['launchctl','print']:
                output='path = '+str(self.root/'home/Library/LaunchAgents'/(args[-1].split('/')[-1]+'.plist'))
            elif '--property=FragmentPath' in args:
                output='FragmentPath='+str(Path(os.environ['XDG_CONFIG_HOME'])/'systemd/user'/args[-2])
            else:output='ActiveState=inactive\nUnitFileState=disabled'
            return subprocess.CompletedProcess(args,0,output,'')
        return NativeScheduler(self.state,self.root/'config.json',platform,self.root/'home',run)
    def test_configure_is_inert_and_install_mac_disabled(self):
        manager=self.manager();manager.configure(60)
        self.assertEqual(self.calls,[]);self.assertFalse(manager.files()[0].exists())
        manager.install();payload=plistlib.loads(manager.files()[0].read_bytes())
        self.assertTrue(payload['Disabled']);self.assertFalse(payload['KeepAlive']);self.assertFalse(payload['RunAtLoad'])
        self.assertEqual(payload['ProgramArguments'][-1],'scheduled-check')
        self.assertFalse(control(self.state)['scheduler_enabled'])
        self.assertFalse(any('bootstrap' in c or 'enable' in c for c in self.calls))
    def test_enable_requires_gate_and_exclusive_lease(self):
        manager=self.manager();manager.configure(60);manager.install()
        with self.assertRaises(QueueError):manager.enable(False)
        with runner_lease(self.state,'foreground'):
            with self.assertRaises(WriterBusy):manager.enable(True)
        manager.enable(True);self.assertEqual(control(self.state)['mode'],'os')
        self.assertTrue(control(self.state)['scheduler_enabled'])
    def test_foreground_disables_native_first(self):
        manager=self.manager();manager.configure(60);manager.install();manager.enable(True)
        self.calls.clear();manager.foreground()
        self.assertIn('disable',self.calls[1]);self.assertIn('bootout',self.calls[2])
        self.assertEqual(control(self.state)['mode'],'foreground')
        self.assertFalse(control(self.state)['scheduler_enabled'])
    def test_changed_definition_never_mutated(self):
        manager=self.manager();manager.configure(60);manager.install()
        path=manager.files()[0];path.write_text('belongs to someone else');self.calls.clear()
        for action in (lambda:manager.enable(True),manager.uninstall,manager.install):
            with self.assertRaises(QueueError):action()
        self.assertEqual(path.read_text(),'belongs to someone else');self.assertEqual(self.calls,[])
    def test_unowned_existing_file_never_overwritten(self):
        manager=self.manager();manager.configure(60);path=manager.files()[0];path.parent.mkdir(parents=True);path.write_text('unowned')
        with self.assertRaises(QueueError):manager.install()
        self.assertEqual(path.read_text(),'unowned')
    def test_uninstall_touches_only_managed_file(self):
        manager=self.manager();manager.configure(60);manager.install()
        other=manager.files()[0].parent/'unrelated.plist';other.write_text('preserve')
        manager.uninstall();self.assertTrue(other.exists());self.assertFalse(manager.manifest.exists())
    def test_linux_generation_oneshot_timer_and_no_enable(self):
        manager=self.manager('linux')
        with patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'xdg')}):
            manager.configure(90);manager.install()
            service,timer=manager.files()
            self.assertIn('Type=oneshot',service.read_text());self.assertIn('scheduled-check',service.read_text())
            self.assertIn('OnUnitInactiveSec=90s',timer.read_text());self.assertNotIn('enable',str(self.calls))
            manager.enable(True);self.assertIn(['systemctl','--user','enable','--now',manager.name+'.timer'],self.calls)
    def test_windows_xml_disabled_ignore_new_no_password(self):
        manager=self.manager('win32');payload=manager.render(60)[0];root=ET.fromstring(payload)
        values={element.tag.split('}')[-1]:element.text for element in root.iter()}
        self.assertEqual(values['Enabled'],'false');self.assertEqual(values['LogonType'],'InteractiveToken')
        self.assertEqual(values['MultipleInstancesPolicy'],'IgnoreNew');self.assertIn('scheduled-check',values['Arguments'])
        self.assertNotIn('Password',values)
        with self.assertRaises(QueueError):manager.render(10)
    def test_windows_owned_task_lifecycle_with_mocked_native_commands(self):
        manager=self.manager('win32');native=None;calls=[]
        def run(args,**kwargs):
            nonlocal native
            calls.append(args)
            if '/Create' in args:
                self.assertNotIn('/F',args);native=ET.fromstring(Path(args[-1]).read_bytes())
            elif '/Query' in args:
                return subprocess.CompletedProcess(args,0,ET.tostring(native,encoding='unicode'),'')
            elif '/Change' in args:
                settings=next(e for e in native.iter() if e.tag.endswith('}Settings'))
                enabled=next(e for e in settings if e.tag.endswith('}Enabled'))
                enabled.text='true' if '/ENABLE' in args else 'false'
            elif '/Delete' in args:native=None
            return subprocess.CompletedProcess(args,0,'','')
        manager.run=run;manager.configure(60);manager.install()
        self.assertEqual(manager.status()['native'],'disabled')
        manager.enable(True);self.assertEqual(manager.status()['native'],'enabled')
        manager.foreground();self.assertEqual(manager.status()['native'],'disabled')
        manager.uninstall();self.assertIsNone(native)
        self.assertTrue(all(manager.name in args for args in calls))
    def test_failed_install_rolls_back_only_its_new_definitions(self):
        manager=self.manager('linux')
        with patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'xdg')}):
            manager.configure(60)
            manager.run=lambda args,**kwargs:subprocess.CompletedProcess(args,1,'','permission denied')
            with self.assertRaises(QueueError):manager.install()
            self.assertFalse(any(file.exists() for file in manager.files()))
            self.assertFalse(manager.manifest.exists())
    def test_native_errors_reported_without_enabling_state(self):
        manager=self.manager();manager.configure(60);manager.install()
        manager.run=lambda args,**kwargs:subprocess.CompletedProcess(args,1,'','permission denied')
        with self.assertRaises(QueueError):manager.enable(True)
        self.assertFalse(control(self.state)['scheduler_enabled'])
    def test_loaded_task_of_other_origin_is_never_changed(self):
        manager=self.manager();manager.configure(60);manager.install()
        calls=[]
        def foreign(args,**kwargs):
            calls.append(args);return subprocess.CompletedProcess(args,0,'path = /synthetic/foreign.plist','')
        manager.run=foreign
        with self.assertRaises(QueueError):manager.enable(True)
        self.assertFalse(any('enable' in command for command in calls))
        with self.assertRaises(QueueError):manager.foreground()
        self.assertFalse(any('disable' in command or 'bootout' in command for command in calls))
    def test_status_queries_actual_native_scheduler(self):
        manager=self.manager();manager.configure(60);manager.install();self.calls.clear()
        status=manager.status();self.assertEqual(status['native'],'loaded');self.assertEqual(self.calls[0][1],'print')


class NavigationTests(Fixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.setup()
    def tearDown(self):self.cleanup()
    async def wizard(self,app,pilot):
        app.push_screen(JobWizard());await pilot.pause();return app.screen
    async def test_new_prompt_review_then_queue_no_account_query(self):
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot)
            view.query_one('#manual-repo',Input).value=str(self.repo);await pilot.click('#add-repo');await pilot.pause()
            self.assertEqual(view.query_one('#conversations',OptionList).get_option_at_index(0).prompt,'Start New Conversation')
            view.query_one('#conversations',OptionList).focus();await pilot.press('enter');await pilot.pause()
            view.query_one('#prompt-editor',TextArea).load_text('A multiline\nprompt to review')
            await pilot.click('#next');await pilot.pause();await pilot.click('#next');await pilot.pause()
            self.assertEqual(self.store.rows(),[]);self.assertEqual(view.step,4)
            await pilot.click('#queue-job');await pilot.pause(.2)
            row=self.store.rows()[0];self.assertEqual(row['kind'],'prompt');self.assertIsNone(row['session_id']);self.assertIn('\n',row['prompt'])
            self.assertEqual(self.provider.calls,0)
    async def test_discovery_search_preview_and_busy_selection(self):
        provider=self.provider
        provider.list_threads=lambda cursor: {'data':[{'id':SESSION,'cwd':str(self.repo),'title':'Busy title','source':'cli','preview':'Preview from metadata','updatedAt':10,'status':{'type':'active'}}],'nextCursor':None}
        app=QueueApp(self.state,config=self.root/'missing.json',provider=provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot);await pilot.click('#discover');await pilot.pause()
            view.query_one('#projects',OptionList).focus();await pilot.press('enter');await pilot.pause()
            view.query_one('#search',Input).value='busy';await pilot.pause()
            options=view.query_one('#conversations',OptionList);options.focus();await pilot.press('down');await pilot.pause()
            self.assertIn('Preview from metadata',str(view.query_one('#conversation-preview').render()))
            await pilot.press('enter');await pilot.pause();self.assertEqual(view.step,1);self.assertEqual(self.store.rows(),[])
    async def test_missing_session_blocks_submission_without_fallback(self):
        provider=self.provider;provider.read_thread=lambda _: {}
        app=QueueApp(self.state,config=self.root/'missing.json',provider=provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot);view.query_one('#manual-repo',Input).value=str(self.repo)
            await pilot.click('#add-repo');await pilot.pause();view.query_one('#manual-session',Input).value=SESSION
            # Activate by keyboard so focus/scroll animations cannot move the mouse target.
            view.query_one('#use-session',Button).focus();await pilot.press('enter')
            await pilot.wait_for_scheduled_animations();self.assertEqual(view.step,2)
            view.query_one('#exclusive',Switch).value=True
            await pilot.click('#next');await pilot.pause();await pilot.click('#next');await pilot.pause();await pilot.click('#queue-job');await pilot.pause()
            self.assertEqual(self.store.rows(),[]);self.assertIn('session missing',str(view.query_one('#wizard-error').render()))
    async def test_file_load_back_edit_and_reset_schedule(self):
        prompt=self.root/'load.md';prompt.write_text('Loaded text\nSecond line')
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot);view.query_one('#manual-repo',Input).value=str(self.repo)
            await pilot.click('#add-repo');await pilot.pause()
            view.query_one('#conversations',OptionList).focus();await pilot.press('enter');await pilot.pause()
            view.query_one('#load-path',Input).value=str(prompt);await pilot.click('#load-file');await pilot.pause()
            self.assertIn('Second line',view.query_one('#prompt-editor',TextArea).text)
            await pilot.click('#next');await pilot.pause();await view.action_back();await pilot.pause()
            self.assertIn('Loaded text',view.query_one('#prompt-editor',TextArea).text)
            await pilot.click('#next');await pilot.pause();view.query_one('#schedule',Select).value='next-reset'
            await pilot.click('#next');await pilot.pause();await pilot.click('#queue-job');await pilot.pause()
            row=self.store.rows()[0];self.assertEqual(row['mode'],'next-reset');self.assertGreater(row['saved_reset'],time.time())
            self.assertEqual(self.provider.calls,1)
    async def test_wizard_narrow_resize_preserves_prompt(self):
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot);view.query_one('#manual-repo',Input).value=str(self.repo)
            await pilot.click('#add-repo');await pilot.pause();view.query_one('#conversations',OptionList).focus();await pilot.press('enter');await pilot.pause()
            view.query_one('#prompt-editor',TextArea).load_text('Keep this text')
            await pilot.resize_terminal(55,22);await pilot.pause()
            self.assertEqual(view.query_one('#prompt-editor',TextArea).text,'Keep this text')
            self.assertEqual(self.store.rows(),[])
    async def test_original_session_submission_preserves_identifier(self):
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot);view.query_one('#manual-repo',Input).value=str(self.repo)
            await pilot.click('#add-repo');await pilot.pause();view.query_one('#manual-session',Input).value=SESSION
            view.query_one('#use-session',Button).focus();await pilot.press('enter')
            await pilot.wait_for_scheduled_animations();self.assertEqual(view.step,2)
            view.query_one('#exclusive',Switch).value=True
            view.query_one('#prompt-editor',TextArea).load_text('Continue the prior task')
            await pilot.click('#next');await pilot.pause();await pilot.click('#next');await pilot.pause();await pilot.click('#queue-job');await pilot.pause()
            row=self.store.rows()[0];self.assertEqual(row['session_id'],SESSION);self.assertEqual(row['kind'],'session')
            self.assertEqual(row['session_cwd'],str(self.repo));self.assertEqual(self.provider.calls,0)
    async def test_mode_switch_stops_watcher_and_does_not_enable_native_task(self):
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        app.scheduler_factory=lambda state,config: NativeScheduler(state,config,'darwin',self.root/'home',lambda args,**kw:subprocess.CompletedProcess(args,0,'',''))
        app.controller=Foreground(self.state,self.provider,{},simulation=True);app.controller.start()
        async with app.run_test(size=(110,48)) as pilot:
            app.push_screen(SchedulerScreen());await pilot.pause()
            app.screen.query_one('#execution-mode',Select).value='os'
            await pilot.click('#apply-mode');await pilot.pause()
            self.assertFalse(app.controller.thread.is_alive());self.assertEqual(control(self.state)['mode'],'os')
            self.assertFalse(control(self.state)['scheduler_enabled']);self.assertFalse(app.screen.manager.manifest.exists())
            app.screen.query_one('#execution-mode',Select).value='foreground';await pilot.click('#apply-mode');await pilot.pause()
            self.assertEqual(control(self.state)['mode'],'foreground');self.assertFalse(app.controller.thread.is_alive())
    async def test_selected_conversation_button_needs_no_manual_id(self):
        child=self.repo/'sub';child.mkdir()
        self.provider.list_threads=lambda cursor:{'data':[{'id':SESSION,'cwd':str(child),'projectPath':str(self.repo),'name':'Existing conversation','source':'cli','status':{'type':'notLoaded'}}],'nextCursor':None}
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot);await pilot.click('#discover');await pilot.pause()
            view.query_one('#projects',OptionList).focus();await pilot.press('enter');await pilot.pause()
            view.query_one('#conversations',OptionList).highlighted=1;await pilot.pause()
            self.assertEqual(view.query_one('#manual-session',Input).value,'')
            await pilot.click('#use-selected');await pilot.pause()
            self.assertEqual(view.step,2);self.assertEqual(view.session_id,SESSION);self.assertEqual(view.cwd,'sub')
            self.assertEqual(self.store.rows(),[])
    async def test_manual_id_blank_help_and_pasted_whitespace(self):
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        async with app.run_test(size=(110,48)) as pilot:
            view=await self.wizard(app,pilot);view.query_one('#manual-repo',Input).value=str(self.repo)
            await pilot.click('#add-repo');await pilot.pause()
            button=view.query_one('#use-session',Button)
            button.active_effect_duration=0
            button.focus();await pilot.press('enter');await pilot.pause()
            self.assertEqual(view.step,1);self.assertIn('selected conversation',str(view.query_one('#wizard-error').render()))
            view.query_one('#manual-session',Input).value=' '+SESSION+' '
            button.focus();await pilot.press('enter');await pilot.wait_for_scheduled_animations()
            self.assertEqual(view.session_id,SESSION);self.assertEqual(view.step,2)
    async def test_settings_mode_navigation_and_configure_do_not_enable(self):
        app=QueueApp(self.state,config=self.root/'missing.json',provider=self.provider)
        app.scheduler_factory=lambda state,config: NativeScheduler(state,config,'darwin',self.root/'home',lambda args,**kw:subprocess.CompletedProcess(args,0,'',''))
        async with app.run_test(size=(110,48)) as pilot:
            app.push_screen(Settings());await pilot.pause();await pilot.click('#execution-settings');await pilot.pause()
            self.assertIsInstance(app.screen,SchedulerScreen)
            await pilot.click('#scheduler-manage');await pilot.pause()
            await pilot.click('#configure-scheduler');await pilot.pause()
            self.assertFalse(app.screen.manager.manifest.exists());self.assertFalse(control(self.state)['scheduler_enabled']);self.assertEqual(self.provider.calls,0)
