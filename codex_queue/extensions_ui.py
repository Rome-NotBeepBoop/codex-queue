"""Lazy optional integration screens; all Herdr calls run off the UI thread."""
import asyncio
import time
import json
from textual.screen import Screen, ModalScreen
from textual.containers import VerticalScroll, Horizontal
from .ui_components import BrandHeader as Header
from textual.widgets import Footer, Label, Static, Button, Input, Switch, OptionList
from .backends import EXTENSIONS
from .herdr import HerdrBackend, HerdrClient, interrupt
from .settings import save
from .store import QueueError


class Confirmation(ModalScreen):
    BINDINGS = [('escape', 'cancel', 'Cancel')]
    def __init__(self, message, action):
        super().__init__(); self.message, self.callback = message, action
    def compose(self):
        with VerticalScroll(id='dialog'):
            yield Label('Confirm action', classes='title')
            yield Static(self.message, markup=False)
            with Horizontal():
                yield Button('Cancel', id='confirm-cancel')
                yield Button('Confirm', id='confirm-action', variant='warning')
    def on_mount(self): self.query_one('#confirm-cancel').focus()
    def action_cancel(self): self.dismiss()
    async def on_button_pressed(self, event):
        if event.button.id == 'confirm-action':
            await self.callback()
        self.dismiss()


class Extensions(Screen):
    BINDINGS = [('escape', 'back', 'Back')]
    def compose(self):
        yield Header(); yield Label('Dashboard › Extensions', classes='title')
        yield OptionList(*(f"{item['name']}  ·  {item['description']}" for item in EXTENSIONS.values()), id='extensions')
        yield Button('Back', id='extensions-back'); yield Footer()
    def on_mount(self): self.query_one(OptionList).focus()
    def on_option_list_option_selected(self, event):
        if event.option_list.id == 'extensions': self.app.push_screen(HerdrScreen())
    def on_button_pressed(self, event): self.action_back()
    def action_back(self): self.app.pop_screen()


