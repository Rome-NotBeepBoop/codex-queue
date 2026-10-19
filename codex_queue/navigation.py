"""Progressive Textual job creation. Metadata stays local; discovery is explicit."""
import asyncio
import json
from rich.text import Text
from pathlib import Path
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from .ui_components import BrandHeader as Header
from textual.widgets import Footer, Label, Input, Button, OptionList, TextArea, Select, Static, Switch
from .projects import Catalog, Project, resolve_project_name, resolve_chat_title
from .sessions import identifier, validate_metadata
from .store import QueueError, Store, git


def time_label(value):
    from .tui import stamp
    return stamp(value)


def project_entry(project):
    entry = Text()
    entry.append("📁 ", style="#8ccac8")
    entry.append(project.name, style="bold #e2eaf3")
    entry.append("\n")
    
    recent = sorted(project.conversations, key=lambda c: c.activity, reverse=True)
    if recent:
        for c in recent[:5]:
            entry.append("   • ", style="#93b7bb")
            entry.append(c.title[:65], style="#e2eaf3")
            if c.activity:
                entry.append(f"  ·  {time_label(c.activity)}", style="dim #8394a7")
            entry.append("\n")
        if len(recent) > 5:
            entry.append(f"   Show more ({len(recent) - 5} more chats in this project)\n", style="italic #8394a7")
    return entry


