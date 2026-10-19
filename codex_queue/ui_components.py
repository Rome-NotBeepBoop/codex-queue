"""Reusable character-cell components for the CODEX/QUEUE visual system."""
from importlib.metadata import version, PackageNotFoundError
import time
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static, Label

COLORS={'bg':'#0c1118','panel':'#101720','surface':'#151f29','border':'#293744','text':'#e2eaf3','muted':'#8394a7','accent':'#8ccac8','soft':'#93b7bb','success':'#8ed0ad','warning':'#dcb980','error':'#d98686'}
PALETTE_CSS='\n'.join(f'$cq-{name}: {color};' for name,color in COLORS.items())+'\n'

try:
    VERSION = version('codex-queue')
except PackageNotFoundError:
    VERSION = 'development'


class BrandHeader(Vertical):
    def compose(self):
        with Horizontal(classes='brand-row'):
            brand=Text('ϟ ',style=COLORS['accent'])
            brand.append('ＣＯＤＥＸ',style='bold '+COLORS['text'])
            brand.append(' / ',style=COLORS['accent'])
            brand.append('ＱＵＥＵＥ',style='bold '+COLORS['text'])
            yield Static(brand,classes='brand-name')
            yield Static(f'v{VERSION}',classes='brand-version',markup=False)
            yield Static('',classes='brand-spacer')
            yield Static('',classes='brand-state',markup=False)
        yield PrimaryNav(id='primary-nav')
    def refresh_state(self):
        if not list(self.query('.brand-state')):
            return
        if self.app.config.get('herdr_enabled') is True:
            checked=getattr(self.app,'herdr_checked_at',0)
            connected=getattr(self.app,'herdr_connected',False) and time.monotonic()-checked<120
            status='● HERDR CONNECTED' if connected else '○ HERDR NOT CHECKED'
        else: status='● LOCAL FIRST'
        self.query_one('.brand-state',Static).update(status)


class PrimaryNav(Horizontal):
    BINDINGS=[('left','previous','Previous'),('right','next','Next')]
    LABELS=[('Dashboard','dashboard'),('Projects','projects'),('Queue','queue'),('Watch','watch'),('Settings','settings')]
    def compose(self):
        for number,(title,key) in enumerate(self.LABELS,1):
            yield Button(f'{number} {title}',id='nav-'+key,classes='nav-tab'+(' active-tab' if number==1 else ''))
    def on_mount(self): self.select(getattr(self.app,'active_primary',1))
    def select(self,number):
        for index,button in enumerate(self.query(Button),1):
            button.set_class(index==number,'active-tab')
    async def on_button_pressed(self,event):
        event.stop()
        keys=[key for _,key in self.LABELS]
        await self.app.action_primary(keys.index(event.button.id[4:])+1)
    def move(self,delta):
        buttons=list(self.query(Button))
        index=buttons.index(self.app.focused) if self.app.focused in buttons else 0
        buttons[(index+delta)%len(buttons)].focus()
    def action_previous(self): self.move(-1)
    def action_next(self): self.move(1)


class UsageBar(Static):
    """A thin, non-animated progress indicator; None draws an unknown track."""
    def __init__(self,**kwargs): super().__init__(**kwargs);self.value=None
    def set_value(self,value):
        if self.value!=value:self.value=value;self.refresh()
    def render(self):
        width=max(1,self.size.width)
        filled=0 if self.value is None else round(width*max(0,min(100,self.value))/100)
        color=COLORS['warning'] if self.value is not None and self.value>=100 else COLORS['soft']
        return Text('━'*filled,style=color)+Text('━'*(width-filled),style=COLORS['border'])


class AllowanceMetric(Vertical):
    def __init__(self,title,key,**kwargs): super().__init__(**kwargs);self.title=title;self.key=key
    def compose(self):
        yield Label(self.title,classes='metric-label')
        yield Static('Unknown  NOT CHECKED',id=self.key+'-value')
        yield UsageBar(id=self.key+'-bar')


class DashboardFooter(Horizontal):
    def compose(self):
        yield Static('○ WORKER STOPPED',id='worker-state',markup=False)
        yield Static('↑↓ Select · Enter Open · Esc Back · 1–5 Tabs · ? Help',id='dashboard-hints',markup=False)