class HerdrScreen(Screen):
    BINDINGS = [('escape', 'back', 'Back')]
    def compose(self):
        yield Header(); yield Label('Dashboard › Extensions › Herdr', classes='title')
        with VerticalScroll(id='integration-body'):
            yield Static('Installed / connection / version: not checked\nNo server or agent starts when this screen opens.', id='herdr-status', markup=False)
            yield Static('Herdr jobs may survive the watcher and TUI closing. Stop watching prevents new dispatches; Interrupt job is a separate confirmed action.', classes='muted')
            yield Label('Integration enabled (independent of real execution permission)')
            yield Switch(self.app.config.get('herdr_enabled', False), id='herdr-enabled')
            for key, label in [('herdr_executable','Herdr executable'), ('herdr_session','Local Herdr session'), ('herdr_workspace_label','Managed workspace name')]:
                yield Label(label); yield Input(self.app.config.get(key, ''), id=key)
            yield Label('Automatically focus new jobs (off by default)')
            yield Switch(self.app.config.get('herdr_auto_focus', False), id='herdr-focus')
            with Horizontal(classes='action-row'):
                yield Button('Save', id='herdr-save', variant='primary')
                yield Button('Check connection', id='herdr-check')
                yield Button('Open workspace', id='herdr-open')
            yield Label('Existing workspace: paste its exact ID and type ADOPT to allow adding managed tabs. Existing panes are left alone.')
            yield Input(placeholder='Workspace ID', id='herdr-adopt-id')
            yield Input(placeholder='Type ADOPT', id='herdr-adopt-confirm')
            with Horizontal(classes='action-row'):
                yield Button('Adopt workspace', id='herdr-adopt')
                yield Button('Diagnostics', id='herdr-diagnostics')
                yield Button('Back', id='herdr-back')
            yield Static('', id='herdr-error', markup=False)
        yield Footer()
    def on_mount(self): self.refresh_status()
    def refresh_status(self, diagnostics=None):
        for header in self.query(Header):header.refresh_state()
        backend = HerdrBackend(self.app.store, self.app.config)
        data = backend.manifest()
        status = 'Enabled' if self.app.config.get('herdr_enabled') else 'Disabled'
        text = f'Herdr · {status}\nSession: {self.app.config.get("herdr_session", "default")}\nManaged workspace: {data.get("workspace", "none")}'
        if diagnostics:
            text += f'\nInstalled · Connected · {diagnostics["version"]}\nCodex hooks: {diagnostics["codex_hooks"]}; setup is manual.'
        else: text += '\nInstalled / version / connection: not checked'
        self.query_one('#herdr-status', Static).update(text)
    async def on_button_pressed(self, event):
        action = event.button.id
        if action == 'herdr-back': self.action_back(); return
        event.button.disabled = True
        try:
            backend = HerdrBackend(self.app.store, self.app.config)
            if action == 'herdr-save':
                if self.app.controller and self.app.controller.thread.is_alive(): raise QueueError('Stop watching before changing the integration.')
                values = dict(self.app.config)
                values['herdr_enabled'] = self.query_one('#herdr-enabled', Switch).value
                values['herdr_auto_focus'] = self.query_one('#herdr-focus', Switch).value
                for key in ('herdr_executable', 'herdr_session', 'herdr_workspace_label'):
                    values[key] = self.query_one('#' + key, Input).value.strip()
                    if not values[key]: raise QueueError('Executable, session, and workspace name are required.')
                if values['herdr_enabled']:
                    await asyncio.to_thread(HerdrClient(values).capabilities)
                save(self.app.config_file, values); self.app.config = values
                self.app.herdr_connected=False
                self.refresh_status(); self.app.refresh_dashboard()
            elif action in ('herdr-check', 'herdr-diagnostics'):
                result = await asyncio.to_thread(backend.client.diagnostics)
                self.app.herdr_connected=True
                self.app.herdr_checked_at=time.monotonic()
                self.refresh_status(result)
                self.app.refresh_dashboard()
                if action == 'herdr-diagnostics':
                    self.query_one('#herdr-error', Static).update(json.dumps(result, indent=2))
            elif action == 'herdr-open': await asyncio.to_thread(backend.focus)
            elif action == 'herdr-adopt':
                if self.query_one('#herdr-adopt-confirm', Input).value != 'ADOPT': raise QueueError('Type ADOPT to explicitly authorize using that workspace.')
                if self.app.controller and self.app.controller.thread.is_alive(): raise QueueError('Stop watching before adopting a workspace.')
                await asyncio.to_thread(backend.adopt, self.query_one('#herdr-adopt-id', Input).value.strip()); self.refresh_status()
        except Exception as error:
            if action in ('herdr-check','herdr-diagnostics','herdr-save'):
                self.app.herdr_connected=False
                for header in self.query(Header):header.refresh_state()
                self.app.refresh_dashboard()
            self.query_one('#herdr-error', Static).update(str(error))
        finally: event.button.disabled = False
    def action_back(self): self.app.pop_screen()


class ShortcutHelp(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Back')]
    def compose(self):
        with VerticalScroll(id='dialog'):
            yield Label('Keyboard shortcuts', classes='title')
            yield Static('↑ ↓  Navigate lists\n← →  Dashboard tabs; table columns and selectors\n1–5  Dashboard destinations (outside text fields)\nEnter  Activate selection or button\nTab / Shift+Tab  Next / previous control\nEsc  Back or cancel\nCtrl+P  Command palette\n?  This help (outside text fields)\nCtrl+C  Stop monitoring and exit\n\nMore… opens history, usage, scheduler, extensions and exit.\nPrompt editor: Enter inserts a newline. Tab reaches the Next button.\nStopping monitoring leaves dispatched Herdr jobs running.\nUse Queue → Interrupt job to request a confirmed interruption.')
            yield Button('Back', id='help-back')
    def on_button_pressed(self, event): self.dismiss()
