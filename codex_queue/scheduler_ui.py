"""Optional scheduler management interface. Native mutations require explicit clicks."""
import asyncio
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from .ui_components import BrandHeader as Header
from textual.widgets import Footer, Label, Select, Input, Button, Static, RichLog
from .scheduler import NativeScheduler
from .store import QueueError


class SchedulerScreen(Screen):
    BINDINGS=[('escape','back','Back')]
    def __init__(self, modes=False):super().__init__();self.modes=modes
    def compose(self):
        yield Header();yield Label('Dashboard → Settings → Execution Mode' if self.modes else 'Dashboard → Scheduler Management',classes='title')
        with VerticalScroll():
            yield Static('',id='scheduler-status',markup=False)
            yield Label('Select execution mode without starting a worker')
            yield Select([('Foreground Watcher (default)','foreground'),('OS Scheduler','os')],value='foreground',allow_blank=False,id='execution-mode')
            yield Button('Apply mode (keeps execution stopped)',id='apply-mode')
            if self.modes:
                yield Button('Open Scheduler Management',id='scheduler-manage')
            else:
                yield from self.scheduler_options()
            yield Static('',id='scheduler-error',markup=False)
            yield Button('Back',id='scheduler-back')
        yield Footer()
    def scheduler_options(self):
            yield Label('OS check interval (seconds)');yield Input(str(self.app.config['poll_interval_seconds']),id='check-interval')
            yield Button('Configure only',id='configure-scheduler')
            yield Input(placeholder='Type INSTALL / ENABLE / UNINSTALL for that action',id='scheduler-confirm')
            with Horizontal(id='actions'):
                for title,key in [('Install disabled','install'),('Enable OS scheduler','enable'),('Disable','disable'),('Uninstall','uninstall'),('Refresh status','status'),('Show logs','logs')]:yield Button(title,id='scheduler-'+key)
            yield Static('Native integration is experimental. Activation requires explicit execution permission and ENABLE. Only this queue’s owned task is managed.',classes='muted')
            yield RichLog(id='scheduler-logs',markup=False,wrap=True)
    async def on_mount(self):
        factory=getattr(self.app,'scheduler_factory',NativeScheduler)
        self.manager=factory(self.app.store.state,self.app.config_file)
        from .execution import control
        self.query_one('#execution-mode',Select).value=control(self.app.store.state)['mode']
        await self.status()
    async def status(self):
        result=await asyncio.to_thread(self.manager.status)
        mode='Foreground Watcher' if result['mode']=='foreground' else 'OS Scheduler'
        status=f"Execution mode: {mode}\nNative scheduler: {result['native']}\nBackground execution: {'permitted' if result['scheduler_enabled'] else 'disabled'}\nCheck interval: {str(result['interval'])+' seconds' if result.get('interval') else 'not configured'}"
        if result.get('error'):status+='\n'+result['error']
        self.query_one('#scheduler-status',Static).update(status)
    async def on_button_pressed(self,event):
        action=event.button.id
        try:
            if action=='scheduler-back':self.action_back();return
            if action=='scheduler-manage':self.app.push_screen(SchedulerScreen());return
            event.button.disabled=True
            if action=='apply-mode':
                selected=self.query_one('#execution-mode',Select).value
                if selected=='foreground':
                    # Existing local watcher already owns its foreground lease.
                    from .execution import control
                    if control(self.app.store.state)['mode']!='foreground' or control(self.app.store.state)['scheduler_enabled']:await asyncio.to_thread(self.manager.foreground)
                else:
                    await self.app.stop_watching();await asyncio.to_thread(self.manager.select_os)
            elif action=='configure-scheduler':await asyncio.to_thread(self.manager.configure,int(self.query_one('#check-interval',Input).value))
            elif action in ('scheduler-install','scheduler-enable','scheduler-uninstall'):
                operation=action.removeprefix('scheduler-')
                if self.query_one('#scheduler-confirm',Input).value!=operation.upper():raise QueueError(f'Type {operation.upper()} to confirm this action')
                await self.app.stop_watching()
                if operation=='enable':await asyncio.to_thread(self.manager.enable,self.app.config['execution_enabled'])
                else:await asyncio.to_thread(getattr(self.manager,operation))
                if operation=='install':
                    from .settings import load
                    self.app.config=load(self.app.config_file)
                self.query_one('#scheduler-confirm',Input).value=''
            elif action=='scheduler-disable':await asyncio.to_thread(self.manager.disable)
            elif action=='scheduler-logs':self.query_one('#scheduler-logs',RichLog).clear();self.query_one('#scheduler-logs',RichLog).write(await asyncio.to_thread(self.manager.logs))
            await self.status();self.app.refresh_dashboard()
            self.query_one('#scheduler-error',Static).update('')
        except Exception as error:self.query_one('#scheduler-error',Static).update(str(error))
        finally:event.button.disabled=False
    def action_back(self):self.app.pop_screen()
