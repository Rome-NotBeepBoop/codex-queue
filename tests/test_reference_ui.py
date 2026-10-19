"""Reference layout and input coverage, using isolated local state only."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from textual.containers import VerticalScroll
from textual.widgets import Button, DataTable, Input, OptionList, Static, TextArea
from codex_queue.tui import QueueApp, Form, QueueView, Settings, UsageView, SecondaryMenu, Execution
from codex_queue.ui_components import UsageBar, BrandHeader, COLORS
from codex_queue.usage import Usage, Window
from codex_queue.extensions_ui import Extensions, ShortcutHelp
from codex_queue.scheduler_ui import SchedulerScreen
from codex_queue.navigation import JobWizard


class MockUsage:
    live = False
    calls = 0
    def read(self):
        self.calls += 1
        now = time.time()
        return Usage(Window(100, 300, int(now)+3600), Window(37, 10080, int(now)+86400), now)


class ReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.provider = MockUsage()
    def tearDown(self): self.temp.cleanup()
    def app(self): return QueueApp(self.root/'state', self.root/'config.json', provider=self.provider)

    async def test_unknown_metrics_and_shared_geometry(self):
        app = self.app()
        async with app.run_test(size=(120,35)) as pilot:
            await pilot.pause()
            self.assertEqual(self.provider.calls,0)
            self.assertIn('Unknown',str(app.query_one('#five-value',Static).render()))
            self.assertIsNone(app.query_one('#five-bar',UsageBar).value)
            metrics=list(app.query('.metric'))
            self.assertEqual(len({m.region.y for m in metrics}),1)
            self.assertEqual(len({m.parent for m in metrics}),1)
            quick=app.query_one('#quick-panel');queue=app.query_one('#queue-panel')
            self.assertEqual(quick.region.y,queue.region.y)
            self.assertEqual(quick.region.height,queue.region.height)
            self.assertAlmostEqual(quick.region.width/(quick.region.width+queue.region.width),.56,delta=.02)
            self.assertEqual(app.query_one('#dashboard',VerticalScroll).max_scroll_y,0)
            self.assertIn('NO PENDING JOBS',str(app.query_one('#next-job',Static).render()))
            self.assertEqual(app.focused.id,'nav-dashboard')
            for key in ('five','weekly'):
                self.assertLessEqual(app.query_one('#'+key+'-bar').region.bottom,app.query_one('#account').content_region.bottom)

    async def test_connection_indicator_requires_recent_explicit_check(self):
        app=self.app();app.config['herdr_enabled']=True
        async with app.run_test(size=(120,35)) as pilot:
            header=app.query_one(BrandHeader)
            self.assertIn('NOT CHECKED',str(header.query_one('.brand-state').render()))
            app.herdr_connected=True;app.herdr_checked_at=time.monotonic()
            header.refresh_state()
            self.assertIn('CONNECTED',str(header.query_one('.brand-state').render()))
            app.herdr_checked_at-=121;header.refresh_state()
            self.assertIn('NOT CHECKED',str(header.query_one('.brand-state').render()))
            self.assertEqual(self.provider.calls,0)

    async def test_tabs_keep_header_geometry_and_preserve_prompt_draft(self):
        app=self.app()
        async with app.run_test(size=(120,35)) as pilot:
            await pilot.pause()
            header_region=app.screen.query_one(BrandHeader).region
            nav_region=app.screen.query_one('#primary-nav').region
            await pilot.press('2');await pilot.pause()
            wizard=app.screen
            self.assertIsInstance(wizard,JobWizard)
            wizard.step=2;await wizard.render_step();await pilot.pause()
            editor=wizard.query_one('#prompt-editor',TextArea)
            editor.load_text('Keep this unfinished draft.');editor.focus()
            # Number shortcuts remain text in an editor; mouse tab switching still works.
            await pilot.press('3');self.assertEqual(editor.text,'3Keep this unfinished draft.')
            await pilot.click(wizard.query_one('#nav-queue'));await pilot.pause()
            self.assertIsInstance(app.screen,QueueView)
            for number,kind in ((4,Execution),(5,Settings),(1,type(app.screen_stack[0])),(2,JobWizard)):
                app.screen.query_one('#nav-dashboard',Button).focus()
                await pilot.press(str(number));await pilot.pause()
                self.assertIsInstance(app.screen,kind)
                self.assertEqual(app.screen.query_one(BrandHeader).region,header_region)
                self.assertEqual(app.screen.query_one('#primary-nav').region,nav_region)
                self.assertTrue(app.screen.query_one('#primary-nav Button.active-tab').label.plain.startswith(str(number)))
            self.assertIs(app.screen,wizard)
            self.assertEqual(wizard.query_one('#prompt-editor',TextArea).text,'3Keep this unfinished draft.')
            self.assertEqual(self.provider.calls,0)
            self.assertIsNone(app.controller)

    async def test_mock_usage_draws_colored_tracks_and_reset(self):
        app=self.app()
        async with app.run_test(size=(120,35)) as pilot:
            await app.refresh_usage();await pilot.pause()
            five=app.query_one('#five-bar',UsageBar);weekly=app.query_one('#weekly-bar',UsageBar)
            self.assertEqual(five.value,100);self.assertEqual(weekly.value,37)
            self.assertEqual(five.render().plain,'━'*five.size.width)
            self.assertEqual(five.render().style,COLORS['warning'])
            self.assertEqual(weekly.render().spans[0].start,round(weekly.size.width*.37))
            self.assertIn('0% usage remaining',str(app.query_one('#five-value',Static).render()))
            self.assertIn('AVAILABLE',str(app.query_one('#weekly-value',Static).render()))
            self.assertIn('local',str(app.query_one('#reset-detail',Static).render()))

    async def test_horizontal_keyboard_mouse_and_focus(self):
        app=self.app()
        async with app.run_test(size=(120,35)) as pilot:
            await pilot.press('right');self.assertEqual(app.focused.id,'nav-projects')
            await pilot.press('left');self.assertEqual(app.focused.id,'nav-dashboard')
            await pilot.press('tab');self.assertEqual(app.focused.id,'nav-projects')
            await pilot.press('shift+tab');self.assertEqual(app.focused.id,'nav-dashboard')
            await pilot.press('right','enter');await pilot.pause();self.assertIsInstance(app.screen,JobWizard)
            await pilot.press('escape','3');await pilot.pause();self.assertIsInstance(app.screen,QueueView)
            await pilot.press('escape','5');await pilot.pause();self.assertIsInstance(app.screen,Settings)
            await pilot.press('escape');await pilot.click('#nav-queue');await pilot.pause();self.assertIsInstance(app.screen,QueueView)
            await pilot.press('escape','?');await pilot.pause();self.assertIsInstance(app.screen,ShortcutHelp)
            await pilot.press('escape');self.assertEqual(len(app.screen_stack),1)
            self.assertEqual(self.provider.calls,0)

    async def test_queue_refresh_after_table_removed_during_teardown(self):
        app=self.app()
        async with app.run_test(size=(120,35)) as pilot:
            view=QueueView()
            app.push_screen(view);await pilot.pause()
            await view.query_one(DataTable).remove()
            with patch.object(app.store,'rows',side_effect=AssertionError('Detached table must not refresh')):
                view.refresh_rows()

    async def test_secondary_destinations_reachable_by_keyboard(self):
        app=self.app()
        async with app.run_test(size=(100,30)) as pilot:
            for index,kind in enumerate((QueueView,UsageView,SchedulerScreen,Extensions,Settings)):
                app.query_one('#more-actions',Button).focus();await pilot.press('enter');await pilot.pause()
                self.assertIsInstance(app.screen,SecondaryMenu)
                await pilot.press(*(['down']*index+['enter']));await pilot.pause()
                self.assertIsInstance(app.screen,kind)
                await pilot.press('escape');await pilot.pause()
            self.assertEqual(self.provider.calls,1) # only the explicit Usage screen

    async def test_shortcuts_do_not_steal_input_or_prompt_text(self):
        app=self.app()
        async with app.run_test(size=(100,30)) as pilot:
            app.push_screen(Form(session=True));await pilot.pause()
            app.screen.query_one('#repo',Input).focus()
            await pilot.press('1','2','3','4','5','?')
            self.assertEqual(app.screen.query_one('#repo',Input).value,'12345?')
            editor=app.screen.query_one(TextArea);editor.focus()
            await pilot.press('1','2','3','4','5','?')
            self.assertEqual(editor.text,'12345?')
            self.assertIsInstance(app.screen,Form)
            await pilot.press('escape');self.assertEqual(len(app.screen_stack),1)

    async def test_responsive_layout_and_scroll_to_controls(self):
        app=self.app()
        async with app.run_test(size=(120,35)) as pilot:
            for size in ((80,24),(100,30),(120,35),(160,45)):
                await pilot.resize_terminal(*size);await pilot.pause()
                buttons=list(app.query('#primary-nav Button'))
                self.assertEqual(len({b.region.y for b in buttons}),1)
                self.assertLessEqual(buttons[-1].region.right,size[0])
                quick=app.query_one('#quick-panel');queue=app.query_one('#queue-panel')
                if size[0]<100:self.assertGreater(queue.region.y,quick.region.y)
                else:self.assertEqual(queue.region.y,quick.region.y)
                app.query_one('#more-actions',Button).focus();await pilot.pause(.6)
                more=app.query_one('#more-actions',Button)
                self.assertGreaterEqual(more.region.y,app.query_one('#dashboard').region.y)
                self.assertLessEqual(more.region.bottom,app.query_one('#dashboard').region.bottom)
                self.assertLessEqual(more.region.bottom,queue.content_region.bottom)
                await pilot.press('enter');await pilot.pause();self.assertIsInstance(app.screen,SecondaryMenu)
                await pilot.press('escape');await pilot.pause()

    async def test_five_hour_allowance_remaining_and_edge_cases(self):
        app=self.app()
        async with app.run_test(size=(120,35)) as pilot:
            now=time.time()
            # 0% remaining (100% used)
            app.latest_usage=Usage(Window(100,300,int(now)+3600),Window(37,10080,int(now)+86400),now)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            bar=app.query_one('#five-bar',UsageBar)
            self.assertEqual(str(render),'0% usage remaining')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['warning'])
            self.assertEqual(bar.value,100)
            self.assertEqual(bar.render().plain,'━'*bar.size.width)
            self.assertEqual(bar.render().style,COLORS['warning'])

            # over-exhausted (>100% used)
            app.latest_usage=Usage(Window(115,300,int(now)+3600),Window(37,10080,int(now)+86400),now)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            bar=app.query_one('#five-bar',UsageBar)
            self.assertEqual(str(render),'0% usage remaining')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['warning'])
            self.assertEqual(bar.value,115)
            self.assertEqual(bar.render().plain,'━'*bar.size.width)
            self.assertEqual(bar.render().style,COLORS['warning'])

            # 100% remaining (0% used)
            app.latest_usage=Usage(Window(0,300,int(now)+3600),Window(37,10080,int(now)+86400),now)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            bar=app.query_one('#five-bar',UsageBar)
            self.assertEqual(str(render),'100% usage remaining')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['success'])
            self.assertEqual(bar.value,0)
            self.assertEqual(bar.render().plain,'━'*bar.size.width)
            self.assertEqual(bar.render().spans[0].style,COLORS['border'])

            # 45% remaining (55% used)
            app.latest_usage=Usage(Window(55,300,int(now)+3600),Window(37,10080,int(now)+86400),now)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            bar=app.query_one('#five-bar',UsageBar)
            self.assertEqual(str(render),'45% usage remaining')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['success'])
            self.assertEqual(bar.value,55)
            self.assertEqual(bar.render().spans[0].start,round(bar.size.width*.55))

            # Fractional float percentage (85.5% used -> 14.5% remaining)
            app.latest_usage=Usage(Window(85.5,300,int(now)+3600),Window(37,10080,int(now)+86400),now)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            self.assertEqual(str(render),'14.5% usage remaining')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['success'])

            # Fractional float edge case (99.99% used -> 0.01% remaining)
            app.latest_usage=Usage(Window(99.99,300,int(now)+3600),Window(37,10080,int(now)+86400),now)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            self.assertEqual(str(render),'0.01% usage remaining')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['success'])

            # Negative used percentage clamped to 100%
            app.latest_usage=Usage(Window(-10,300,int(now)+3600),Window(37,10080,int(now)+86400),now)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            self.assertEqual(str(render),'100% usage remaining')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['success'])

            # Stale usage
            app.latest_usage=Usage(Window(100,300,int(now)+3600),Window(37,10080,int(now)+86400),now-130)
            app.refresh_dashboard()
            render=app.query_one('#five-value',Static).render()
            bar=app.query_one('#five-bar',UsageBar)
            self.assertEqual(str(render),'0% STALE')
            self.assertEqual(render.spans[1].style.foreground.hex.lower(),COLORS['warning'])
            self.assertEqual(bar.value,100)
            self.assertIn('Five-hour: 100% used · 0% remaining',app.usage_text())


