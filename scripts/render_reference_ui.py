"""Export actual Textual renders using empty temporary state and synthetic usage.

Run from the repository: .venv/bin/python scripts/render_reference_ui.py
No account queries, session discovery, watchers, or native schedulers are started.
"""
import asyncio
from pathlib import Path
import tempfile
import time
from codex_queue.tui import QueueApp
from codex_queue.usage import Usage, Window
from codex_queue.navigation import JobWizard
from codex_queue.tui import QueueView, Settings, SecondaryMenu
from codex_queue.extensions_ui import ShortcutHelp, HerdrScreen

class NoLiveProvider:
    live=False
    def read(self): raise AssertionError('Rendering must never query Codex')

async def main():
    output=Path(__file__).resolve().parents[1]/'docs'/'screenshots'
    output.mkdir(parents=True,exist_ok=True)
    for width,height in ((80,24),(100,30),(120,35),(160,45)):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            app=QueueApp(root/'state',root/'config.json',provider=NoLiveProvider())
            async with app.run_test(size=(width,height)) as pilot:
                await pilot.pause()
                app.save_screenshot(str(output/f'reference-dashboard-{width}x{height}.svg'))
                if width==120:
                    now=time.time()
                    app.latest_usage=Usage(Window(100,300,int(now)+13500),Window(37,10080,int(now)+86400),now)
                    app.refresh_dashboard();await pilot.pause()
                    app.save_screenshot(str(output/'reference-dashboard-mock-usage.svg'))
                    for number in (2,3,4,5):
                        await app.action_primary(number);await pilot.pause()
                        app.save_screenshot(str(output/f'reference-tab-{number}-120x35.svg'))
                        await app.action_primary(1);await pilot.pause()
                    for name,view in [('projects',JobWizard()),('queue',QueueView()),('settings',Settings()),('more',SecondaryMenu()),('herdr',HerdrScreen()),('help',ShortcutHelp())]:
                        app.push_screen(view);await pilot.pause()
                        app.save_screenshot(str(output/f'reference-{name}-120x35.svg'))
                        app.pop_screen();await pilot.pause()
                    view=JobWizard();app.push_screen(view);await pilot.pause()
                    view.step=2;view.prompt='Synthetic prompt for visual review.\nNo execution or discovery takes place.'
                    await view.render_step();await pilot.pause()
                    app.save_screenshot(str(output/'reference-prompt-120x35.svg'))
                if width==80:
                    app.query_one('#more-actions').focus();await pilot.pause(.6)
                    app.save_screenshot(str(output/'reference-dashboard-80x24-scrolled.svg'))

if __name__=='__main__':asyncio.run(main())
