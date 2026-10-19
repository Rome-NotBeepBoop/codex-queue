"""Tests for project/chat metadata resolution, Details prompt visibility, and dashboard live usage."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from textual.widgets import OptionList, Static, TextArea, DataTable
from codex_queue.projects import resolve_project_name, resolve_chat_title, Catalog, Project, set_mock_metadata
from codex_queue.store import Store, git
from codex_queue.tui import QueueApp, Details, QueueView
from codex_queue.usage import Usage, Window


class LiveMockUsage:
    live = True
    calls = 0

    def read(self):
        self.calls += 1
        now = time.time()
        return Usage(Window(85, 300, int(now) + 7200), Window(25, 10080, int(now) + 86400), now)


class UIImprovementsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"codex": "codex", "bucket": "codex", "poll_interval_seconds": 60, "default_mode": "manual"}))
        self.store = Store(self.state)

        # Create isolated git repo for testing
        self.repo = self.root / "Hestia.git"
        self.repo.mkdir()
        for args in [('init',), ('config', 'user.name', 'Test User'), ('config', 'user.email', 'test@example.invalid')]:
            git(self.repo, *args)
        (self.repo / "README.md").write_text("test repo")
        git(self.repo, 'add', 'README.md')
        git(self.repo, 'commit', '-m', 'initial')

        self.mock_projects = {
            str(self.repo.resolve()): "Hestia Archive❤️‍🔥",
            "/Users/example/Developer/Sample-Project.git": "Sample Project"
        }
        self.mock_threads = [
            {"id": "01a14b97-ef3b-7852-a3d1-6c8d0d752482", "name": "Create cinematic showcase", "cwd": str(self.repo.resolve())}
        ]
        set_mock_metadata((self.mock_projects, {}, self.mock_threads))

    def tearDown(self):
        set_mock_metadata(None)
        self.store.close()
        self.temp.cleanup()

    def test_project_and_chat_name_resolution(self):
        # Known project resolution
        self.assertEqual(resolve_project_name(str(self.repo)), "Hestia Archive❤️‍🔥")
        self.assertEqual(resolve_project_name("/Users/example/Developer/Sample-Project.git"), "Sample Project")
        
        # Fallback for arbitrary path
        custom = Path("/tmp/my-custom-tool.git")
        self.assertEqual(resolve_project_name(str(custom)), "my-custom-tool")
        
        # Chat title resolution
        self.assertEqual(resolve_chat_title("01a14b97-ef3b-7852-a3d1-6c8d0d752482"), "Create cinematic showcase")
        self.assertEqual(resolve_chat_title(None), "New Session")

    async def test_details_modal_displays_prompt_and_5_hour_reset_info(self):
        # Create a next-reset job with a prompt
        job_id = self.store.add(
            prompt="please continue, usage ran out but its up now",
            repo=str(self.repo),
            cwd=".",
            mode="next-reset",
            reset=int(time.time()) + 7200
        )
        # Update session_id to test chat title resolution in Details
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET session_id=? WHERE id=?", ("01a14b97-ef3b-7852-a3d1-6c8d0d752482", job_id))

        app = QueueApp(self.state, self.config, provider=LiveMockUsage())
        async with app.run_test(size=(120, 45)) as pilot:
            details = Details(job_id)
            app.push_screen(details)
            await pilot.pause()

            # Verify prompt is prominently displayed in TextArea
            prompt_widget = details.query_one('#detail-prompt', TextArea)
            self.assertEqual(prompt_widget.text, "please continue, usage ran out but its up now")
            self.assertTrue(prompt_widget.read_only)

            # Verify overview contains Project title, Chat title, and 5-Hour Reset info
            overview = str(details.query_one('#detail-overview', Static).render())
            self.assertIn("Hestia Archive❤️‍🔥", overview)
            self.assertIn("Create cinematic showcase", overview)
            self.assertIn("5-Hour Reset:", overview)
            self.assertIn("in 1h 59m", overview)
            self.assertIn("Runs automatically after the 5-hour Codex quota resets", overview)

    async def test_queue_view_displays_project_and_chat_names(self):
        job_id = self.store.add(
            prompt="Initial task description",
            repo=str(self.repo),
            cwd=".",
            mode="next-reset",
            reset=int(time.time()) + 3600
        )

        app = QueueApp(self.state, self.config, provider=LiveMockUsage())
        async with app.run_test(size=(120, 45)) as pilot:
            queue_screen = QueueView()
            app.push_screen(queue_screen)
            await pilot.pause()

            table = queue_screen.query_one(DataTable)
            self.assertEqual(table.row_count, 1)
            row_data = [str(cell) for cell in table.get_row(job_id)]
            
            # Verify Project column displays resolved project name 'Hestia Archive❤️‍🔥'
            self.assertIn("Hestia Archive❤️‍🔥", row_data[1])
            # Verify Mode and Reset columns
            self.assertEqual(row_data[5], "next-reset")
            self.assertIn("in 0h", row_data[6])

    async def test_dashboard_live_provider_refreshes_on_startup_and_action(self):
        provider = LiveMockUsage()
        app = QueueApp(self.state, self.config, provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause(0.2)
            # Live provider was checked on mount
            self.assertGreaterEqual(provider.calls, 1)
            
            # Dashboard shows usage percentages and not NOT CHECKED
            five_text = str(app.query_one('#five-value', Static).render())
            self.assertIn("15%", five_text)
            self.assertIn("usage remaining", five_text)
            self.assertNotIn("NOT CHECKED", five_text)
            
            weekly_text = str(app.query_one('#weekly-value', Static).render())
            self.assertIn("25%", weekly_text)
            self.assertNotIn("NOT CHECKED", weekly_text)
            
            # Test pressing 'r' triggers refresh
            calls_before = provider.calls
            await pilot.press('r')
            await pilot.pause(0.1)
            self.assertGreater(provider.calls, calls_before)
