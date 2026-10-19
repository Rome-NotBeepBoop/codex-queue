import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from codex_queue.cli import main
from codex_queue.foreground import Foreground
from codex_queue.sessions import identifier, command, validate_metadata
from codex_queue.store import Store, QueueError, git, lock
from codex_queue.usage import parse, UsageError
from codex_queue.worker import Worker
from codex_queue.tui import QueueApp, Form, Settings, QueueView, Execution
from codex_queue.navigation import JobWizard
from textual.widgets import Input, Select, OptionList, TextArea, Switch

SESSION='11111111-1111-4111-8111-111111111111'
class Provider:
    live=False
    def __init__(self, weekly=0, error=False): self.weekly=weekly;self.error=error;self.calls=0
    def read(self):
        self.calls+=1
        if self.error: raise UsageError('Authentication required')
        now=time.time()
        window=lambda minutes,used: {'usedPercent':used,'windowDurationMins':minutes,'resetsAt':int(now)+minutes*60}
        return parse({'rateLimits':{'limitId':'codex','primary':window(300,20),'secondary':window(10080,self.weekly)}},now,now)
    def read_thread(self, value): return {'id':value,'cwd':str(self.cwd),'status':{'type':'idle'}}

class Fixture:
    def setup(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.repo=self.root/'repo';self.repo.mkdir()
        for args in [('init',),('config','user.name','Queue Tests'),('config','user.email','test@example.invalid')]:git(self.repo,*args)
        (self.repo/'README.md').write_text('fixture');git(self.repo,'add','.');git(self.repo,'commit','-m','fixture')
        self.state=self.root/'state';self.store=Store(self.state);self.provider=Provider();self.provider.cwd=self.repo
    def cleanup(self):self.store.close();self.temp.cleanup()

class SessionTests(Fixture,unittest.TestCase):
    setUp=Fixture.setup;tearDown=Fixture.cleanup
    def add(self):return self.store.add_session(SESSION,self.repo,ownership=True,mode='immediate')
    def test_identifier_rejects_names_flags_and_paths(self):
        for value in ('--last','name','../../session','',None):
            with self.assertRaises(QueueError):identifier(value)
        self.assertEqual(identifier(SESSION),SESSION)
    def test_resume_command_preserves_context_and_sandbox(self):
        args=command('codex',SESSION,'final.txt')
        self.assertIn('resume',args);self.assertIn(SESSION,args);self.assertIn('workspace-write',args)
        self.assertNotIn('--last',args);self.assertNotIn('--worktree',args)
    def test_missing_active_unknown_or_wrong_checkout_blocked(self):
        job=self.store.get(self.add())
        for data in ({},{'id':SESSION,'cwd':str(self.repo),'status':{'type':'active'}},
                     {'id':SESSION,'cwd':str(self.repo),'status':{}},
                     {'id':SESSION,'cwd':'/wrong','status':{'type':'idle'}}):
            with self.assertRaises(QueueError):validate_metadata(data,job)
    def test_requires_explicit_ownership(self):
        with self.assertRaises(QueueError):self.store.add_session(SESSION,self.repo)
        self.assertEqual(self.store.rows(),[])
    def test_no_duplicate_session_and_persistence(self):
        job=self.add()
        with self.assertRaises(QueueError):self.add()
        self.store.close();self.store=Store(self.state)
        self.assertEqual(self.store.get(job)['kind'],'session')
    def test_simulated_resume_preserves_dirty_original_checkout(self):
        job=self.add();(self.repo/'unfinished.txt').write_text('preserve')
        result=Worker(self.store,self.provider,simulation=True).once()
        self.assertEqual(result['state'],'completed');self.assertEqual(Path(result['worktree']),self.repo)
        self.assertEqual((self.repo/'unfinished.txt').read_text(),'preserve')
        self.assertFalse((self.state/'worktrees').exists())
    def test_missing_session_never_falls_back_to_new_job(self):
        self.add();self.provider.live=True
        self.provider.read_thread=lambda _: {}
        with patch('codex_queue.sessions.check_compatibility'):
            result=Worker(self.store,self.provider).once()
        self.assertEqual(result['state'],'waiting');self.assertEqual(self.store.rows()[0]['status'],'paused')
        self.assertEqual(self.store.rows()[0]['attempts'],0)
    def test_exact_resume_with_mocked_runner(self):
        job=self.add();self.provider.live=True
        recorded=[]
        real=subprocess.Popen
        def mock(command,*args,**kwargs):
            if command[0]=='mock-codex':
                recorded.append(command)
                return real([sys.executable,'-c','import sys;sys.stdin.read();print("mock resume")'],*args,**kwargs)
            return real(command,*args,**kwargs)
        with patch('codex_queue.sessions.check_compatibility'),patch('codex_queue.worker.subprocess.Popen',side_effect=mock):
            result=Worker(self.store,self.provider,'mock-codex').once()
        self.assertEqual(result['state'],'completed');self.assertEqual(len(recorded),1);self.assertIn(SESSION,recorded[0])
    def test_runner_cannot_report_a_replacement_session(self):
        job=self.add();self.provider.live=True
        real=subprocess.Popen
        def mock(command,*args,**kwargs):
            if command[0]=='mock-codex':
                script='import sys,json;sys.stdin.read();print(json.dumps({"type":"thread.started","thread_id":"22222222-2222-4222-8222-222222222222"}))'
                return real([sys.executable,'-c',script],*args,**kwargs)
            return real(command,*args,**kwargs)
        with patch('codex_queue.sessions.check_compatibility'),patch('codex_queue.worker.subprocess.Popen',side_effect=mock):
            result=Worker(self.store,self.provider,'mock-codex').once()
        self.assertEqual(result['state'],'failed')
        self.assertIn('different session',self.store.get(job)['reason'])
        attempt=self.store.db.execute('SELECT session_id,branch FROM attempts WHERE job=?',(job,)).fetchone()
        self.assertEqual(attempt['session_id'],SESSION)
        self.assertEqual(attempt['branch'],git(self.repo,'branch','--show-current'))
    def test_legacy_resume_still_unpauses(self):
        job=self.store.add('prompt',self.repo);self.store.transition(job,'pause')
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['--state',str(self.state),'resume',job]),0)
        self.assertEqual(self.store.get(job)['status'],'queued')
    def test_stop_before_cycle_prevents_even_usage_read(self):
        self.store.add('prompt',self.repo,mode='immediate');worker=Worker(self.store,self.provider,simulation=True);worker.stop()
        self.assertEqual(worker.once()['state'],'stopped');self.assertEqual(self.provider.calls,0)
    def test_observer_streams_output_and_completion(self):
        self.store.add('prompt',self.repo,mode='immediate');events=[]
        result=Worker(self.store,self.provider,simulation=True,observer=events.append).once()
        self.assertEqual(result['state'],'completed');self.assertTrue(any(e['state']=='output' for e in events))
    def test_foreground_requires_gate_and_rejects_fixture_real_execution(self):
        for gate in (False,True):
            with self.assertRaises(QueueError):Foreground(self.state,self.provider,{'execution_enabled':gate})
    def test_foreground_stop_and_restart_recovery(self):
        job=self.store.add('prompt',self.repo,mode='immediate');self.store.claim(job)
        controller=Foreground(self.state,self.provider,{},simulation=True);controller.start()
        deadline=time.monotonic()+3
        while self.store.get(job)['status']=='running' and time.monotonic()<deadline:time.sleep(.02)
        controller.stop();controller.join();self.assertFalse(controller.thread.is_alive())
        self.assertEqual(self.store.get(job)['status'],'paused')
    def test_weekly_exhaustion_never_runs(self):
        self.store.add('prompt',self.repo,mode='immediate');self.provider.weekly=100
        result=Worker(self.store,self.provider,simulation=True).once()
        self.assertIn('weekly',result['reason']);self.assertFalse((self.state/'worktrees').exists())

