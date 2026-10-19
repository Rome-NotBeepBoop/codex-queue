"""Textual interface. No monitoring, history scan, or execution begins on startup."""
import asyncio
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import queue
import time
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, RichLog, Select, Static, Switch, TextArea
from .app_server import AppServer
from .cli import Fixture
from .foreground import Foreground
from .settings import load, save, is_cli_mode
from .store import Store, QueueError
from .paths import config_path
from .ui_components import BrandHeader as Header, PrimaryNav, AllowanceMetric, UsageBar, DashboardFooter, PALETTE_CSS, COLORS


def stamp(value):
    if value is None or value == '': return 'Unknown'
    try:
        from datetime import timezone
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().strftime('%a %H:%M:%S %Z')
    except Exception:
        try:
            from datetime import timezone
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime('%a %H:%M:%S UTC')
        except Exception:
            return 'Unknown'



class Form(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Back')]
    def __init__(self, session=False):
        super().__init__(); self.session = session

    def compose(self):
        with VerticalScroll(id='dialog'):
            yield Label('Resume a Codex conversation' if self.session else 'Add a new prompt', classes='title')
            yield Label('Repository'); yield Input(self.app.config.get('default_repo', ''), id='repo')
            yield Label('Working directory relative to repository'); yield Input('.', id='cwd')
            if self.session:
                yield Label('Known queue sessions (manual ID below is also supported)')
                known = self.app.store.db.execute('SELECT DISTINCT session_id,worktree FROM attempts WHERE session_id IS NOT NULL').fetchall()
                yield Select([(f'{r[0]} · {Path(r[1]).name}', r[0]) for r in known], prompt='Select a known session', id='known')
                yield Label('Session UUID'); yield Input(id='session')
                yield Label('Optional continuation prompt'); yield TextArea(id='continuation')
                yield Label('I have stopped the original session and exclusively own this checkout.')
                yield Switch(False, id='ownership')
            else:
                yield Label('Prompt file'); yield Input(id='prompt')
            yield Label('Scheduling mode')
            yield Select([(v, v) for v in ('manual', 'immediate', 'next-reset')], value=self.app.config.get('default_mode', 'manual'), id='mode', allow_blank=False)
            yield Static('Next-reset obtains live usage only when you submit (or uses --fixture).', classes='muted')
            yield Static('', id='error', markup=False)
            with Horizontal():
                yield Button('Queue job', id='submit', variant='primary')
                yield Button('Back', id='back')

    def on_select_changed(self, event):
        if event.select.id == 'known' and event.value is not Select.BLANK:
            self.query_one('#session', Input).value = str(event.value)

    async def on_button_pressed(self, event):
        if event.button.id == 'back': self.dismiss(); return
        if event.button.id != 'submit': return
        values = {name: self.query_one('#' + name, Input).value for name in ('repo', 'cwd')}
        values['mode'] = self.query_one('#mode', Select).value
        if self.session:
            values.update(session_id=self.query_one('#session', Input).value,
                          prompt=self.query_one('#continuation', TextArea).text,
                          ownership=self.query_one('#ownership', Switch).value)
        else:
            values['prompt_file'] = self.query_one('#prompt', Input).value
        try:
            event.button.disabled = True
            job = await asyncio.to_thread(self.app.enqueue, values, self.session)
            self.app.notify(f'Queued {job}'); self.dismiss(); self.app.refresh_dashboard()
        except Exception as error:
            self.query_one('#error', Static).update(str(error))
        finally:
            event.button.disabled = False


class Details(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Back')]
    def __init__(self, job): super().__init__(); self.job = job
    def compose(self):
        row = self.app.store.get(self.job)
        from .projects import resolve_project_name, resolve_chat_title
        p_name = resolve_project_name(row['repo'])
        chat_title = resolve_chat_title(row.get('session_id')) if row.get('session_id') else ''
        with VerticalScroll(id='dialog'):
            yield Label(f"Job {row['id']} · {row['kind'].upper()}", classes='title')
            
            lines = [
                f"Project: 📁 {p_name}  ({Path(row['repo']).name})",
                f"Repository: {row['repo']}",
            ]
            if row.get('cwd') and row['cwd'] != '.':
                lines.append(f"Directory: {row['cwd']}")
            if row.get('session_id'):
                lines.append(f"Chat: 💬 {chat_title}")
                lines.append(f"Session UUID: {row['session_id']}")
            lines.append(f"Status: {row['status'].upper()} · Mode: {row['mode']}")
            
            if row['mode'] == 'next-reset' or row.get('saved_reset'):
                reset_ts = row.get('saved_reset')
                if reset_ts:
                    secs = max(0, int(reset_ts - time.time()))
                    if secs > 0:
                        lines.append(f"5-Hour Reset: {stamp(reset_ts)} (in {secs//3600}h {(secs%3600)//60}m)")
                        lines.append("Schedule: Runs automatically after the 5-hour Codex quota resets.")
                    else:
                        lines.append(f"5-Hour Reset: {stamp(reset_ts)} (Reset time reached — eligible)")
                else:
                    lines.append("5-Hour Reset: Waiting for next quota reset timestamp")
                    
            if row.get('reason'):
                lines.append(f"Reason: {row['reason']}")
            
            yield Static("\n".join(lines), id='detail-overview', markup=False)
            
            prompt_header = "PROMPT JOB TO SEND AFTER 5-HOUR RESET:" if row['mode'] == 'next-reset' else "PROMPT JOB TO SEND:"
            with Horizontal(classes='detail-prompt-header'):
                yield Label(prompt_header, classes='section-label')
                yield Button('✏️ Edit Prompt', id='detail-edit-prompt', disabled=row['status'] in ('running', 'completed'))
                yield Button('💾 Save Prompt', id='detail-save-prompt', classes='hidden')
            yield TextArea(row.get('prompt', '') or '(Empty prompt)', read_only=True, id='detail-prompt')
            
            yield Label("Execution Backend:", classes='metric-label')
            yield Select([('Integration default','default'),('Direct Codex','direct'),('Herdr','herdr')], value=row['backend'], allow_blank=False, id='detail-backend')
            yield Button('Save backend choice', id='detail-save-backend', disabled=row['status']=='running')
            yield Label("Attempt Logs & History:", classes='section-label')
            yield RichLog(id='detail-log', wrap=True, markup=False)
            with Horizontal(classes='action-row'):
                yield Button('Back', id='back', variant='primary')
                yield Button('Open in Herdr', id='detail-herdr')
                yield Button('Interrupt managed job', id='detail-interrupt', variant='warning')
    def on_mount(self):
        log = self.query_one('#detail-log', RichLog)
        for attempt in self.app.store.db.execute('SELECT * FROM attempts WHERE job=? ORDER BY number', (self.job,)):
            log.write(f"Attempt {attempt['number']}: {attempt['status']} · {stamp(attempt['started'])}\n{attempt['worktree']}")
            if attempt['log'] and Path(attempt['log']).exists():
                with Path(attempt['log']).open('rb') as source:
                    source.seek(0, 2); source.seek(max(0, source.tell()-200000))
                    log.write(source.read().decode(errors='replace'))
    async def on_button_pressed(self, event):
        from .extensions_ui import Confirmation
        from .herdr import HerdrBackend, interrupt
        if event.button.id == 'back': self.dismiss(); return
        try:
            if event.button.id == 'detail-edit-prompt':
                editor = self.query_one('#detail-prompt', TextArea)
                editor.read_only = False
                editor.focus()
                self.query_one('#detail-edit-prompt', Button).add_class('hidden')
                self.query_one('#detail-save-prompt', Button).remove_class('hidden')
                self.app.notify('Editing prompt enabled. Click Save Prompt when done.')
            elif event.button.id == 'detail-save-prompt':
                editor = self.query_one('#detail-prompt', TextArea)
                new_prompt = editor.text
                with self.app.store.db:
                    self.app.store.db.execute("UPDATE jobs SET prompt=?, updated=? WHERE id=?", (new_prompt, time.time(), self.job))
                editor.read_only = True
                self.query_one('#detail-save-prompt', Button).add_class('hidden')
                self.query_one('#detail-edit-prompt', Button).remove_class('hidden')
                self.app.notify('Prompt updated successfully!')
            elif event.button.id == 'detail-save-backend':
                row=self.app.store.get(self.job)
                if row['status']=='running':raise QueueError('Cannot change the backend of a running or unknown job.')
                with self.app.store.db:self.app.store.db.execute('UPDATE jobs SET backend=? WHERE id=?',(self.query_one('#detail-backend',Select).value,self.job))
                self.app.notify('Backend choice saved; no job started.')
            elif event.button.id == 'detail-herdr':
                await asyncio.to_thread(HerdrBackend(self.app.store, self.app.config).focus, self.job)
            elif event.button.id == 'detail-interrupt':
                async def confirmed():
                    interrupt(self.app.store, self.job)
                    self.app.notify('Interruption requested; wait for the runner receipt.')
                self.app.push_screen(Confirmation('Interrupt only this managed Codex job? Partial work will be preserved.', confirmed))
        except QueueError as error: self.app.notify(str(error), severity='warning')


class QueueView(Screen):
    BINDINGS = [('escape', 'back', 'Back')]
    def __init__(self, history=False):
        super().__init__(); self.history = history; self._refresh_timer = None
    def compose(self):
        yield Header()
        yield Label('Execution history' if self.history else 'Queue', classes='title')
        yield DataTable(id='jobs', cursor_type='row', zebra_stripes=True)
        with Horizontal(id='actions'):
            for name in ('Details', 'Add', 'Pause', 'Resume', 'Cancel', 'Retry', 'Back'):
                yield Button(name, id=name.lower())
        yield Static('', id='queue-error', markup=False)
        yield Footer()
    def on_mount(self):
        self.query_one(DataTable).add_columns('Job', 'Project', 'Chat / Session', 'Type', 'Status', 'Mode', 'Reset / eligibility')
        self.refresh_rows()
        self._refresh_timer = self.set_interval(2, self.refresh_rows)
    def on_unmount(self):
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None
    def refresh_rows(self):
        # A pending timer callback may run while the screen's children are removed.
        table = next(iter(self.query(DataTable)), None)
        if table is None:
            return
        selected = table.cursor_row; table.clear()
        from .projects import resolve_project_name, resolve_chat_title
        for row in self.app.store.rows():
            if self.history and not row['attempts']: continue
            status=Text(row['status'],style={'running':'bold #83c9c1','failed':'#e3a18b','queued':'#c9b384','paused':'#c9b384','completed':'#83c9c1'}.get(row['status'],'#9eafc0'))
            p_name = resolve_project_name(row['repo'])
            chat_name = resolve_chat_title(row.get('session_id')) if row.get('session_id') else (row['prompt'][:25] if row.get('prompt') else 'New Prompt')
            
            if row['mode'] == 'next-reset':
                if row.get('saved_reset'):
                    secs = max(0, int(row['saved_reset'] - time.time()))
                    reset_label = f"in {secs//3600}h {(secs%3600)//60}m" if secs > 0 else "Reset reached"
                else:
                    reset_label = "Next reset"
            else:
                reset_label = row['mode']
                
            table.add_row(row['id'], p_name, chat_name, row['kind'], status, row['mode'], reset_label, key=row['id'])
        if table.row_count: table.move_cursor(row=min(selected, table.row_count-1))
    def chosen(self):
        table = self.query_one(DataTable)
        if not table.row_count: raise QueueError('No job selected.')
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
    def on_data_table_row_selected(self, event): self.app.push_screen(Details(str(event.row_key.value)))

    async def on_button_pressed(self, event):
        action = event.button.id
        try:
            if action=='back': self.action_back()
            elif action=='add':
                from .navigation import JobWizard
                self.app.push_screen(JobWizard())
            elif action=='details': self.app.push_screen(Details(self.chosen()))
            else:
                job=self.chosen();row=self.app.store.get(job)
                managed=self.app.store.db.execute('SELECT 1 FROM backend_runs WHERE job=? AND number=?',(job,row['attempts'])).fetchone()
                if row['status']=='running' and managed and action in ('pause','cancel'):
                    from .extensions_ui import Confirmation
                    async def confirmed():
                        self.app.store.transition(job,action);self.refresh_rows()
                    self.app.push_screen(Confirmation(f'{action.title()} this managed running job? This interrupts Codex and preserves partial work.',confirmed))
                else:self.app.store.transition(job,action);self.refresh_rows()
        except QueueError as error: self.query_one('#queue-error', Static).update(str(error))
    def action_back(self): self.app.pop_screen(); self.app.refresh_dashboard()


class Settings(Screen):
    BINDINGS = [('escape', 'dismiss', 'Back')]
    def dismiss(self): self.app.pop_screen(); self.app.refresh_dashboard()
    def action_dismiss(self): self.dismiss()
    def compose(self):
        yield Header()
        with VerticalScroll(id='settings-body'):
            yield Label('Settings', classes='title')
            yield Button('Execution Mode',id='execution-settings')
            for name, title in [('codex','Codex executable'), ('poll_interval_seconds','Polling interval (10–3600 seconds)'), ('default_repo','Default repository')]:
                yield Label(title); yield Input(str(self.app.config[name]), id=name)
            yield Select([(v,v) for v in ('manual','immediate','next-reset')], value=self.app.config['default_mode'], id='default_mode', allow_blank=False)
            yield Label('Interface mode (TUI vs CLI)')
            current_mode = 'cli' if is_cli_mode(self.app.config) else 'tui'
            yield Select([(v, v) for v in ('tui', 'cli')], value=current_mode, id='interface_mode', allow_blank=False)
            yield Label('CLI mode toggle')
            yield Switch(current_mode == 'cli', id='cli_mode')
            yield Label('Permit real execution (does not start monitoring)')
            yield Switch(self.app.config['execution_enabled'], id='permission')
            yield Input(placeholder='Type ENABLE to enable real execution', id='confirm')
            yield Static(f'Config: {self.app.config_file}\nDatabase: {self.app.store.state / "queue.sqlite"}\nLogs: {self.app.store.state / "logs"}', markup=False)
            yield Static('',id='settings-error',markup=False)
            with Horizontal(): yield Button('Save',id='save',variant='primary'); yield Button('Back',id='back')
    def on_switch_changed(self, event: Switch.Changed):
        if event.switch.id == 'cli_mode':
            target = 'cli' if event.value else 'tui'
            sel = self.query_one('#interface_mode', Select)
            if sel.value != target:
                sel.value = target
    def on_select_changed(self, event: Select.Changed):
        if event.select.id == 'interface_mode':
            target = (event.value == 'cli')
            sw = self.query_one('#cli_mode', Switch)
            if sw.value != target:
                sw.value = target
    async def on_button_pressed(self,event):
        if event.button.id=='back': self.dismiss(); return
        if event.button.id=='execution-settings':
            from .scheduler_ui import SchedulerScreen
            self.app.push_screen(SchedulerScreen(modes=True)); return
        try:
            if self.app.controller and self.app.controller.thread.is_alive(): raise QueueError('Stop monitoring before changing settings.')
            values=dict(self.app.config)
            for name in ('codex','default_repo'): values[name]=self.query_one('#'+name,Input).value
            values['poll_interval_seconds']=int(self.query_one('#poll_interval_seconds',Input).value)
            values['default_mode']=self.query_one('#default_mode',Select).value
            mode_choice = self.query_one('#interface_mode', Select).value
            switch_choice = self.query_one('#cli_mode', Switch).value
            cli_active = (mode_choice == 'cli') if mode_choice != ('cli' if is_cli_mode(self.app.config) else 'tui') else switch_choice
            values['interface_mode'] = 'cli' if cli_active else 'tui'
            values['cli_mode'] = cli_active
            values['execution_enabled']=self.query_one('#permission',Switch).value
            if values['execution_enabled'] and not self.app.config['execution_enabled'] and self.query_one('#confirm',Input).value!='ENABLE':
                raise QueueError('Type ENABLE to explicitly permit execution.')
            save(self.app.config_file,values); self.app.config=values; self.app.provider=self.app.make_provider(); self.dismiss(); self.app.refresh_dashboard()
        except (ValueError,QueueError,OSError) as error: self.query_one('#settings-error',Static).update(str(error))



class Execution(Screen):
    BINDINGS = [('escape','back','Dashboard'), ('s','stop','Stop monitoring')]
    def compose(self):
        yield Header(); yield Label('Foreground watcher', classes='title')
        yield Static('Monitoring stopped — press Start monitoring explicitly.', id='execution-status', markup=False)
        yield Static('Usage unknown', id='execution-usage', markup=False)
        yield RichLog(id='execution-log', wrap=True, max_lines=3000, markup=False)
        with Horizontal(): yield Button('Start monitoring',id='start-watch'); yield Button('Stop monitoring', id='stop', variant='warning'); yield Button('Dashboard',id='back')
        yield Footer()
    async def action_stop(self): await self.app.stop_watching()
    def action_back(self): self.app.pop_screen(); self.app.refresh_dashboard()
    async def on_button_pressed(self,event):
        if event.button.id=='start-watch':await self.app.start_watching()
        elif event.button.id=='stop': await self.action_stop()
        else: self.action_back()


class UsageView(Screen):
    BINDINGS=[('escape','back','Back')]
    def compose(self):
        yield Header();yield Label('Dashboard › Usage & Reset Times',classes='title')
        yield Static('',id='usage-detail',markup=False)
        yield Button('Refresh usage',id='usage-refresh');yield Button('Back',id='usage-back');yield Footer()
    async def on_mount(self):await self.refresh_usage_panel()
    async def refresh_usage_panel(self):
        await self.app.refresh_usage()
        self.query_one('#usage-detail',Static).update(self.app.usage_text())
    async def on_button_pressed(self,event):
        if event.button.id=='usage-back':self.action_back()
        else:await self.refresh_usage_panel()
    def action_back(self):self.app.pop_screen()


class SecondaryMenu(Screen):
    BINDINGS=[('escape','back','Back')]
    DESTINATIONS=[('Execution History',5),('Usage & Reset Times',6),('Scheduler Management',7),('Extensions',8),('Settings',9),('Exit',10)]
    def compose(self):
        yield Header();yield Label('Dashboard › More actions',classes='title')
        yield OptionList(*(title for title,_ in self.DESTINATIONS),id='secondary-menu')
        yield Button('Back',id='secondary-back');yield Footer()
    def on_mount(self):self.query_one(OptionList).focus()
    async def on_option_list_option_selected(self,event):
        event.stop();index=self.DESTINATIONS[event.option_index][1]
        self.app.pop_screen();await self.app.open_destination(index)
    def on_button_pressed(self,event):self.action_back()
    def action_back(self):self.app.pop_screen()


class QueueApp(App):
    TITLE='CODEX QUEUE · 0.2.0'
    BINDINGS=[Binding('ctrl+c','graceful_quit','Exit',priority=True),Binding('escape','back','Back'), Binding('question_mark','help','Keys'), Binding('r','refresh_usage','Refresh Usage'), *[Binding(str(n),f'primary({n})',str(n),show=False) for n in range(1,6)]]
    CSS=PALETTE_CSS+'''
    Horizontal { height: auto; }
    Screen { background: $cq-bg; color: $cq-text; scrollbar-color: $cq-border; scrollbar-color-hover: $cq-soft; scrollbar-color-active: $cq-accent; scrollbar-background: $cq-bg; scrollbar-background-hover: $cq-bg; scrollbar-background-active: $cq-bg; }
    VerticalScroll, OptionList, RichLog, TextArea { scrollbar-color: $cq-border; scrollbar-color-hover: $cq-soft; scrollbar-color-active: $cq-accent; scrollbar-background: $cq-bg; scrollbar-background-hover: $cq-bg; scrollbar-background-active: $cq-bg; }
    BrandHeader { height: 6; background: $cq-bg; dock: top; }
    .brand-row { height: 2; padding: 1 2 0 2; }
    .brand-name { width: auto; height: 1; }
    .brand-version { width: auto; height: 1; margin-left: 2; color: $cq-muted; }
    .brand-spacer { width: 1fr; }
    .brand-state { width: 24; height: 1; text-align: right; color: $cq-success; }
    PrimaryNav { height: 4; padding: 0 2; border-bottom: solid $cq-border; overflow-x: auto; }
    PrimaryNav Button { height: 3; min-width: 0; width: auto; margin: 0 1 0 0; padding: 0 1; border: solid transparent; background: $cq-bg; color: $cq-muted; text-style: none; }
    PrimaryNav Button.active-tab { background: $cq-surface; color: $cq-accent; text-style: bold; border: solid $cq-soft; }
    PrimaryNav Button:focus { background: $cq-surface; color: $cq-text; text-style: bold; border: solid $cq-accent; }
    Footer, DashboardFooter { background: $cq-bg; color: $cq-muted; border-top: solid $cq-border; }
    DashboardFooter { height: 2; padding: 0 2; }
    #worker-state { width: 23; height: 1; color: $cq-soft; }
    #dashboard-hints { width: 1fr; height: 1; text-align: right; color: $cq-muted; }
    #dashboard { padding: 0 2; }
    .eyebrow { height: 1; color: $cq-muted; margin: 0 0 1 0; }
    #dashboard > .section-label { margin-bottom: 0; }
    .section-label { height: 1; color: $cq-accent; text-style: bold; margin: 0 0 1 0; }
    .panel { background: $cq-panel; border: solid $cq-border; }
    #account { height: 7; padding: 1 2; margin-bottom: 1; }
    .metric { width: 1fr; height: auto; padding-right: 3; }
    .metric:last-child { padding-right: 0; }
    .metric-label { height: 1; color: $cq-muted; }
    UsageBar { height: 1; width: 100%; }
    #five-value, #weekly-value, #reset-value { height: 1; }
    #reset-value { text-style: bold; }
    .metadata { height: auto; color: $cq-muted; }
    #main-panels { height: 16; }
    #quick-panel { width: 56%; padding: 1 2; margin-right: 1; }
    #queue-panel { width: 44%; padding: 1 2; }
    #quick-actions { height: 10; padding: 0; }
    #quick-actions > .option-list--option { padding: 1 1; }
    #quick-actions:focus { border: none; }
    #quick-actions > .option-list--option-highlighted { background: $cq-panel; color: $cq-accent; }
    #queue-panel .section-label { margin: 0; }
    .state-row { height: 2; border-bottom: solid $cq-border; }
    .state-row.running-row { display: none; }
    .state-row .metadata { width: 1fr; }
    .state-value { width: auto; min-width: 9; height: 1; text-align: right; }
    #next-job { height: 3; margin-top: 0; color: $cq-muted; }
    #queue-panel .section-label { margin-bottom: 1; }
    #main-panels { height: 17; }
    #quick-panel, #queue-panel { padding: 1 2 0 2; }
    #account { height: 7; }
    .eyebrow { margin-bottom: 0; }
    #queue-links { height: 3; margin-top: 0; }
    #queue-links Button { min-width: 0; margin: 0 1 0 0; padding: 0 1; }
    .title { text-style: bold; color: $cq-accent; margin: 0 2 1 2; }
    .muted { color: $cq-muted; margin: 0 1; }
    OptionList { background: $cq-panel; border: none; }
    OptionList > .option-list--option { padding: 0 1; color: $cq-text; }
    OptionList > .option-list--option-highlighted { background: $cq-surface; color: $cq-accent; }
    OptionList > .option-list--option-hover { background: $cq-surface; }
    OptionList:focus { border-left: solid $cq-accent; }
    Input, TextArea, Select { background: $cq-surface; border: solid $cq-border; margin-bottom: 1; }
    Input:focus, TextArea:focus, Select:focus { border: solid $cq-accent; }
    Button { background: $cq-panel; color: $cq-soft; border: solid $cq-border; margin: 0 1 1 0; min-width: 10; }
    Button:focus { color: $cq-text; border: solid $cq-accent; text-style: bold; }
    Button.-primary { background: $cq-surface; color: $cq-accent; }
    Button.-warning { background: $cq-panel; color: $cq-warning; }
    Button:disabled { color: $cq-muted; }
    #wizard, #integration-body, #settings-body { padding: 0 2; }
    #wizard-error, #herdr-error, #error, #settings-error, #queue-error { color: $cq-error; height: auto; }
    .action-row, #actions { overflow-x: auto; }
    #projects { height: auto; max-height: 24; margin: 1 0; }
    #projects > .option-list--option { padding: 1 2; }
    #dialog { width: 80%; max-width: 100; height: 90%; border: solid $cq-border; background: $cq-panel; padding: 1 2; }
    #detail-overview { background: $cq-surface; padding: 1 2; margin-bottom: 1; border: solid $cq-border; }
    #detail-prompt { height: 8; margin-bottom: 1; }
    .account-header-row { height: 1; margin: 0; }
    .account-header-row Label { margin: 0; }
    .account-refresh-btn { min-width: 0; height: 1; margin: 0; padding: 0 1; background: transparent; border: none; color: $cq-accent; text-style: bold; }
    .account-refresh-btn:hover { background: $cq-surface; }
    .detail-prompt-header { height: 2; margin-bottom: 0; }
    .detail-prompt-header Button { min-width: 0; height: 1; padding: 0 1; margin: 0; }
    .hidden { display: none; }
    #project-summary { background: $cq-surface; padding: 1 2; margin: 1 0; border-left: solid $cq-accent; }
    .projects-title { text-style: bold; color: $cq-text; margin-top: 1; }
    .add-repo-row { height: 3; margin-top: 1; }
    .add-repo-row Input { width: 1fr; margin-right: 1; }
    ModalScreen { align: center middle; background: #00000080; }
    TextArea { height: 6; }
    #jobs { height: 1fr; }
    #execution-status, #execution-usage, #usage-detail { padding: 0 2; height: auto; }
    RichLog { border: solid $cq-border; margin: 0 2; height: 1fr; }
    .compact #dashboard { padding: 0 1; }
    .compact PrimaryNav { padding: 0 1; }
    .compact PrimaryNav Button { margin-right: 0; }
    .compact #main-panels { layout: vertical; height: auto; }
    .compact #quick-panel, .compact #queue-panel { width: 100%; height: 14; margin: 0 0 1 0; }
    .compact #queue-panel { height: 18; }
    .compact #dialog { width: 96%; }
    .compact #projects { max-height: 12; }
    .tiny .brand-state { width: 19; }
    .tiny .brand-row { padding: 1 1 0 1; }
    .tiny .brand-version { display: none; }
    .tiny #account { height: auto; layout: vertical; }
    .tiny .metric { width: 100%; margin-bottom: 1; }
    .tiny #dashboard-hints { display: none; }
    '''
    MENU=['Dashboard','Projects & Conversations','Add Job','Watch Queued Jobs','Queue Management','Execution History','Usage & Reset Times','Scheduler Management','Extensions','Settings','Exit']
    def __init__(self,state,config=None,fixture=None,provider=None):
        super().__init__(); self.state_path=state; self.config_file=Path(config or config_path()).expanduser()
        self.config=load(self.config_file); self.fixture=fixture; self.provider=provider or self.make_provider()
        self.active_primary=1; self.primary_pages={}; self.store=None; self.controller=None; self.latest_usage=None; self.usage_error='Not refreshed — no Codex process started.'; self.last_refresh=None; self.last_result={}; self.quit_requested=False
    def make_provider(self):
        return Fixture(self.fixture,self.config['bucket']) if self.fixture else AppServer(self.config['codex'],self.config['bucket'])
    def compose(self):
        yield Header()
        with VerticalScroll(id='dashboard'):
            yield Label('CODEX QUEUE / LOCAL',classes='eyebrow')
            with Horizontal(classes='account-header-row'):
                yield Label('ACCOUNT / OVERVIEW',classes='section-label')
                yield Button('↻ Refresh (r)',id='action-refresh-usage',classes='account-refresh-btn')
            with Horizontal(id='account',classes='panel'):
                yield AllowanceMetric('5–HOUR ALLOWANCE','five',classes='metric')
                yield AllowanceMetric('WEEKLY ALLOWANCE','weekly',classes='metric')
                with Vertical(classes='metric',id='reset-metric'):
                    yield Label('NEXT RESET',classes='metric-label')
                    yield Static('Unknown',id='reset-value',markup=False)
                    yield Static('Not checked',id='reset-detail',classes='metadata',markup=False)
            with Horizontal(id='main-panels'):
                with Vertical(id='quick-panel',classes='panel'):
                    yield Label('QUICK ACTIONS',classes='section-label')
                    entries=[]
                    for label,description in [('Continue a session','Select a project and an existing chat'),('New prompt','Start a fresh Codex conversation'),('Watch queued jobs','Manual foreground monitoring')]:
                        entry=Text('› '+label,style=COLORS['text']);entry.append('\n  '+description+'\n',style=COLORS['muted']);entries.append(entry)
                    yield OptionList(*entries,id='quick-actions')
                with Vertical(id='queue-panel',classes='panel'):
                    yield Label('QUEUE STATE',classes='section-label')
                    for name in ('waiting','running','watcher','scheduler'):
                        with Horizontal(classes='state-row'+(' running-row' if name=='running' else '')):
                            yield Static({'waiting':'Waiting','running':'Running','watcher':'Foreground worker','scheduler':'OS scheduler'}[name],classes='metadata')
                            yield Static('',id='state-'+name,classes='state-value',markup=False)
                    yield Static('',id='next-job',markup=False)
                    with Horizontal(id='queue-links'):
                        yield Button('View queue →',id='view-queue')
                        yield Button('More…',id='more-actions')
        yield DashboardFooter(id='dashboard-footer')
    def on_mount(self):
        self.store=Store(self.state_path)
        try:
            cached=self.store.db.execute("SELECT value FROM metadata WHERE key='latest_usage'").fetchone()
            if cached:
                cdata=json.loads(cached[0])
                from .usage import Usage, Window
                self.latest_usage=Usage(
                    Window(**cdata['five']),
                    Window(**cdata['weekly']),
                    cdata['observed']
                )
                self.last_refresh=cdata.get('last_refresh',self.latest_usage.observed)
                self.usage_error='Cached usage loaded; updating live...'
        except Exception:
            pass
        self.refresh_dashboard()
        self.query_one('#nav-dashboard',Button).focus()
        self.set_interval(.25,self.consume_events)
        self.set_interval(2,self.refresh_dashboard)
        if getattr(self.provider, 'live', False):
            asyncio.create_task(self.refresh_usage())
    def on_resize(self, event):
        self.set_class(event.size.width < 100, 'compact')
        self.set_class(event.size.width < 60, 'tiny')
        hints=list(self.screen_stack[0].query('#dashboard-hints'))
        if hints:hints[0].update('r Refresh · ←→ Tabs · Tab Focus · ? Help' if event.size.width<100 else 'r Refresh Usage · ↑↓ Select · Enter Open · Esc Back · 1–5 Tabs · ? Help')

    async def action_refresh_usage(self):
        await self.refresh_usage()
        self.notify('Codex usage updated.', timeout=2)

    def usage_text(self):
        usage=self.latest_usage
        if not usage: return 'ALLOWANCE · not checked\nFive-hour Unknown    Weekly Unknown\nNext reset Unknown · '+self.usage_error
        stale=time.time()-usage.observed>120
        five_rem=max(0,min(100,round(100-usage.five.used,4)))
        week_rem=max(0,min(100,round(100-usage.weekly.used,4)))
        return (f'ALLOWANCE · {"stale / blocked" if stale else "Connected"}\n'
                f'Five-hour: {usage.five.used:g}% used · {five_rem:g}% remaining\n'
                f'Weekly: {usage.weekly.used:g}% used · {week_rem:g}% remaining\n'
                f'Next reset: {stamp(usage.five.reset)}\nLast successful refresh: {stamp(self.last_refresh)}\n'
                f'{self.usage_error}')
    def refresh_dashboard(self):
        if not self.store or not self.screen_stack: return
        screen=self.screen_stack[0]
        if not list(screen.query('#five-value')): return
        from .herdr import reconcile
        from .execution import control
        reconcile(self.store)
        rows=self.store.rows();counts=Counter(row['status'] for row in rows)
        usage=self.latest_usage
        stale=bool(usage and time.time()-usage.observed>120)
        for key,window in [('five',usage.five if usage else None),('weekly',usage.weekly if usage else None)]:
            if window:
                if key=='five':
                    remaining=max(0,min(100,round(100-window.used,4)))
                    state='STALE' if stale else 'usage remaining'
                    value=Text(f'{remaining:g}% ',style='bold '+COLORS['text'])
                    value.append(state,style=COLORS['warning'] if stale or remaining<=0 else COLORS['success'])
                else:
                    state='STALE' if stale else 'EXHAUSTED' if window.used>=100 else 'AVAILABLE'
                    value=Text(f'{window.used:g}% ',style='bold '+COLORS['text'])
                    value.append(state,style=COLORS['warning'] if stale or window.used>=100 else COLORS['success'])
            else:
                if 'Checking' in self.usage_error:
                    value=Text('● ',style=COLORS['accent'])+Text('CHECKING...',style='bold '+COLORS['text'])
                elif self.usage_error and 'Not refreshed' not in self.usage_error:
                    value=Text('Unknown ',style='bold '+COLORS['text'])+Text('RETRY (r)',style=COLORS['warning'])
                else:
                    value=Text('Unknown ',style='bold '+COLORS['text'])+Text('NOT CHECKED',style=COLORS['muted'])
            screen.query_one('#'+key+'-value',Static).update(value)
            screen.query_one('#'+key+'-bar',UsageBar).set_value(window.used if window else None)
        if usage:
            try:
                from datetime import timezone
                reset=datetime.fromtimestamp(usage.five.reset, tz=timezone.utc).astimezone()
            except Exception:
                try:
                    from datetime import timezone
                    reset=datetime.fromtimestamp(usage.five.reset, tz=timezone.utc)
                except Exception:
                    reset=None
            if reset:
                screen.query_one('#reset-value',Static).update(f'{reset:%H:%M}  {reset:%Z}')
            else:
                screen.query_one('#reset-value',Static).update('Unknown')
            seconds=max(0,int(usage.five.reset-time.time()))
            screen.query_one('#reset-detail',Static).update('Refresh required' if stale or not seconds else f'in {seconds//3600}h {(seconds%3600)//60}m · local')
        else:
            screen.query_one('#reset-value',Static).update('Unknown')
            if 'Checking' in self.usage_error:
                detail = 'Checking Codex...'
            elif self.usage_error and 'Not refreshed' not in self.usage_error:
                detail = 'Failed · press r to retry'
            else:
                detail = 'Local · not checked'
            screen.query_one('#reset-detail',Static).update(detail)
        watching=bool(self.controller and self.controller.thread.is_alive())
        screen.query_one('#state-waiting',Static).update(f"{counts['queued']} job"+('s' if counts['queued']!=1 else ''))
        screen.query_one('#state-running',Static).update(str(counts['running']))
        screen.query_one('#state-watcher',Static).update('WATCHING' if watching else 'STOPPED')
        scheduler=control(self.store.state)
        screen.query_one('#state-scheduler',Static).update('PERMITTED' if scheduler['scheduler_enabled'] else 'DISABLED')
        screen.query_one('#state-scheduler',Static).tooltip='Local execution permission. Open Scheduler Management for checked native status.'
        waiting=next((r for r in rows if r['status']=='queued'),None)
        if waiting:
            from .projects import resolve_project_name
            p_name = resolve_project_name(waiting['repo'])
            p_prompt = (waiting.get('prompt') or '').splitlines()[0][:35] if waiting.get('prompt') else waiting['id']
            next_text = f"{waiting['mode'].upper()}\n📁 {p_name}\n💬 {p_prompt}"
        else:
            next_text = 'NO PENDING JOBS\nAdd a prompt to get started'
        if list(screen.query('#next-job')):
            screen.query_one('#next-job',Static).update(next_text)
        if list(screen.query('#worker-state')):
            screen.query_one('#worker-state',Static).update('● WORKER WATCHING' if watching else '○ WORKER STOPPED')


        if len(self.screen_stack)==1:self.active_primary=1
        for page in self.screen_stack:
            for header in page.query(Header):
                try:
                    header.refresh_state()
                    nav = list(header.query(PrimaryNav))
                    if nav:
                        nav[0].select(self.active_primary)
                except Exception:
                    pass
    async def open_destination(self,index):
        from .navigation import JobWizard
        from .scheduler_ui import SchedulerScreen
        self.active_primary={1:2,2:2,3:4,4:3,9:5}.get(index,self.active_primary)
        if index==0: self.refresh_dashboard()
        elif index in (1,2): self.push_screen(JobWizard())
        elif index==3: self.push_screen(Execution())
        elif index in (4,5): self.push_screen(QueueView(index==5))
        elif index==6: self.push_screen(UsageView())
        elif index==7: self.push_screen(SchedulerScreen())
        elif index==8:
            from .extensions_ui import Extensions
            self.push_screen(Extensions())
        elif index==9: self.push_screen(Settings())
        else: await self.action_graceful_quit()
    async def on_option_list_option_selected(self,event):
        if event.option_list.id=='quick-actions':
            await self.open_destination((1,2,3)[event.option_index])
    async def on_button_pressed(self,event):
        action=event.button.id
        if action and action.startswith('nav-'):
            keys=['dashboard','projects','queue','watch','settings']
            await self.action_primary(keys.index(action[4:])+1)
        elif action=='view-queue':await self.open_destination(4)
        elif action=='more-actions':self.push_screen(SecondaryMenu())
        elif action=='action-refresh-usage':await self.action_refresh_usage()
    def remember_primary(self,number,screen):
        if not self.is_screen_installed(screen):
            self.install_screen(screen,f'primary-{number}-{id(screen)}')
        self.primary_pages[number]=screen
    async def action_primary(self,number):
        if isinstance(self.screen,ModalScreen):return
        from .navigation import JobWizard
        current=self.screen
        if isinstance(current,JobWizard):self.remember_primary(2,current)
        elif isinstance(current,QueueView) and not current.history:self.remember_primary(3,current)
        elif isinstance(current,Execution):self.remember_primary(4,current)
        elif isinstance(current,Settings):self.remember_primary(5,current)
        if number==self.active_primary and len(self.screen_stack)>1 and current is self.primary_pages.get(number):return
        while len(self.screen_stack)>1:self.pop_screen()
        self.active_primary=number
        if number!=1:
            page=self.primary_pages.get(number)
            if page is None:
                page={2:JobWizard,3:QueueView,4:Execution,5:Settings}[number]()
                self.remember_primary(number,page)
            self.push_screen(page)
        self.refresh_dashboard()
        for header in self.screen.query(Header):
            header.query_one(PrimaryNav).select(number)
        def focus_selected_tab():
            if isinstance(self.focused,Button) and (self.focused.id or '').startswith('nav-'):
                key=PrimaryNav.LABELS[number-1][1]
                self.screen.query_one('#nav-'+key,Button).focus()
        self.call_after_refresh(focus_selected_tab)
    
    async def refresh_usage(self):
        self.usage_error='Checking Codex availability'; self.refresh_dashboard()
        try:
            self.latest_usage=await asyncio.to_thread(self.provider.read); self.last_refresh=time.time(); self.usage_error='Usage refreshed.'
            if self.store and self.latest_usage:
                try:
                    cdata=json.dumps({
                        'five': vars(self.latest_usage.five),
                        'weekly': vars(self.latest_usage.weekly),
                        'observed': self.latest_usage.observed,
                        'last_refresh': self.last_refresh
                    })
                    with self.store.db:
                        self.store.db.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('latest_usage', ?)", (cdata,))
                except Exception:
                    pass
        except Exception as error: self.latest_usage=None; self.usage_error=str(error)
        self.refresh_dashboard()
    def enqueue(self,values,session=False):
        repo=Path(values['repo']).expanduser()
        if not repo.is_dir(): raise QueueError('Repository directory does not exist.')
        prompt=values.get('prompt','')
        if not session and 'prompt_file' in values:
            path=Path(values['prompt_file']).expanduser()
            if not path.is_file(): raise QueueError('Prompt file does not exist.')
            prompt=path.read_text()
        # Validate identifier/ownership before contacting any provider.
        if session:
            from .sessions import identifier
            identifier(values['session_id'])
            if not values['ownership']: raise QueueError('Confirm original-session ownership before queuing.')
        reset=self.provider.read().five.reset if values['mode']=='next-reset' else None
        store=Store(self.state_path)
        try:
            backend=values.get('backend','default')
            if backend not in ('default','direct','herdr'): raise QueueError('Invalid backend')
            job=store.add_session(values['session_id'],repo,values['cwd'],prompt,values['mode'],reset,values['ownership']) if session else store.add(prompt,repo,values['cwd'],values['mode'],reset)
            if store.get(job)['status']!='queued' and store.get(job)['backend']!=backend:
                raise QueueError('Existing job is not queued; review it in Queue Management before changing its backend.')
            with store.db: store.db.execute('UPDATE jobs SET backend=? WHERE id=?',(backend,job))
            return job
        finally: store.close()
    async def start_watching(self):
        if self.controller and self.controller.thread.is_alive():
            self.push_screen(Execution()); return
        try:
            from .execution import control
            if control(self.store.state)['mode']!='foreground': raise QueueError('Select Foreground Watcher in Execution Mode settings first; this disables the managed scheduler.')
            self.controller=Foreground(self.state_path,self.provider,self.config,simulation=bool(self.fixture))
            self.controller.start()
            if not isinstance(self.screen,Execution):self.push_screen(Execution())
        except QueueError as error: self.notify(str(error),severity='warning',timeout=10)
    async def stop_watching(self):
        if self.controller:
            self.controller.stop(); await asyncio.to_thread(self.controller.join)
            self.consume_events(); self.refresh_dashboard()
    def consume_events(self):
        if not self.controller: return
        changed=False
        while True:
            try: event=self.controller.events.get_nowait()
            except queue.Empty: break
            changed=True
            if event['state']=='usage': self.latest_usage=event['usage']; self.last_refresh=time.time(); self.usage_error='Monitoring allowance.'
            elif event['state']!='output':
                self.last_result=event
                if event['state']=='blocked':
                    self.latest_usage=None; self.usage_error=event.get('reason','Usage unavailable')
            view=next((s for s in reversed(self.screen_stack) if isinstance(s,Execution)),None)
            if view:
                if event['state']=='output': view.query_one('#execution-log',RichLog).write(event['text'])
                else:
                    wait=max(0,int(event.get('next',0)-time.time()))
                    view.query_one('#execution-status',Static).update(f"{event['state'].upper()} · {event.get('project','')} · {event.get('job','')}\nSession: {event.get('session_id') or 'new'}\n{event.get('reason','')}"+(f'\nNext check in {wait}s' if wait else ''))
                view.query_one('#execution-usage',Static).update(self.usage_text())
        view=next((s for s in reversed(self.screen_stack) if isinstance(s,Execution)),None)
        if view:
            event=self.last_result
            wait=max(0,int(event.get('next',0)-time.time()))
            elapsed=max(0,int(time.time()-event.get('started',time.time())))
            view.query_one('#execution-status',Static).update(f"{event.get('state','ready').upper()} · {event.get('project','')} · {event.get('job','')}\nSession: {event.get('session_id') or 'new'}\n{event.get('reason','')}"+(f'\nNext check in {wait}s' if wait else '')+(f'\nRunning for {elapsed}s' if event.get('started') else ''))
            view.query_one('#execution-usage',Static).update(self.usage_text())
        if changed:self.refresh_dashboard()
    async def action_graceful_quit(self):
        await self.stop_watching(); self.exit('Monitoring stopped; queued jobs preserved. Dispatched Herdr jobs may still be running; interrupt them separately.')
    def action_back(self):
        if len(self.screen_stack)>1: self.pop_screen()
    def check_action(self, action, parameters):
        if action in ('help','primary') and isinstance(self.focused,(Input,TextArea,Select)): return False
        return True
    def action_help(self):
        from .extensions_ui import ShortcutHelp
        self.push_screen(ShortcutHelp())
    def on_unmount(self):
        if self.controller: self.controller.stop(); self.controller.join()
        if self.store: self.store.close(); self.store=None
