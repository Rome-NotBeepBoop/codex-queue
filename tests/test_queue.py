import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from codex_queue.app_server import AppServer
from codex_queue.cli import main
from codex_queue.store import Store, QueueError, git, lock
from codex_queue.usage import UsageError, parse
from codex_queue.worker import Worker


def response(now, used=20, weekly=10):
    return {"rateLimitsByLimitId": {"codex": {"limitId": "codex", "primary": {
        "usedPercent": used, "windowDurationMins": 300, "resetsAt": int(now) + 18000},
        "secondary": {"usedPercent": weekly, "windowDurationMins": 10080, "resetsAt": int(now) + 600000}}}}


class Provider:
    live = False

    def __init__(self, used=0, weekly=0, error=False):
        self.used, self.weekly, self.error, self.calls = used, weekly, error, 0

    def read(self):
        self.calls += 1
        if self.error:
            raise UsageError("authentication failure")
        now = time.time()
        return parse(response(now, self.used, self.weekly), now, now)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.data = response(self.now)

    def parse(self, data=None, observed=None, now=None):
        return parse(data or self.data, observed or self.now, now or self.now)

    def test_available(self):
        self.assertTrue(self.parse().eligible(self.now)[0])

    def test_swapped_windows(self):
        bucket = self.data['rateLimitsByLimitId']['codex']
        bucket['primary'], bucket['secondary'] = bucket['secondary'], bucket['primary']
        self.assertEqual(self.parse().five.minutes, 300)

    def test_weekly_exhaustion(self):
        self.assertFalse(self.parse(response(self.now, weekly=100)).eligible(self.now)[0])

    def test_five_exhaustion(self):
        self.assertFalse(self.parse(response(self.now, used=100)).eligible(self.now)[0])

    def test_next_reset_waits_despite_available_quota(self):
        u = self.parse()
        self.assertFalse(u.eligible(self.now, u.five.reset)[0])

    def test_reset_needs_advance(self):
        old_reset = self.now + 18000
        # At the old boundary the server still reporting it is rejected.
        with self.assertRaises(UsageError):
            parse(self.data, old_reset, old_reset)
        fresh = parse(response(old_reset), old_reset, old_reset)
        self.assertTrue(fresh.eligible(old_reset, old_reset)[0])

    def test_after_observed_reset(self):
        old = self.now - 1
        self.assertTrue(self.parse().eligible(self.now, old)[0])

    def test_early_credit_reset_does_not_bypass_saved_time(self):
        self.assertFalse(self.parse().eligible(self.now, self.now + 10)[0])

    def test_stale(self):
        with self.assertRaises(UsageError):
            self.parse(observed=self.now - 121)

    def test_future_observation(self):
        with self.assertRaises(UsageError):
            self.parse(observed=self.now + 1)

    def test_missing_bucket(self):
        with self.assertRaises(UsageError):
            parse(self.data, self.now, self.now, 'other')

    def test_legacy(self):
        self.assertTrue(self.parse({'rateLimits': self.data['rateLimitsByLimitId']['codex']}).eligible(self.now)[0])

    def test_modern_map_does_not_fall_back(self):
        with self.assertRaises(UsageError):
            self.parse({'rateLimitsByLimitId': {}, 'rateLimits': self.data['rateLimitsByLimitId']['codex']})

    def test_malformed_fields(self):
        for key, value in [('usedPercent', True), ('usedPercent', -1), ('usedPercent', float('nan')),
                           ('resetsAt', self.now - 1), ('resetsAt', self.now + 99999999),
                           ('windowDurationMins', 15), ('resetsAt', 'tomorrow')]:
            data = copy.deepcopy(self.data)
            data['rateLimitsByLimitId']['codex']['primary'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(UsageError):
                self.parse(data)

    def test_missing_weekly(self):
        self.data['rateLimitsByLimitId']['codex']['secondary'] = None
        with self.assertRaises(UsageError):
            self.parse()

    def test_reached_type(self):
        self.data['rateLimitsByLimitId']['codex']['rateLimitReachedType'] = 'weekly'
        self.assertFalse(self.parse().eligible(self.now)[0])

    def test_credits_ignored(self):
        self.data['rateLimitResetCredits'] = {'remaining': 1000}
        self.assertFalse(self.parse(response(self.now, used=100)).eligible(self.now)[0])


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.real_popen = subprocess.Popen
        if os.name == 'nt':
            def portable_runner(command, *args, **kwargs):
                if Path(command[0]).name == 'mock-codex':
                    command = [sys.executable, *command]
                return self.real_popen(command, *args, **kwargs)
            self.portable_patch = patch('subprocess.Popen', side_effect=portable_runner)
            self.portable_patch.start()
            self.addCleanup(self.portable_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.name', 'Queue Test'), ('config', 'user.email', 'test@example.invalid')]:
            git(self.repo, *args)
        (self.repo / 'README.md').write_text('test repository\n')
        git(self.repo, 'add', 'README.md')
        git(self.repo, 'commit', '-m', 'fixture')
        self.state = self.root / 'state'
        self.store = Store(self.state)
        self.provider = Provider()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, mode='immediate', reset=None, key=None, cwd='.'):
        return self.store.add('A harmless prompt', self.repo, cwd, mode, reset, key)

    def worker(self):
        return Worker(self.store, self.provider, simulation=True)

    def test_persistence(self):
        job = self.add()
        self.store.close()
        self.store = Store(self.state)
        self.assertEqual(self.store.get(job)['status'], 'queued')

    def test_dedup(self):
        self.assertEqual(self.add(), self.add())
        self.assertEqual(len(self.store.rows()), 1)

    def test_manual_is_not_automatic(self):
        self.add('manual')
        self.assertEqual(self.worker().once()['state'], 'idle')
        self.assertEqual(self.provider.calls, 0)

    def test_manual_explicit_run(self):
        self.assertEqual(self.worker().once(self.add('manual'))['state'], 'completed')

    def test_simulation_isolated(self):
        job = self.add()
        old = git(self.repo, 'rev-parse', 'HEAD')
        result = self.worker().once()
        tree = Path(result['worktree'])
        self.assertTrue((tree / 'queue-simulation.txt').exists())
        self.assertFalse((self.repo / 'queue-simulation.txt').exists())
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), old)
        self.assertTrue(git(tree, 'branch', '--show-current').startswith('codex/queue-'))
        self.assertEqual(self.store.get(job)['status'], 'completed')
        self.assertEqual(self.provider.calls, 2)
        self.assertEqual(self.worker().once()['state'], 'idle')

    def test_two_unrelated_repositories(self):
        other = self.root / 'other-repository'
        other.mkdir()
        for args in [('init',), ('config', 'user.name', 'Queue Test'),
                     ('config', 'user.email', 'test@example.invalid')]:
            git(other, *args)
        (other / 'notes.txt').write_text('independent project\n')
        git(other, 'add', '.')
        git(other, 'commit', '-m', 'independent fixture')
        jobs = [self.add(), self.store.add('Another prompt', other, mode='immediate')]
        for job in jobs:
            result = self.worker().once(job)
            self.assertEqual(result['state'], 'completed')
            self.assertTrue((Path(result['worktree']) / 'queue-simulation.txt').exists())
            self.assertFalse((Path(self.store.get(job)['repo']) / 'queue-simulation.txt').exists())
            self.store.backoff(time.time(), reset=True)
        self.assertNotEqual(self.store.get(jobs[0])['repo'], self.store.get(jobs[1])['repo'])

    def test_state_in_unrelated_checkout_rejected(self):
        # A state folder in another repo would create a nested worktree.
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        git(unrelated, 'init')
        nested = Store(unrelated / 'queue-state')
        try:
            job = nested.add('prompt', self.repo, mode='immediate')
            result = Worker(nested, self.provider, simulation=True).once()
            self.assertEqual(result['state'], 'waiting')
            self.assertEqual(nested.get(job)['status'], 'paused')
            self.assertFalse((nested.state / 'worktrees').exists())
        finally:
            nested.close()

    def test_environment_state_and_config(self):
        output = io.StringIO()
        with patch.dict(os.environ, {
            'CODEX_QUEUE_STATE': str(self.state),
            'CODEX_QUEUE_CONFIG': str(self.root / 'generic.json'),
        }), contextlib.redirect_stdout(output):
            (self.root / 'generic.json').write_text('{"execution_enabled": false}')
            self.assertEqual(main(['doctor']), 0)
        self.assertEqual(json.loads(output.getvalue())['state'], str(self.state.resolve()))

    def test_environment_state_without_config(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'CODEX_QUEUE_STATE': str(self.state)}, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(main(['doctor']), 0)
        self.assertEqual(json.loads(output.getvalue())['state'], str(self.state.resolve()))

    def test_dry_run(self):
        job = self.add()
        self.assertEqual(self.worker().once(dry_run=True)['state'], 'dry-run')
        self.assertFalse((self.state / 'worktrees').exists())
        self.assertEqual(self.store.get(job)['attempts'], 0)

    def test_weekly_blocks_writes(self):
        self.add()
        self.provider.weekly = 100
        self.assertEqual(self.worker().once()['state'], 'waiting')
        self.assertFalse((self.state / 'worktrees').exists())

    def test_auth_failure(self):
        self.add()
        self.provider.error = True
        self.assertEqual(self.worker().once()['state'], 'blocked')
        self.assertGreater(self.store.next_poll(), time.time())

    def test_backoff_persists(self):
        self.add()
        self.provider.error = True
        self.worker().once()
        calls = self.provider.calls
        self.assertEqual(self.worker().once()['reason'], 'poll backoff')
        self.assertEqual(calls, self.provider.calls)

    def test_reboot_pauses_uncertain_attempt(self):
        job = self.add()
        self.store.claim(job)
        self.assertEqual(self.worker().once()['state'], 'idle')
        self.assertEqual(self.store.get(job)['status'], 'paused')
        with self.assertRaises(QueueError):
            self.store.transition(job, 'resume')
        self.store.transition(job, 'retry')
        self.assertEqual(self.worker().once()['state'], 'completed')
        self.assertEqual(self.store.get(job)['attempts'], 2)

    def test_single_writer(self):
        self.add()
        with lock(self.state / 'worker.lock'), self.assertRaises(QueueError):
            self.worker().once()

    def test_repository_lock(self):
        self.add()
        with lock(self.repo / '.git' / 'codex-queue.lock'):
            self.assertEqual(self.worker().once()['state'], 'waiting')

    def test_atomic_claim(self):
        job = self.add()
        self.store.claim(job)
        with self.assertRaises(QueueError):
            self.store.claim(job)

    def test_pause_resume_cancel(self):
        job = self.add()
        self.store.transition(job, 'pause')
        self.assertEqual(self.worker().once()['state'], 'idle')
        self.store.transition(job, 'resume')
        self.store.transition(job, 'cancel')
        self.assertEqual(self.store.get(job)['status'], 'cancelled')

    def test_running_cancel_requested(self):
        job = self.add()
        self.store.claim(job)
        self.store.transition(job, 'cancel')
        self.assertEqual(self.store.get(job)['requested'], 'cancel')

    def test_failed_runner_and_retry(self):
        job = self.add()
        real_popen = subprocess.Popen
        def fail_runner(command, *args, **kwargs):
            if command[0] in ('python3', sys.executable):
                raise OSError('fake failure')
            return real_popen(command, *args, **kwargs)
        with patch('codex_queue.worker.subprocess.Popen', side_effect=fail_runner):
            self.assertEqual(self.worker().once()['state'], 'failed')
        self.assertEqual(self.store.get(job)['status'], 'failed')
        self.store.transition(job, 'retry')
        self.store.backoff(time.time(), reset=True)
        self.assertEqual(self.worker().once()['state'], 'completed')

    def test_dirty_source_rejected(self):
        self.add()
        (self.repo / 'untracked').write_text('keep me')
        self.assertEqual(self.worker().once()['state'], 'waiting')
        self.assertEqual(self.store.rows()[0]['status'], 'paused')
        self.assertEqual((self.repo / 'untracked').read_text(), 'keep me')

    def test_escape_rejected(self):
        for cwd in ('..', '/tmp'):
            with self.assertRaises(QueueError):
                self.add(cwd=cwd)

    def test_snapshot_reset_preserved(self):
        reset = int(time.time()) + 18000
        job = self.add('next-reset', reset)
        self.assertEqual(self.worker().once()['state'], 'waiting')
        self.assertEqual(self.store.get(job)['saved_reset'], reset)

    def test_second_usage_check_blocks(self):
        original = self.provider.read
        def changed():
            if self.provider.calls:
                self.provider.weekly = 100
            return original()
        self.provider.read = changed
        job = self.add()
        result = self.worker().once()
        self.assertEqual(result['state'], 'paused')
        row = self.store.db.execute('SELECT worktree FROM attempts WHERE job=?', (job,)).fetchone()
        self.assertFalse((Path(row[0]) / 'queue-simulation.txt').exists())

    def test_fixture_cannot_authorize_real_codex(self):
        self.add()
        with self.assertRaises(QueueError):
            Worker(self.store, self.provider).once()

    def test_cli_disabled(self):
        job = self.add()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--state', str(self.state), 'run', job]), 1)

    def test_next_reset_worker_boundary(self):
        initial = int(time.time())
        reset = initial + 18000
        job = self.add('next-reset', reset)
        self.assertEqual(self.worker().once()['state'], 'waiting')
        with patch('time.time', return_value=reset + 1):
            self.assertEqual(self.worker().once()['state'], 'completed')
        self.assertEqual(self.store.get(job)['saved_reset'], reset)

    def test_nonzero_codex_process(self):
        job = self.add()
        mock = self.root / 'mock-codex'
        mock.write_text('#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\nprint("mock runner failure")\nsys.exit(37)\n')
        mock.chmod(0o700)
        self.provider.live = True
        result = Worker(self.store, self.provider, str(mock)).once()
        self.assertEqual(result['state'], 'failed')
        self.assertIn('37', self.store.get(job)['reason'])

    def test_timeout_pauses_process(self):
        job = self.add()
        mock = self.root / 'mock-codex'
        mock.write_text('#!/usr/bin/env python3\nimport sys,time\nsys.stdin.read()\ntime.sleep(10)\n')
        mock.chmod(0o700)
        self.provider.live = True
        result = Worker(self.store, self.provider, str(mock), timeout=0.1).once()
        self.assertEqual(result['state'], 'paused')
        self.assertTrue(Path(result['worktree']).exists())
        self.assertIn('interrupted', self.store.get(job)['reason'])

    def test_cancel_live_mock_process(self):
        job = self.add()
        mock = self.root / 'mock-codex'
        mock.write_text('#!/usr/bin/env python3\nimport sys,time\nfrom pathlib import Path\nsys.stdin.read()\nPath("started.txt").write_text("started")\ntime.sleep(10)\n')
        mock.chmod(0o700)
        self.provider.live = True
        errors = []
        def cancel_when_started():
            other = Store(self.state)
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    trees = list((self.state / 'worktrees').glob('*/started.txt'))
                    if trees:
                        other.transition(job, 'cancel')
                        return
                    time.sleep(0.02)
                errors.append('mock child never started')
            finally:
                other.close()
        controller = threading.Thread(target=cancel_when_started)
        controller.start()
        result = Worker(self.store, self.provider, str(mock), timeout=10).once()
        controller.join(timeout=9)
        self.assertFalse(errors)
        self.assertEqual(result['state'], 'cancelled')
        self.assertTrue(Path(result['worktree']).exists())

    @unittest.skipIf(os.name == "nt", "POSIX permission bits; Windows uses user ACLs")
    def test_private_state(self):
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state / 'queue.sqlite').stat().st_mode & 0o777, 0o600)