class TUITests(Fixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.setup()
    def tearDown(self):self.cleanup()
    async def test_startup_missing_usage_and_resize_without_provider(self):
        app=QueueApp(self.state,provider=self.provider)
        async with app.run_test(size=(100,35)) as pilot:
            await pilot.pause();self.assertIn('Unknown',str(app.query_one('#five-value').render()))
            await pilot.resize_terminal(55,22);await pilot.pause();self.assertEqual(app.size.width,55)
            self.assertEqual(self.provider.calls,0)
    async def test_menu_keyboard_add_form_back(self):
        app=QueueApp(self.state,provider=self.provider)
        async with app.run_test() as pilot:
            await pilot.press('right','enter');await pilot.pause();self.assertIsInstance(app.screen,JobWizard)
            await pilot.press('escape');await pilot.pause();self.assertEqual(len(app.screen_stack),1)
    async def test_prompt_creation_persists(self):
        prompt=self.root/'prompt.md';prompt.write_text('A harmless prompt')
        app=QueueApp(self.state,provider=self.provider)
        async with app.run_test(size=(100,40)) as pilot:
            app.push_screen(Form());await pilot.pause()
            app.screen.query_one('#repo',Input).value=str(self.repo)
            app.screen.query_one('#prompt',Input).value=str(prompt)
            await pilot.click('#submit');await pilot.pause(.3)
            self.assertEqual(len(app.store.rows()),1);self.assertEqual(self.provider.calls,0)
        self.assertEqual(len(self.store.rows()),1)
    async def test_form_invalid_path_shows_error(self):
        app=QueueApp(self.state,provider=self.provider)
        async with app.run_test(size=(100,40)) as pilot:
            app.push_screen(Form());await pilot.pause();await pilot.click('#submit');await pilot.pause()
            self.assertEqual(self.store.rows(),[]);self.assertTrue(str(app.screen.query_one('#error').render()))
    async def test_session_form_and_settings_navigation(self):
        app=QueueApp(self.state,provider=self.provider)
        async with app.run_test(size=(100,40)) as pilot:
            app.push_screen(Form(True));await pilot.pause()
            self.assertIsInstance(app.screen,Form);self.assertTrue(app.screen.session)
            await pilot.press('escape','5');await pilot.pause()
            self.assertIsInstance(app.screen,Settings);self.assertFalse(app.screen.query_one('#permission',Switch).value)
    async def test_watcher_gate_stays_closed(self):
        app=QueueApp(self.state,provider=self.provider)
        async with app.run_test() as pilot:
            await pilot.press('4');await pilot.pause()
            self.assertIsNone(app.controller);self.assertEqual(self.provider.calls,0)
            app.screen.query_one('#start-watch').focus();await pilot.press('enter');await pilot.pause()
            self.assertIsNone(app.controller);self.assertEqual(self.provider.calls,0)
    async def test_simulated_watcher_graceful_exit(self):
        now=time.time();fixture=self.root/'usage.json';window=lambda m:{'usedPercent':0,'windowDurationMins':m,'resetsAt':int(now)+m*60}
        fixture.write_text(json.dumps({'observed_at':now,'response':{'rateLimits':{'limitId':'codex','primary':window(300),'secondary':window(10080)}}}))
        self.store.add('prompt',self.repo,mode='immediate')
        app=QueueApp(self.state,fixture=str(fixture))
        async with app.run_test() as pilot:
            await pilot.press('4');await pilot.pause();self.assertIsInstance(app.screen,Execution)
            app.screen.query_one('#start-watch').focus();await pilot.press('enter');await pilot.pause(.5)
            await pilot.press('ctrl+c');await pilot.pause()
        self.assertFalse(app.controller.thread.is_alive())
        self.assertEqual(self.store.rows()[0]['status'],'completed')
