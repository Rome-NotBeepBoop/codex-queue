import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_queue.cli import main
from codex_queue.cli_mode import render_cli_overview, run_cli_mode, CliController
from codex_queue.cli_style import Style, Glyphs, should_color, strip_ansi
from codex_queue.projects import set_mock_metadata, sanitize_chat_title, _path_aliases, resolve_chat_title, Catalog, Project
from codex_queue.settings import DEFAULTS, load, save, is_cli_mode, toggle_cli_mode
from codex_queue.store import Store, QueueError, git
from codex_queue.tui import QueueApp, Settings
from codex_queue.usage import Usage, Window, UsageError
from textual.widgets import Select, Switch


class MockUsageProvider:
    live = False

    def __init__(self, five_used=100, weekly_used=37, error=None):
        self.five_used = five_used
        self.weekly_used = weekly_used
        self.error = error
        self.calls = 0

    def read(self):
        self.calls += 1
        if self.error:
            raise UsageError(self.error)
        now = time.time()
        return Usage(
            Window(self.five_used, 300, int(now) + 7200),
            Window(self.weekly_used, 10080, int(now) + 86400),
            now
        )


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp.name) / "config.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_settings_defaults(self):
        self.assertIn("interface_mode", DEFAULTS)
        self.assertIn("cli_mode", DEFAULTS)
        self.assertEqual(DEFAULTS["interface_mode"], "tui")
        self.assertFalse(DEFAULTS["cli_mode"])

    def test_is_cli_mode(self):
        self.assertFalse(is_cli_mode({}))
        self.assertFalse(is_cli_mode({"interface_mode": "tui"}))
        self.assertFalse(is_cli_mode({"cli_mode": False}))
        self.assertTrue(is_cli_mode({"interface_mode": "cli"}))
        self.assertTrue(is_cli_mode({"cli_mode": True}))
        self.assertTrue(is_cli_mode({"cli_mode": "cli"}))

    def test_toggle_cli_mode(self):
        # Initial config should default to TUI
        cfg = toggle_cli_mode(self.config_path)
        self.assertEqual(cfg["interface_mode"], "cli")
        self.assertTrue(cfg["cli_mode"])

        # Toggling again should return to TUI
        cfg2 = toggle_cli_mode(self.config_path)
        self.assertEqual(cfg2["interface_mode"], "tui")
        self.assertFalse(cfg2["cli_mode"])

    def test_save_validation(self):
        valid = dict(DEFAULTS)
        valid["interface_mode"] = "cli"
        valid["cli_mode"] = True
        save(self.config_path, valid)

        loaded = load(self.config_path)
        self.assertEqual(loaded["interface_mode"], "cli")
        self.assertTrue(loaded["cli_mode"])

        invalid_mode = dict(DEFAULTS)
        invalid_mode["interface_mode"] = "gui"
        with self.assertRaises(QueueError):
            save(self.config_path, invalid_mode)

        invalid_cli = dict(DEFAULTS)
        invalid_cli["cli_mode"] = 12345
        with self.assertRaises(QueueError):
            save(self.config_path, invalid_cli)

    def test_corrupt_config_load_raises_queue_error(self):
        self.config_path.write_text("{ corrupt json: [")
        with self.assertRaises(QueueError):
            load(self.config_path)

    def test_save_empty_path_raises_queue_error(self):
        with self.assertRaises(QueueError):
            save("", dict(DEFAULTS))


class CliModeRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.name', 'CLI Tests'), ('config', 'user.email', 'test@example.invalid')]:
            git(self.repo, *args)
        (self.repo / 'README.md').write_text('cli fixture')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'cli fixture')
        self.state = self.root / "state"
        self.store = Store(self.state)

    def tearDown(self):
        set_mock_metadata(None)
        self.store.close()
        self.temp.cleanup()

    def test_overview_with_usage_and_empty_queue(self):
        # 100% used -> 0% usage remaining
        provider = MockUsageProvider(five_used=100, weekly_used=37)
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)

        self.assertIn("Connection Status: Connected", out)
        self.assertIn("0% usage remaining", out)
        self.assertIn("Reset Time:", out)
        self.assertIn("Weekly Usage: 37% used (63% remaining)", out)
        self.assertIn("=== Projects & Conversations ===", out)
        self.assertIn("=== Queue ===", out)
        self.assertIn("(no active or queued jobs)", out)

    def test_overview_partial_usage(self):
        # 55% used -> 45% usage remaining
        provider = MockUsageProvider(five_used=55, weekly_used=20)
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)

        self.assertIn("45% usage remaining", out)
        self.assertIn("Weekly Usage: 20% used (80% remaining)", out)

    def test_overview_with_projects_and_chats(self):
        # Set mock metadata for codex projects discovery
        session_id = "22222222-2222-4222-8222-222222222222"
        mock_meta = (
            {str(self.repo): "My CLI Project"},
            {"proj1": "My CLI Project"},
            [{
                "id": session_id,
                "cwd": str(self.repo),
                "name": "Auth Flow Refactoring",
                "preview": "Refactoring the token refresh mechanism",
                "updated_at": time.time(),
                "created_at": time.time(),
            }]
        )
        set_mock_metadata(mock_meta)
        provider = MockUsageProvider()
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)

        self.assertIn("My CLI Project", out)
        self.assertIn(str(self.repo), out)
        self.assertIn("Auth Flow Refactoring", out)

    def test_overview_with_active_and_next_reset_jobs(self):
        provider = MockUsageProvider()
        # Add next-reset job
        job1 = self.store.add(
            prompt="Optimize database index performance",
            repo=self.repo,
            mode="next-reset",
            reset=int(time.time() + 3600)
        )
        # Add manual job
        job2 = self.store.add(
            prompt="Fix edge case in logging",
            repo=self.repo,
            mode="manual"
        )

        out = render_cli_overview(state=self.state, store=self.store, provider=provider)

        self.assertIn(f"Job #{job1}", out)
        self.assertIn(f"Job #{job2}", out)
        self.assertIn("Status: queued", out)
        self.assertIn("Mode: next-reset", out)
        self.assertIn("Reset Countdown:", out)
        self.assertIn("Prompt job to send after reset: Optimize database index performance", out)
        self.assertIn("Prompt job to send: Fix edge case in logging", out)

    def test_overview_offline_fallback_to_cached_usage(self):
        # Prepopulate store with cached usage
        now = time.time()
        cdata = json.dumps({
            'five': {'used': 80, 'minutes': 300, 'reset': int(now) + 3600},
            'weekly': {'used': 40, 'minutes': 10080, 'reset': int(now) + 86400},
            'observed': now,
            'last_refresh': now
        })
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('latest_usage', ?)", (cdata,))

        # Provider fails
        provider = MockUsageProvider(error="Codex offline")
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)

        self.assertIn("Connection Status: Cached", out)
        self.assertIn("20% usage remaining", out)
        self.assertIn("Weekly Usage: 40% used (60% remaining)", out)

    def test_run_cli_mode_stream(self):
        provider = MockUsageProvider()
        stream = io.StringIO()
        code = run_cli_mode(state=self.state, store=self.store, provider=provider, stream=stream)
        self.assertEqual(code, 0)
        output = stream.getvalue()
        self.assertIn("=== Codex Queue (CLI Mode) ===", output)
        self.assertIn("Connection Status: Connected", output)

    def test_overview_empty_prompt(self):
        provider = MockUsageProvider()
        sess_id = "44444444-4444-4444-8444-444444444444"
        job = self.store.add_session(session_id=sess_id, repo=self.repo, prompt="", ownership=True)
        # Verify empty prompt rendering
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET prompt='' WHERE id=?", (job,))
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)
        self.assertIn(f"Job #{job}", out)
        self.assertIn("(continue session", out)

    def test_overview_multiline_prompt_truncation(self):
        provider = MockUsageProvider()
        multiline = "\n".join([f"Line {i} of detailed instruction" for i in range(1, 15)])
        job = self.store.add(prompt=multiline, repo=self.repo, mode="manual")
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)
        self.assertIn(f"Job #{job}", out)
        self.assertIn("Line 1 of detailed instruction", out)
        self.assertIn("Line 2 of detailed instruction", out)
        self.assertIn("... (10 more lines)", out)

    def test_overview_next_reset_fallback_to_provider_reset(self):
        # A next-reset job with missing saved_reset timestamp falls back to provider.read().five.reset
        provider = MockUsageProvider()
        future_ts = int(time.time() + 7200)
        job = self.store.add(prompt="Test fallback reset", repo=self.repo, mode="next-reset", reset=future_ts)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET saved_reset=NULL WHERE id=?", (job,))
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)
        self.assertIn(f"Job #{job}", out)
        self.assertIn("in 1h 59m", out)

    def test_overview_missing_codex_thread_metadata(self):
        # Continued session job whose thread is not in local SQLite threads metadata
        provider = MockUsageProvider()
        sess_id = "33333333-3333-4333-8333-333333333333"
        job = self.store.add_session(
            session_id=sess_id,
            repo=self.repo,
            cwd=".",
            prompt="Follow up changes",
            mode="manual",
            ownership=True
        )
        out = render_cli_overview(state=self.state, store=self.store, provider=provider)
        self.assertIn(f"Job #{job}", out)
        self.assertIn(sess_id, out)
        self.assertIn("Follow up changes", out)


class CliCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.name', 'CLI Tests'), ('config', 'user.email', 'test@example.invalid')]:
            git(self.repo, *args)
        (self.repo / 'README.md').write_text('cli fixture')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'cli fixture')
        self.state = self.root / "state"
        self.config_path = self.root / "config.json"
        self.fixture_path = self.root / "usage.json"
        now = time.time()
        window = lambda m, used: {'usedPercent': used, 'windowDurationMins': m, 'resetsAt': int(now) + m * 60}
        self.fixture_path.write_text(json.dumps({
            'observed_at': now,
            'response': {'rateLimits': {'limitId': 'codex', 'primary': window(300, 100), 'secondary': window(10080, 25)}}
        }))

    def tearDown(self):
        self.temp.cleanup()

    def test_main_with_cli_flag(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--state", str(self.state), "--config", str(self.config_path), "--fixture", str(self.fixture_path), "--cli"])
        self.assertEqual(code, 0)
        val = buf.getvalue()
        self.assertIn("=== Codex Queue (CLI Mode) ===", val)
        self.assertIn("0% usage remaining", val)

    def test_main_cli_subcommand(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--state", str(self.state), "--config", str(self.config_path), "--fixture", str(self.fixture_path), "cli"])
        self.assertEqual(code, 0)
        val = buf.getvalue()
        self.assertIn("=== Codex Queue (CLI Mode) ===", val)
        self.assertIn("0% usage remaining", val)

    def test_main_defaults_to_cli_when_interface_mode_is_cli(self):
        save(self.config_path, {**DEFAULTS, "interface_mode": "cli", "cli_mode": True})
        buf = io.StringIO()
        with patch("sys.stdout", buf), patch("codex_queue.tui.QueueApp.run") as mock_tui_run:
            code = main(["--state", str(self.state), "--config", str(self.config_path), "--fixture", str(self.fixture_path)])
            mock_tui_run.assert_not_called()
        self.assertEqual(code, 0)
        val = buf.getvalue()
        self.assertIn("=== Codex Queue (CLI Mode) ===", val)

    def test_main_tui_flag_overrides_cli_setting(self):
        save(self.config_path, {**DEFAULTS, "interface_mode": "cli", "cli_mode": True})
        with patch("codex_queue.tui.QueueApp.run") as mock_tui_run:
            mock_tui_run.return_value = 0
            code = main(["--state", str(self.state), "--config", str(self.config_path), "--fixture", str(self.fixture_path), "--tui"])
            mock_tui_run.assert_called_once()
        self.assertEqual(code, 0)

    def test_main_toggle_cli_flag(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--config", str(self.config_path), "--toggle-cli"])
        self.assertEqual(code, 0)
        cfg = load(self.config_path)
        self.assertEqual(cfg["interface_mode"], "cli")
        self.assertTrue(cfg["cli_mode"])

    def test_main_settings_subcommand(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--config", str(self.config_path), "settings", "--interface-mode", "cli"])
        self.assertEqual(code, 0)
        cfg = load(self.config_path)
        self.assertEqual(cfg["interface_mode"], "cli")

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--config", str(self.config_path), "settings", "--toggle-cli"])
        self.assertEqual(code, 0)
        cfg2 = load(self.config_path)
        self.assertEqual(cfg2["interface_mode"], "tui")

    def test_main_corrupt_config_clean_error(self):
        self.config_path.write_text("{ not valid json: ")
        err_buf = io.StringIO()
        with patch("sys.stderr", err_buf):
            code = main(["--state", str(self.state), "--config", str(self.config_path), "--cli"])
        self.assertEqual(code, 1)
        err = err_buf.getvalue()
        self.assertIn("error", err)
        self.assertIn("Corrupted config file", err)

    def test_main_toggle_cli_corrupt_config(self):
        self.config_path.write_text("{ broken: ")
        err_buf = io.StringIO()
        with patch("sys.stderr", err_buf):
            code = main(["--config", str(self.config_path), "--toggle-cli"])
        self.assertEqual(code, 1)
        self.assertIn("Corrupted config file", err_buf.getvalue())

    def test_mutually_exclusive_cli_and_tui_flags(self):
        # Specifying both --cli and --tui should trigger an argparse error (SystemExit code 2)
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["--cli", "--tui"])
            self.assertEqual(ctx.exception.code, 2)

    def test_render_cli_overview_default_config_path(self):
        # Calling render_cli_overview with config=None should use config_path() rather than failing
        provider = MockUsageProvider()
        out = render_cli_overview(state=self.state, config=None, provider=provider)
        self.assertIn("=== Codex Queue (CLI Mode) ===", out)


class TuiSettingsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.config_path = self.root / "config.json"
        save(self.config_path, dict(DEFAULTS))
        self.provider = MockUsageProvider()

    def tearDown(self):
        self.temp.cleanup()

    async def test_tui_settings_cli_mode_toggle_and_save(self):
        app = QueueApp(self.state, config=self.config_path, provider=self.provider)
        async with app.run_test(size=(120, 60)) as pilot:
            app.push_screen(Settings())
            await pilot.pause()

            settings_screen = app.screen
            self.assertIsInstance(settings_screen, Settings)

            # Check presence of interface_mode select and cli_mode switch
            mode_select = settings_screen.query_one("#interface_mode", Select)
            cli_switch = settings_screen.query_one("#cli_mode", Switch)

            self.assertEqual(mode_select.value, "tui")
            self.assertFalse(cli_switch.value)

            # Toggle switch to True
            cli_switch.value = True
            await pilot.pause()
            self.assertEqual(mode_select.value, "cli")

            # Click save
            settings_screen.query_one("#save").scroll_visible()
            await pilot.pause()
            await pilot.click("#save")
            await pilot.pause()

            # Verify persisted config
            saved_cfg = load(self.config_path)
            self.assertEqual(saved_cfg["interface_mode"], "cli")
            self.assertTrue(saved_cfg["cli_mode"])

    async def test_tui_settings_toggle_back_to_tui(self):
        # Start in CLI mode
        save(self.config_path, {**DEFAULTS, "interface_mode": "cli", "cli_mode": True})
        app = QueueApp(self.state, config=self.config_path, provider=self.provider)
        async with app.run_test(size=(120, 60)) as pilot:
            app.push_screen(Settings())
            await pilot.pause()

            settings_screen = app.screen
            mode_select = settings_screen.query_one("#interface_mode", Select)
            cli_switch = settings_screen.query_one("#cli_mode", Switch)

            self.assertEqual(mode_select.value, "cli")
            self.assertTrue(cli_switch.value)

            # Toggle back to TUI via switch
            cli_switch.value = False
            await pilot.pause()
            self.assertEqual(mode_select.value, "tui")

            # Click save
            settings_screen.query_one("#save").scroll_visible()
            await pilot.pause()
            await pilot.click("#save")
            await pilot.pause()

            # Verify persisted config
            saved_cfg = load(self.config_path)
            self.assertEqual(saved_cfg["interface_mode"], "tui")
            self.assertFalse(saved_cfg["cli_mode"])


class ChatTitleSanitizationTests(unittest.TestCase):
    def test_multiline_prompt_sanitization_takes_first_line(self):
        raw = """Create a Scheduled Task called "Weekday Morning Brief" that runs every weekday at 7:30 AM in my local time zone.

First, make a lightweight read from [@Slack](plugin://slack@openai-curated-remote) and [@Gmail](plugin://gmail@openai-curated-remote).
Once connected, retry and continue.
Keep it fast, concise, and skimmable. No calendar section. No process notes."""
        sanitized = sanitize_chat_title(raw)
        self.assertNotIn("\n", sanitized)
        self.assertTrue(sanitized.startswith('Create a Scheduled Task called "Weekday Morning Brief"'))
        self.assertTrue(sanitized.endswith("…"))
        self.assertLessEqual(len(sanitized), 60)

    def test_sanitization_collapses_whitespace_and_truncates(self):
        raw = "   \n\n   Deploy    production    bundle   to   cloud   cluster    with   zero   downtime   canary    release   \n\n   second line"
        sanitized = sanitize_chat_title(raw, max_length=50)
        self.assertNotIn("\n", sanitized)
        self.assertNotIn("  ", sanitized)
        self.assertTrue(sanitized.endswith("…"))
        self.assertLessEqual(len(sanitized), 50)

    def test_sanitization_empty_or_none(self):
        self.assertEqual(sanitize_chat_title(None), "Untitled chat")
        self.assertEqual(sanitize_chat_title(""), "Untitled chat")
        self.assertEqual(sanitize_chat_title("   \n\n  \t  "), "Untitled chat")

    def test_resolve_chat_title_sanitizes_raw_metadata(self):
        session_id = "11111111-1111-4111-8111-111111111111"
        multiline_thread = {
            "id": session_id,
            "cwd": "/some/repo",
            "name": "Refactor authentication flow\n\nEnsure JWT expiration and refresh token rotation works as intended.",
            "updated_at": time.time(),
        }
        set_mock_metadata(({}, {}, [multiline_thread]))
        try:
            resolved = resolve_chat_title(session_id)
            self.assertNotIn("\n", resolved)
            self.assertEqual(resolved, "Refactor authentication flow")
        finally:
            set_mock_metadata(None)


class ProjectDiscoveryAndFilteringTests(unittest.TestCase):
    def test_path_aliases_does_not_iterate_users_directory(self):
        # Even with a path like /Users/example/project, aliases should only contain the path itself
        # and current user's home remapping if applicable - no foreign user paths
        aliases = _path_aliases("/Users/example/Developer/Sample-Project.git")
        self.assertTrue(len(aliases) >= 1)
        expected_target = str(Path.home() / "Developer" / "Sample-Project.git")
        self.assertIn(expected_target, aliases)
        for alias in aliases:
            for forbidden_user in ("temp", "Shared", "Guest"):
                self.assertNotIn(f"/Users/{forbidden_user}/", alias)

    def test_projects_with_zero_conversations_filtered_out(self):
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name).resolve()
            repo_active = root / "active"
            repo_active.mkdir()
            repo_inactive = root / "inactive"
            repo_inactive.mkdir()
            state = root / "state"
            store = Store(state)
            provider = MockUsageProvider()

            mock_meta = (
                {str(repo_active): "Active Project", str(repo_inactive): "Inactive Project"},
                {"p1": "Active Project", "p2": "Inactive Project"},
                [{
                    "id": "55555555-5555-4555-8555-555555555555",
                    "cwd": str(repo_active),
                    "name": "Active Chat",
                    "updated_at": time.time(),
                }]
            )
            set_mock_metadata(mock_meta)
            out = render_cli_overview(state=state, store=store, provider=provider)

            self.assertIn("Active Project", out)
            self.assertIn("Active Chat", out)
            # Inactive project with zero conversations must be filtered out
            self.assertNotIn("Inactive Project", out)
            self.assertNotIn("(none)", out)
            store.close()
        finally:
            set_mock_metadata(None)
            temp.cleanup()

    def test_top_5_projects_and_top_3_chats_limit(self):
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name).resolve()
            state = root / "state"
            store = Store(state)
            provider = MockUsageProvider()

            projects_dict = {}
            threads_list = []
            now = time.time()
            for p_idx in range(1, 8):  # 7 projects
                p_dir = root / f"proj_{p_idx}"
                p_dir.mkdir()
                projects_dict[str(p_dir)] = f"Project_{p_idx}"
                for c_idx in range(1, 6):  # 5 chats each
                    threads_list.append({
                        "id": f"{p_idx}{c_idx}000000-0000-4000-8000-000000000000",
                        "cwd": str(p_dir),
                        "name": f"P{p_idx} Chat {c_idx}",
                        "updated_at": now - (p_idx * 100) - c_idx,
                    })

            set_mock_metadata((projects_dict, {}, threads_list))
            out = render_cli_overview(state=state, store=store, provider=provider)

            # Top 5 projects (Project_1 to Project_5) should appear
            for p_idx in range(1, 6):
                self.assertIn(f"Project_{p_idx}", out)
            # Projects 6 and 7 should be excluded from top 5
            self.assertNotIn("Project_6", out)
            self.assertNotIn("Project_7", out)

            # Top 3 chats for Project_1 should appear, chats 4 and 5 should not
            self.assertIn("P1 Chat 1", out)
            self.assertIn("P1 Chat 2", out)
            self.assertIn("P1 Chat 3", out)
            self.assertNotIn("P1 Chat 4", out)
            self.assertNotIn("P1 Chat 5", out)
            store.close()
        finally:
            set_mock_metadata(None)
            temp.cleanup()


class FastCachedUsageAndPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.config_path = self.root / "config.json"
        save(self.config_path, dict(DEFAULTS))
        self.store = Store(self.state)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_cli_overview_reads_cached_usage_instantly_without_app_server(self):
        now = time.time()
        cdata = json.dumps({
            'five': {'used': 75, 'minutes': 300, 'reset': int(now) + 5400},
            'weekly': {'used': 30, 'minutes': 10080, 'reset': int(now) + 86400},
            'observed': now,
            'last_refresh': now
        })
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('latest_usage', ?)", (cdata,))

        # With provider=None and fixture=None, must read cached usage without calling AppServer
        start = time.perf_counter()
        out = render_cli_overview(state=self.state, config=self.config_path, store=self.store, provider=None, fixture=None)
        elapsed = time.perf_counter() - start

        self.assertIn("Connection Status: Cached", out)
        self.assertIn("25% usage remaining", out)
        self.assertIn("Weekly Usage: 30% used (70% remaining)", out)
        # Must execute in under 0.5s
        self.assertLess(elapsed, 0.5)


class InteractiveCliControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "test-repo"
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.name', 'Tester'), ('config', 'user.email', 'tester@example.invalid')]:
            git(self.repo, *args)
        (self.repo / "README.md").write_text("readme")
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'init')

        self.state = self.root / "state"
        self.config_path = self.root / "config.json"
        save(self.config_path, dict(DEFAULTS))
        self.store = Store(self.state)
        self.provider = MockUsageProvider(five_used=50, weekly_used=20)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_interactive_menu_quit(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        with patch("builtins.input", side_effect=["q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn("=== Codex Queue (CLI Mode) ===", output)
        self.assertIn("[1] View/Manage Queue", output)
        self.assertIn("Exiting Codex Queue.", output)

    def test_interactive_view_manage_queue_and_details(self):
        job_id = self.store.add(prompt="Test interactive details prompt", repo=str(self.repo), mode="manual")
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # User enters 1 (View/Manage Queue), then v 1 (View Details), then q
        with patch("builtins.input", side_effect=["1", f"v {job_id}", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn("=== Queue Management ===", output)
        self.assertIn(f"--- Job #{job_id} Details ---", output)
        self.assertIn("Test interactive details prompt", output)

    def test_interactive_pause_and_resume_job(self):
        job_id = self.store.add(prompt="Job to pause", repo=str(self.repo), mode="manual")
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # 1 -> p <id> -> 1 -> r <id> -> q
        with patch("builtins.input", side_effect=["1", f"p {job_id}", "1", f"r {job_id}", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn(f"Job #{job_id} status updated to: paused", output)
        self.assertIn(f"Job #{job_id} status updated to: queued", output)

    def test_interactive_add_prompt_job(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # 3 (Add Job) -> prompt -> repo path -> mode 1 (manual) -> q
        with patch("builtins.input", side_effect=["3", "Fix memory leak in parser", str(self.repo), "1", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn("successfully queued", output)
        rows = self.store.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"], "Fix memory leak in parser")
        self.assertEqual(rows[0]["status"], "queued")

    def test_interactive_toggle_settings(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # 5 (Settings) -> t (Toggle) -> q
        with patch("builtins.input", side_effect=["5", "t", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        cfg = load(self.config_path)
        self.assertEqual(cfg["interface_mode"], "cli")
        self.assertTrue(cfg["cli_mode"])

    def test_interactive_refresh_usage(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # 4 (Refresh Usage) -> q
        with patch("builtins.input", side_effect=["4", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn("Usage refreshed successfully.", output)

    def test_non_interactive_flag_bypasses_tty_loop(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--state", str(self.state), "--config", str(self.config_path), "--cli", "--non-interactive"])
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("=== Codex Queue (CLI Mode) ===", output)
        self.assertNotIn("Commands:", output)

    def test_cli_overview_without_cached_usage_never_spawns_app_server(self):
        # Even if codex binary exists in PATH, overview rendering must not spawn AppServer
        with patch("codex_queue.cli_mode.AppServer.read", side_effect=AssertionError("AppServer.read must not be called!")):
            start = time.perf_counter()
            out = render_cli_overview(state=self.state, config=self.config_path, store=self.store, provider=None, fixture=None)
            elapsed = time.perf_counter() - start
            self.assertIn("Connection Status: Disconnected", out)
            self.assertIn("5-Hour Allowance: Unknown", out)
            self.assertLess(elapsed, 0.2)

    def test_show_job_details_displays_real_attempts_and_reason(self):
        job_id = self.store.add(prompt="Test attempts and reason prompt", repo=str(self.repo), mode="manual")
        now = time.time()
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET reason='interrupted by signal' WHERE id=?", (job_id,))
            self.store.db.execute(
                "INSERT INTO attempts(job, number, worktree, branch, status, started, ended, log) VALUES(?, 1, ?, 'feature-branch', 'interrupted', ?, ?, 'error log')",
                (job_id, str(self.repo), now - 30, now)
            )

        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        with patch("builtins.input", side_effect=["1", f"v #{job_id}", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn("Reason: interrupted by signal", output)
        self.assertIn("status=interrupted", output)
        self.assertIn("duration=30s", output)
        self.assertIn("branch=feature-branch", output)
        self.assertNotIn("state=None", output)
        self.assertNotIn("exit_code=None", output)

    def test_view_manage_queue_prefix_and_not_found(self):
        job_id = self.store.add(prompt="Prefix test prompt", repo=str(self.repo), mode="manual")
        prefix = job_id[:6]
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # Test valid prefix
        with patch("builtins.input", side_effect=["1", f"v {prefix}", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn(f"--- Job #{job_id} Details ---", output)

        # Test job not found
        stream2 = io.StringIO()
        controller2 = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream2)
        with patch("builtins.input", side_effect=["1", "v nonexistingjobid", "q"]):
            ret2 = controller2.run_loop()
        self.assertEqual(ret2, 0)
        self.assertIn("Job not found: 'nonexistingjobid'", stream2.getvalue())

    def test_add_prompt_job_with_file_and_empty_file(self):
        prompt_file = Path(self.temp.name) / "prompt.txt"
        prompt_file.write_text("Instruction loaded from text file\nSecond line")

        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        with patch("builtins.input", side_effect=["3", f"@{prompt_file}", str(self.repo), "1", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        self.assertIn("successfully queued", stream.getvalue())

        # Empty prompt file
        empty_file = Path(self.temp.name) / "empty.txt"
        empty_file.write_text("   \n\n  ")
        stream2 = io.StringIO()
        controller2 = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream2)
        with patch("builtins.input", side_effect=["3", f"@{empty_file}", "q"]):
            ret2 = controller2.run_loop()
        self.assertEqual(ret2, 0)
        self.assertIn("Prompt file is empty.", stream2.getvalue())

    def test_add_prompt_job_next_reset_missing_timestamp(self):
        stream = io.StringIO()
        # provider with no future reset
        expired_provider = MockUsageProvider(five_used=50, weekly_used=20)
        expired_provider.read = lambda: None
        controller = CliController(state=self.state, config=self.config_path, provider=expired_provider, store=self.store, stream=stream)
        with patch("builtins.input", side_effect=["3", "Do something after reset", str(self.repo), "3", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        self.assertIn("Cannot schedule with 'next-reset'", stream.getvalue())

    def test_manage_settings_toggle_execution_enabled(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        with patch("builtins.input", side_effect=["5", "e", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        cfg = load(self.config_path)
        self.assertTrue(cfg["execution_enabled"])
        self.assertIn("Execution permission set to: True", stream.getvalue())

    def test_discover_local_codex_strictly_filters_foreign_user_paths(self):
        catalog = Catalog()
        mock_meta = (
            {
                **{f"/Users/{u}/project": f"{u} Project" for u in ("Shared", "Guest", "temp")},
                str(self.repo): "My Real Project",
            },
            {},
            [
                {
                    "id": "77777777-7777-4777-8777-777777777777",
                    "cwd": str(self.repo),
                    "name": "Chat in real project",
                    "updated_at": time.time(),
                }
            ],
        )
        set_mock_metadata(mock_meta)
        try:
            catalog.discover_local_codex()
            self.assertIn(str(self.repo), catalog.projects)
            # Foreign user projects with no conversations or foreign paths should not exist in catalog
            for u in ("Shared", "Guest", "temp"):
                self.assertNotIn(f"/Users/{u}/project", catalog.projects)
        finally:
            set_mock_metadata(None)

    def test_interactive_cancel_and_retry_job(self):
        job_id = self.store.add(prompt="Job to cancel and retry", repo=str(self.repo), mode="manual")
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # 1 -> c <id> -> 1 -> t <id> -> q
        with patch("builtins.input", side_effect=["1", f"c {job_id}", "1", f"t {job_id}", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn(f"Job #{job_id} status updated to: cancelled", output)
        self.assertIn(f"Job #{job_id} status updated to: queued", output)

    def test_interactive_view_projects_and_chats(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # 2 (Projects & Chats) -> q
        with patch("builtins.input", side_effect=["2", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn("=== Active Projects & Recent Chats ===", output)

    def test_show_job_details_attempt_duration_edge_cases(self):
        job_id = self.store.add(prompt="Job with duration edge cases", repo=str(self.repo), mode="manual")
        now = time.time()
        with self.store.db:
            # Attempt with ended in the past (clock drift/inversion)
            self.store.db.execute(
                "INSERT INTO attempts(job, number, worktree, branch, status, started, ended, log) VALUES(?, 1, ?, 'test', 'failed', ?, ?, '')",
                (job_id, str(self.repo), now, now - 10)
            )
            # Attempt still in progress (ended is None)
            self.store.db.execute(
                "INSERT INTO attempts(job, number, worktree, branch, status, started, ended, log) VALUES(?, 2, ?, 'test', 'running', ?, NULL, '')",
                (job_id, str(self.repo), now)
            )

        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        with patch("builtins.input", side_effect=["1", f"v {job_id}", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()
        self.assertIn("duration=0s", output)
        self.assertIn("Attempt #2: status=running", output)

    def test_view_manage_queue_wildcard_prefix_safety(self):
        job_id = self.store.add(prompt="Wildcard prefix test", repo=str(self.repo), mode="manual")
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        with patch("builtins.input", side_effect=["1", "v %", "1", "v _", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        self.assertIn("Job not found: '%'", stream.getvalue())
        self.assertIn("Job not found: '_'", stream.getvalue())

    def test_add_prompt_job_named_modes(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream)
        # Mode entered as literal "immediate" instead of "2"
        with patch("builtins.input", side_effect=["3", "Immediate prompt", str(self.repo), "immediate", "q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        rows = self.store.rows()
        imm_rows = [r for r in rows if r["prompt"] == "Immediate prompt"]
        self.assertEqual(len(imm_rows), 1)
        self.assertEqual(imm_rows[0]["mode"], "immediate")

    def test_foreign_user_path_with_chat_remapped(self):
        catalog = Catalog()
        mock_meta = (
            {
                f"/Users/{'other'}/project": "Other Project",
                str(self.repo): "My Real Project",
            },
            {},
            [
                {
                    "id": "88888888-8888-4888-8888-888888888888",
                    "cwd": f"/Users/{'other'}/project",
                    "name": "Foreign chat",
                    "updated_at": time.time(),
                }
            ],
        )
        set_mock_metadata(mock_meta)
        try:
            catalog.discover_local_codex()
            # Foreign user literal path should never be in catalog
            self.assertNotIn(f"/Users/{'other'}/project", catalog.projects)
        finally:
            set_mock_metadata(None)


class CliStyleAndColorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "test-repo"
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.name', 'Tester'), ('config', 'user.email', 'tester@example.invalid')]:
            git(self.repo, *args)
        (self.repo / "README.md").write_text("readme")
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'init')

        self.state = self.root / "state"
        self.config_path = self.root / "config.json"
        save(self.config_path, dict(DEFAULTS))
        self.store = Store(self.state)
        self.provider = MockUsageProvider(five_used=100, weekly_used=56)

    def tearDown(self):
        set_mock_metadata(None)
        self.store.close()
        self.temp.cleanup()

    def test_should_color_logic(self):
        # Stream None defaults to False unless overridden
        self.assertFalse(should_color(None))
        self.assertTrue(should_color(None, color_arg=True))
        self.assertFalse(should_color(None, color_arg=False))

        # Non-tty stream
        buf = io.StringIO()
        self.assertFalse(should_color(buf))

        # Mock tty stream
        class MockTTY(io.StringIO):
            def isatty(self):
                return True

        tty = MockTTY()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
            os.environ.pop("NO_COLOR", None)
            os.environ.pop("FORCE_COLOR", None)
            self.assertTrue(should_color(tty))

        # NO_COLOR overrides
        with patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "xterm-256color"}):
            self.assertFalse(should_color(tty))

        # FORCE_COLOR overrides
        with patch.dict(os.environ, {"FORCE_COLOR": "1", "TERM": "dumb"}):
            self.assertTrue(should_color(buf))

        # TERM=dumb overrides
        with patch.dict(os.environ, {"TERM": "dumb"}):
            os.environ.pop("FORCE_COLOR", None)
            os.environ.pop("NO_COLOR", None)
            self.assertFalse(should_color(tty))

    def test_strip_ansi(self):
        raw = "\033[1;36m⚡ Codex Queue\033[0m \033[38;5;244m◈\033[0m"
        stripped = strip_ansi(raw)
        self.assertEqual(stripped, "⚡ Codex Queue ◈")
        self.assertEqual(strip_ansi("plain text"), "plain text")
        self.assertEqual(strip_ansi(""), "")

    def test_progress_bars_allowance_and_usage(self):
        style = Style(color=True)
        unstyle = Style(color=False)

        # 0% remaining allowance
        bar_0 = style.allowance_bar(0)
        self.assertIn("░", bar_0)
        self.assertIn("\033[", bar_0)

        unbar_0 = unstyle.allowance_bar(0)
        self.assertEqual(unbar_0, "[░░░░░░░░░░░░]")
        self.assertNotIn("\033[", unbar_0)

        # 100% remaining allowance
        unbar_100 = unstyle.allowance_bar(100)
        self.assertEqual(unbar_100, "[████████████]")

        # Usage bar
        unbar_use_50 = unstyle.usage_bar(50)
        self.assertEqual(unbar_use_50, "[██████░░░░░░]")

    def test_status_and_connection_pills(self):
        style = Style(color=True)
        unstyle = Style(color=False)

        # Status pills
        self.assertIn("RUNNING", style.status_pill("running"))
        self.assertIn("QUEUED", style.status_pill("queued"))
        self.assertIn("PAUSED", style.status_pill("paused"))
        self.assertIn("CANCELLED", style.status_pill("cancelled"))
        self.assertIn("FAILED", style.status_pill("failed"))
        self.assertIn("DONE", style.status_pill("done"))

        self.assertEqual(unstyle.status_pill("queued"), "[queued]")

        # Connection pills
        self.assertIn("Connected", style.connection_pill("Connected"))
        self.assertIn(Glyphs.DOT, style.connection_pill("Connected"))
        self.assertIn(Glyphs.HALF_DOT, style.connection_pill("Connected (stale)"))
        self.assertIn(Glyphs.CROSS, style.connection_pill("Disconnected"))
        self.assertIn(Glyphs.DOTTED, style.connection_pill("Cached (stale)"))
        self.assertIn(Glyphs.CIRCLE, style.connection_pill("Cached"))

        self.assertEqual(unstyle.connection_pill("Disconnected"), "Disconnected")

    def test_render_cli_overview_styled_mode(self):
        session_id = "99999999-9999-4999-8999-999999999999"
        mock_meta = (
            {str(self.repo): "Styled Project"},
            {"p1": "Styled Project"},
            [{
                "id": session_id,
                "cwd": str(self.repo),
                "name": "Design Polish",
                "preview": "Elevating the CLI design",
                "updated_at": time.time(),
                "created_at": time.time(),
            }]
        )
        set_mock_metadata(mock_meta)
        job_id = self.store.add(prompt="Elevate CLI styling", repo=str(self.repo), mode="next-reset", reset=int(time.time() + 1800))

        out = render_cli_overview(state=self.state, store=self.store, provider=self.provider, color=True)

        # Check for nerd-font glyphs
        self.assertIn(Glyphs.LOGO, out)
        self.assertIn(Glyphs.GAUGE, out)
        self.assertIn(Glyphs.FOLDER, out)
        self.assertIn(Glyphs.CHAT, out)
        self.assertIn(Glyphs.CLOCK, out)
        self.assertIn(Glyphs.DIAMOND, out)

        # Check for ANSI escape sequences
        self.assertIn("\033[", out)

        # Check content is retained
        self.assertIn("Account / Usage Overview", out)
        self.assertIn("Projects & Conversations", out)
        self.assertIn("Queue", out)
        self.assertIn("Styled Project", out)
        self.assertIn("Design Polish", out)
        self.assertIn(f"Job #{job_id}", out)
        self.assertIn("Elevate CLI styling", out)

        # Stripping ANSI returns clean readable text
        clean = strip_ansi(out)
        self.assertNotIn("\033[", clean)
        self.assertIn("Styled Project", clean)

    def test_cli_controller_styled_commands_menu(self):
        stream = io.StringIO()
        controller = CliController(state=self.state, config=self.config_path, provider=self.provider, store=self.store, stream=stream, color=True)
        with patch("builtins.input", side_effect=["q"]):
            ret = controller.run_loop()
        self.assertEqual(ret, 0)
        output = stream.getvalue()

        # Styled commands menu has ANSI codes and glyphs
        self.assertIn("\033[", output)
        self.assertIn(Glyphs.LOGO, output)
        self.assertIn("Commands:", output)
        self.assertIn("View/Manage Queue", output)

    def test_main_cli_color_flags(self):
        # --color flag forces color even when sys.stdout is redirected
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = main(["--state", str(self.state), "--config", str(self.config_path), "--cli", "--color", "--non-interactive"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("\033[", out)
        self.assertIn(Glyphs.LOGO, out)

        # --no-color flag strips color
        buf2 = io.StringIO()
        with patch("sys.stdout", buf2):
            code2 = main(["--state", str(self.state), "--config", str(self.config_path), "--cli", "--no-color", "--non-interactive"])
        self.assertEqual(code2, 0)
        out2 = buf2.getvalue()
        self.assertNotIn("\033[", out2)
        self.assertIn("=== Codex Queue (CLI Mode) ===", out2)

    def test_dynamic_divider_width(self):
        import collections
        from codex_queue.cli_style import get_divider_width

        Size = collections.namedtuple("terminal_size", ["columns", "lines"])

        with patch("shutil.get_terminal_size", return_value=Size(40, 20)):
            self.assertEqual(get_divider_width(), 36)
            style_narrow = Style(True)
            self.assertEqual(len(strip_ansi(style_narrow.divider())), 36)

        with patch("shutil.get_terminal_size", return_value=Size(120, 40)):
            self.assertEqual(get_divider_width(), 68)
            style_wide = Style(True)
            self.assertEqual(len(strip_ansi(style_wide.divider())), 68)

    def test_format_display_path(self):
        from codex_queue.cli_style import format_display_path
        home = str(Path.home())
        self.assertEqual(format_display_path(home), "~")
        self.assertEqual(format_display_path(f"{home}/Developer/my-repo"), "~/Developer/my-repo")
        self.assertEqual(format_display_path("/opt/custom/repo"), "/opt/custom/repo")
        self.assertEqual(format_display_path(""), "")
        self.assertEqual(format_display_path(None), "")

    def test_mock_metadata_propagation_with_uncreated_paths(self):
        # Even if mock project paths do not exist physically on disk,
        # render_cli_overview should render them when mock metadata is set.
        session_id = "11111111-2222-4333-8444-555555555555"
        non_existent_mock_path = f"{Path.home()}/Documents/Codex/2026-10-10/synthetic-nonexistent-project"
        mock_meta = (
            {non_existent_mock_path: "Synthetic Project"},
            {},
            [{
                "id": session_id,
                "cwd": non_existent_mock_path,
                "name": "Synthetic Chat",
                "updated_at": time.time(),
            }]
        )
        set_mock_metadata(mock_meta)
        out = render_cli_overview(state=self.state, store=self.store, provider=self.provider, color=True)
        self.assertIn("Synthetic Project", out)
        self.assertIn("Synthetic Chat", out)
        self.assertIn("~/Documents/Codex/2026-10-10/synthetic-nonexistent-project", out)

    def test_projects_dict_normalized_mapping_operations(self):
        from codex_queue.projects import ProjectsDict
        d = ProjectsDict()
        d['C:/Users/example/project'] = 'proj1'

        # setdefault on slash-variant should not duplicate
        res = d.setdefault('C:\\Users\\example\\project', 'proj2')
        self.assertEqual(res, 'proj1')
        self.assertEqual(len(d), 1)

        # __contains__ and get
        self.assertIn('C:\\Users\\example\\project', d)
        self.assertEqual(d.get('C:\\Users\\example\\project'), 'proj1')

        # __setitem__ on variant updates existing key rather than creating duplicate
        d['C:\\Users\\example\\project'] = 'proj1_updated'
        self.assertEqual(len(d), 1)
        self.assertEqual(d['C:/Users/example/project'], 'proj1_updated')

        # pop on variant
        val = d.pop('C:\\Users\\example\\project')
        self.assertEqual(val, 'proj1_updated')
        self.assertEqual(len(d), 0)

        # del on variant
        d['C:/Users/example/another'] = 'val'
        del d['C:\\Users\\example\\another']
        self.assertEqual(len(d), 0)

    def test_is_current_user_path_8_3_symmetry(self):
        from codex_queue.projects import _is_current_user_path
        # When home is short (e.g. EXAMPL~1) and path is long (example)
        with patch.object(Path, 'home', return_value=Path('C:/Users/EXAMPL~1')):
            self.assertTrue(_is_current_user_path('C:/Users/example/myproject'))
        # When home is long and path is short
        with patch.object(Path, 'home', return_value=Path('C:/Users/example')):
            self.assertTrue(_is_current_user_path('C:/Users/EXAMPL~1/myproject'))

    def test_stamp_and_safe_dt_epoch_handling(self):
        from codex_queue.tui import stamp
        from codex_queue.cli_mode import _safe_local_dt
        # 0 timestamp (Unix epoch) should not be 'Unknown'
        self.assertNotEqual(stamp(0), 'Unknown')
        self.assertNotEqual(stamp(0.0), 'Unknown')
        self.assertEqual(stamp(None), 'Unknown')
        self.assertEqual(stamp(''), 'Unknown')
        self.assertIsNotNone(_safe_local_dt(0))
        self.assertIsNotNone(_safe_local_dt(10))
        self.assertIsNone(_safe_local_dt(None))