class JobWizard(Screen):
    BINDINGS=[('escape','back','Back')]
    STEPS=['Project','Conversation','Prompt','Schedule','Review']
    def __init__(self):
        super().__init__();self.step=0;self.catalog=Catalog();self.project=None;self.conversation=None
        self.prompt='';self.mode='manual';self.cwd='.';self.session_id='';self.ownership=False;self.cursor=None;self.loaded=False
        self.backend='default'
    def compose(self):
        yield Header();yield Label('Dashboard → Add Job → Project',id='breadcrumbs',classes='title')
        yield VerticalScroll(id='wizard')
        yield Static('',id='wizard-error',markup=False)
        yield Footer()
    async def on_mount(self):
        if getattr(self.app.provider, 'live', False):
            self.catalog.discover_local_codex()
        for row in self.app.store.rows():
            p_name = resolve_project_name(row['repo'])
            self.catalog.projects.setdefault(row['repo'], Project(row['repo'], p_name))
        saved = self.app.store.db.execute("SELECT value FROM metadata WHERE key='projects'").fetchone()
        for repo in json.loads(saved[0]) if saved else []:
            p_name = resolve_project_name(repo)
            self.catalog.projects.setdefault(repo, Project(repo, p_name))
        for row in self.app.store.db.execute('SELECT a.session_id,a.worktree,a.started,j.repo FROM attempts a JOIN jobs j ON j.id=a.job WHERE a.session_id IS NOT NULL'):
            self.catalog.ingest([{'id':row['session_id'],'cwd':row['worktree'],'projectPath':row['repo'],'title':row['session_id'],'source':'queue history','updatedAt':row['started'],'status':{'type':'notLoaded'}}])
        await self.render_step()
    async def render_step(self):
        self.query_one('#breadcrumbs',Label).update('Dashboard → Projects & Conversations → '+' → '.join(self.STEPS[:self.step+1]))
        area=self.query_one('#wizard',VerticalScroll);await area.remove_children()
        self.query_one('#wizard-error',Static).update('')
        widgets=[]
        if self.step==0:
            self.project_options=sorted(self.catalog.projects.values(),key=lambda p:p.activity,reverse=True)
            widgets=[
                Label('Projects', classes='projects-title'),
                Static('Select a project below to choose a chat and schedule a prompt job:', classes='muted'),
                OptionList(*(project_entry(p) for p in self.project_options), id='projects'),
                Static('', id='project-summary', markup=False),
                Horizontal(
                    Input(placeholder='Repository path for new project', id='manual-repo'),
                    Button('Add repository', id='add-repo'),
                    Button('Discover Codex projects' if not self.loaded else 'Load more conversations', id='discover', disabled=self.loaded and not self.cursor),
                    classes='add-repo-row'
                ),
                Static('Discovery reads local Codex session metadata only when selected. No GitHub required.', classes='muted')
            ]
        elif self.step==1:
            widgets=[
                Label(f"Projects › 📁 {self.project.name} · Choose what chat it got it from:", classes='section-label'),
                Input(placeholder='Search titles, sources or IDs', id='search'),
                OptionList(id='conversations'),
                Static('', id='conversation-preview', markup=False),
                Button('Continue with selected conversation', id='use-selected', variant='primary'),
                Label('Manual ID entry (optional — no ID needed for a listed conversation)'),
                Input('', placeholder='Paste a full session UUID here', id='manual-session'),
                Button('Use pasted session ID', id='use-session')
            ]
        elif self.step==2:
            widgets=[Label('Edit the prompt. Terminal paste is supported.'),TextArea(self.prompt,id='prompt-editor'),Input(placeholder='Optional Markdown/text file path',id='load-path'),Button('Load file',id='load-file'),Input(self.cwd,placeholder='Working directory relative to repository',id='working-dir'),Label('For existing sessions: original owner stopped; I exclusively own this checkout.'),Switch(self.ownership,id='exclusive',disabled=not bool(self.session_id)),Button('Choose schedule',id='next')]
        elif self.step==3:
            widgets=[
                Label(f'When should this job in 📁 {self.project.name} become eligible?'),
                Select([
                    ('Manual', 'manual'),
                    ('Immediately when eligible', 'immediate'),
                    ('After next five-hour reset', 'next-reset')
                ], value=self.mode, allow_blank=False, id='schedule'),
                Static('This schedule works with either execution mode. No job runs while you create it.', markup=False),
                Button('Review job', id='next')
            ]
            widgets.insert(2,Select([('Use integration setting','default'),('Direct Codex','direct'),('Herdr','herdr')],value=self.backend,allow_blank=False,id='job-backend'))
        else:
            from .execution import control
            mode=control(self.app.store.state)['mode']
            chat_name = resolve_chat_title(self.session_id) if self.session_id else "Start New Conversation"
            widgets=[Static(f'Project: 📁 {self.project.name}\nChat / Source: 💬 {chat_name}\nRepository: {self.project.path}\nDirectory: {self.cwd}\nSession ID: {self.session_id or "new"}\nSchedule: {self.mode}\nBackend: {self.backend}\nExecution mode: {mode}\nExisting session ownership: {self.ownership}',markup=False),Label('PROMPT TO SEND AFTER RESET:' if self.mode == 'next-reset' else 'PROMPT PREVIEW:'),TextArea(self.prompt,read_only=True,id='prompt-preview'),Button('Add to queue',id='queue-job',variant='primary')]
        widgets.append(Button('Back',id='wizard-back'))
        await area.mount(*widgets)
        if self.step==0 and self.project_options:
            try:
                self.query_one('#projects',OptionList).highlighted=0
                p = self.project_options[0]
                recent_chat = max(p.conversations, key=lambda c: c.activity).title if p.conversations else "None yet"
                self.query_one('#project-summary', Static).update(f"PROJECT: 📁 {p.name}  ({Path(p.path).name})\nLOCATION: {p.path}\nRECORDED CHATS: {len(p.conversations)}  ·  LATEST CHAT: {recent_chat}\nPress Enter to open this project's conversations and queue a job.")
            except Exception:
                pass

        if self.step==1:self.refresh_conversations('')
        if self.step==2:self.query_one('#prompt-editor',TextArea).styles.height=12
        focus=next((w for w in widgets if w.can_focus and not w.disabled),None)
        if focus:focus.focus()
    def refresh_conversations(self,search):
        self.conversation_options=self.catalog.filtered(self.project,search)
        options=self.query_one('#conversations',OptionList);options.clear_options()
        opts = ['Start New Conversation']
        for c in self.conversation_options:
            t = Text()
            t.append(f"{c.title} · {c.source} · {time_label(c.activity)}")
            if not c.available:
                t.append(" · busy/unavailable")
            opts.append(t)
        options.add_options(opts)
        options.highlighted=0
    def on_input_changed(self,event):
        if event.input.id=='search':self.refresh_conversations(event.value)
    def on_option_list_option_highlighted(self,event):
        if event.option_list.id=='projects':
            if hasattr(self, 'project_options') and event.option_index is not None and event.option_index < len(self.project_options):
                p = self.project_options[event.option_index]
                recent_chat = max(p.conversations, key=lambda c: c.activity).title if p.conversations else "None yet"
                summary = (f"PROJECT: 📁 {p.name}  ({Path(p.path).name})\n"
                           f"LOCATION: {p.path}\n"
                           f"RECORDED CHATS: {len(p.conversations)}  ·  LATEST CHAT: {recent_chat}\n"
                           f"Press Enter to open this project's conversations and queue a job.")
                if list(self.query('#project-summary')):
                    self.query_one('#project-summary', Static).update(summary)
        elif event.option_list.id=='conversations':
            c=self.conversation_options[event.option_index-1] if event.option_index else None
            if c:
                prev = (f"PROJECT: 📁 {self.project.name}\n"
                        f"CHAT: 💬 {c.title}\n"
                        f"SESSION ID: {c.id}\n"
                        f"SOURCE: {c.source} · LAST ACTIVITY: {time_label(c.activity)}\n"
                        f"DIRECTORY: {c.cwd}\n\n"
                        f"PREVIEW:\n{c.preview or 'No preview supplied by this Codex version.'}")
            else:
                prev = f"PROJECT: 📁 {self.project.name}\nA fresh session will start in an isolated checkout of this project."
            self.query_one('#conversation-preview',Static).update(prev)

    async def on_option_list_option_selected(self,event):
        try:
            if event.option_list.id=='projects':
                self.project=self.project_options[event.option_index];self.step=1;await self.render_step()
            elif event.option_list.id=='conversations':
                await self.select_conversation(event.option_index)
        except (QueueError,OSError,ValueError) as error:self.query_one('#wizard-error',Static).update(str(error))
    async def select_conversation(self,index):
        self.conversation=self.conversation_options[index-1] if index else None
        if self.conversation and not self.conversation.available:raise QueueError('Conversation is busy or unavailable; release ownership first')
        self.session_id=self.conversation.id if self.conversation else ''
        self.cwd='.';self.ownership=False
        if self.conversation:
            # Only the explicitly selected checkout is inspected now.
            root=await asyncio.to_thread(git,self.conversation.cwd,'rev-parse','--show-toplevel')
            self.project=Project(root,Path(root).name,self.project.conversations)
            self.cwd=str(Path(self.conversation.cwd).resolve().relative_to(Path(root).resolve()))
        self.step=2;await self.render_step()
    async def on_button_pressed(self,event):
        action=event.button.id
        try:
            if action=='wizard-back':await self.action_back()
            elif action=='add-repo':
                project=await asyncio.to_thread(self.catalog.manual,self.query_one('#manual-repo',Input).value)
                with self.app.store.db:self.app.store.db.execute("INSERT OR REPLACE INTO metadata VALUES('projects',?)",(json.dumps(list(self.catalog.projects)),))
                self.project=project;self.step=1;await self.render_step()
            elif action=='discover':
                event.button.disabled=True
                if not hasattr(self.app.provider,'list_threads'):raise QueueError('Session listing unavailable for this provider; add a repository manually')
                if not getattr(self.app.provider, 'live', False):
                    page = self.app.provider.list_threads(self.cursor)
                else:
                    page = await asyncio.to_thread(self.app.provider.list_threads, self.cursor)
                self.catalog.ingest(page.get('data',[]));self.cursor=page.get('nextCursor');self.loaded=True;await self.render_step()
            elif action=='use-selected':
                index=self.query_one('#conversations',OptionList).highlighted
                if index is None:raise QueueError('Choose a conversation from the list first')
                await self.select_conversation(index)
            elif action=='use-session':
                value=self.query_one('#manual-session',Input).value.strip()
                if not value:raise QueueError('Paste an ID in the box above, or use Continue with selected conversation.')
                self.session_id=identifier(value);self.conversation=None;self.cwd='.';self.step=2;await self.render_step()
            elif action=='load-file':
                file=Path(self.query_one('#load-path',Input).value).expanduser()
                if file.suffix.lower() not in ('.md','.markdown','.txt') or file.stat().st_size>1000000:raise QueueError('Choose a Markdown/text file smaller than 1 MB')
                text=await asyncio.to_thread(file.read_text,encoding='utf-8');self.query_one('#prompt-editor',TextArea).load_text(text)
            elif action=='next':
                if self.step==2:
                    self.prompt=self.query_one('#prompt-editor',TextArea).text;self.cwd=self.query_one('#working-dir',Input).value;self.ownership=self.query_one('#exclusive',Switch).value
                    if not self.prompt.strip() and not self.session_id:raise QueueError('Enter a prompt before continuing')
                    if self.session_id and not self.ownership:raise QueueError('Confirm exclusive session ownership before continuing')
                else:
                    self.mode=self.query_one('#schedule',Select).value
                    self.backend=self.query_one('#job-backend',Select).value
                self.step+=1;await self.render_step()
            elif action=='queue-job':
                event.button.disabled=True
                if not getattr(self.app.provider, 'live', False):
                    job = self.submit()
                else:
                    job = await asyncio.to_thread(self.submit)
                self.app.notify(f'Queued {job}')
                if self.app.primary_pages.get(2) is self:self.app.primary_pages.pop(2)
                self.app.pop_screen();self.app.refresh_dashboard()

        except Exception as error:
            self.query_one('#wizard-error',Static).update(str(error))
            if action in ('discover','queue-job'):event.button.disabled=False
    def submit(self):
        if self.session_id:
            metadata=self.app.provider.read_thread(self.session_id)
            validate_metadata(metadata,{'session_id':self.session_id,'session_cwd':str((Path(self.project.path)/self.cwd).resolve()),'ownership_confirmed':self.ownership})
        values={'repo':self.project.path,'cwd':self.cwd,'prompt':self.prompt,'mode':self.mode,'session_id':self.session_id,'ownership':self.ownership,'backend':self.backend}
        return self.app.enqueue(values,bool(self.session_id))
    async def action_back(self):
        if self.step:
            if self.step==2:
                self.prompt=self.query_one('#prompt-editor',TextArea).text
                self.cwd=self.query_one('#working-dir',Input).value;self.ownership=self.query_one('#exclusive',Switch).value
            if self.step==3:
                self.mode=self.query_one('#schedule',Select).value
                self.backend=self.query_one('#job-backend',Select).value
            self.step-=1;await self.render_step()
        else:self.app.pop_screen();self.app.refresh_dashboard()