class AppServerTests(unittest.TestCase):
    def setUp(self):
        if os.name == 'nt':
            real = subprocess.Popen
            def portable_runner(command, *args, **kwargs):
                if Path(command[0]).name == 'mock-codex': command = [sys.executable, *command]
                return real(command, *args, **kwargs)
            portable = patch('subprocess.Popen', side_effect=portable_runner)
            portable.start(); self.addCleanup(portable.stop)

    def test_auth_failure_no_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'mock-codex'
            path.write_text('''#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    r = json.loads(line)
    assert r['method'] in ('initialize', 'initialized', 'account/read', 'account/rateLimits/read')
    if 'id' in r:
        print(json.dumps({'id': r['id'], 'result': {'account': None}}), flush=True)
''')
            path.chmod(0o700)
            with self.assertRaises(UsageError):
                AppServer(str(path), timeout=5).read()

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'mock-codex'
            path.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n')
            path.chmod(0o700)
            with self.assertRaises(UsageError):
                AppServer(str(path), timeout=0.1).read()

    def test_protocol_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'mock-codex'
            path.write_text('''#!/usr/bin/env python3
import json, sys, time
for line in sys.stdin:
    r = json.loads(line)
    assert r['method'] in ('initialize', 'initialized', 'account/read', 'account/rateLimits/read')
    if 'id' not in r: continue
    result = {}
    if r['method'] == 'account/read': result = {'account': {'type': 'chatgpt'}}
    if r['method'] == 'account/rateLimits/read':
        now = int(time.time())
        result = {'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 0, 'windowDurationMins': 300, 'resetsAt': now+100}, 'secondary': {'usedPercent': 0, 'windowDurationMins': 10080, 'resetsAt': now+500}}}
    print(json.dumps({'id': r['id'], 'result': result}), flush=True)
''')
            path.chmod(0o700)
            self.assertTrue(AppServer(str(path), timeout=5).read().eligible(time.time())[0])


if __name__ == '__main__':
    unittest.main()
