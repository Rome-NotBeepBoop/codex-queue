"""Minimal CLI mode rendering core TUI features in clean text format."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import time

from .paths import state_path, config_path
from .settings import load as load_settings, save as save_settings, toggle_cli_mode
from .store import Store, QueueError
from .usage import Usage, Window, UsageError
from .projects import (
    Catalog,
    Project,
    resolve_project_name,
    resolve_chat_title,
    sanitize_chat_title,
)
from . import projects as projects_mod
from .app_server import AppServer


from .cli_style import Style, Glyphs, should_color, strip_ansi, format_display_path


def _safe_local_dt(ts):
    if ts is None or ts == '':
        return None
    try:
        from datetime import timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    except Exception:
        try:
            from datetime import timezone
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None


def _build_catalog(store=None) -> Catalog:
    """Build and populate catalog from discovered local projects and store history."""
    catalog = Catalog()
    try:
        catalog.discover_local_codex()
    except Exception:
        pass

    if store:
        try:
            for row in store.rows():
                p_name = resolve_project_name(row['repo'])
                catalog.projects.setdefault(row['repo'], Project(row['repo'], p_name))
            saved = store.db.execute("SELECT value FROM metadata WHERE key='projects'").fetchone()
            for repo in (json.loads(saved[0]) if saved else []):
                p_name = resolve_project_name(repo)
                catalog.projects.setdefault(repo, Project(repo, p_name))
            for row in store.db.execute("SELECT a.session_id, a.worktree, a.started, j.repo FROM attempts a JOIN jobs j ON j.id=a.job WHERE a.session_id IS NOT NULL"):
                repo_path = Path(row['repo'])
                cwd_val = row['worktree']
                if not cwd_val or not Path(cwd_val).is_absolute():
                    cwd_val = str((repo_path / (cwd_val or '.')).resolve()) if repo_path.is_absolute() else str(repo_path)
                chat_title = resolve_chat_title(row['session_id'])
                catalog.ingest([{
                    'id': row['session_id'],
                    'cwd': cwd_val,
                    'projectPath': row['repo'],
                    'title': chat_title if chat_title != row['session_id'] else row['session_id'],
                    'source': 'queue history',
                    'updatedAt': row['started'],
                    'status': {'type': 'notLoaded'}
                }])
            for row in store.db.execute("SELECT session_id, cwd, repo, created, prompt FROM jobs WHERE session_id IS NOT NULL"):
                repo_path = Path(row['repo'])
                cwd_val = row['cwd']
                if not cwd_val or not Path(cwd_val).is_absolute():
                    cwd_val = str((repo_path / (cwd_val or '.')).resolve()) if repo_path.is_absolute() else str(repo_path)
                chat_title = resolve_chat_title(row['session_id'])
                catalog.ingest([{
                    'id': row['session_id'],
                    'cwd': cwd_val,
                    'projectPath': row['repo'],
                    'title': chat_title if chat_title != row['session_id'] else sanitize_chat_title(row.get('prompt') or row['session_id']),
                    'source': 'queue job',
                    'updatedAt': row['created'],
                    'status': {'type': 'idle'}
                }])
        except Exception:
            pass
    return catalog


def render_cli_overview(state=None, config=None, fixture=None, provider=None, store=None, color=None, stream=None) -> str:
    """Render account/usage, projects & conversations, and active/queued jobs."""
    close_store = False
    if store is None:
        st_path = state or os.environ.get("CODEX_QUEUE_STATE", str(state_path()))
        try:
            store = Store(st_path)
            close_store = True
        except Exception:
            store = None

    effective_color = color if color is not None else should_color(stream)
    style = Style(effective_color)

    try:
        cfg_file = config or os.environ.get("CODEX_QUEUE_CONFIG", str(config_path()))
        cfg = load_settings(cfg_file)
        codex = cfg.get("codex", "codex")
        bucket = cfg.get("bucket", "codex")

        # --- Usage fetching ---
        usage = None
        conn_status = "Not checked"
        error_detail = ""

        if fixture and provider is None:
            from .cli import Fixture
            provider = Fixture(fixture, bucket)

        is_live_provider = getattr(provider, "live", True) if provider is not None else False

        # If a non-live provider (like MockUsageProvider in tests) is passed, read it
        if provider is not None and not is_live_provider:
            try:
                usage = provider.read()
                conn_status = "Connected"
                if usage and time.time() - usage.observed > 120:
                    conn_status = "Connected (stale)"
                if store and usage:
                    try:
                        cdata = json.dumps({
                            'five': vars(usage.five),
                            'weekly': vars(usage.weekly),
                            'observed': usage.observed,
                            'last_refresh': time.time()
                        })
                        with store.db:
                            store.db.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('latest_usage', ?)", (cdata,))
                    except Exception:
                        pass
            except Exception as e:
                error_detail = str(e)
                usage = None

        # Fallback to cached usage directly from store without waiting for app-server timeouts
        if usage is None and store:
            try:
                cached = store.db.execute("SELECT value FROM metadata WHERE key='latest_usage'").fetchone()
                if cached:
                    cdata = json.loads(cached[0])
                    usage = Usage(
                        Window(**cdata['five']),
                        Window(**cdata['weekly']),
                        cdata['observed']
                    )
                    conn_status = "Cached"
                    if time.time() - usage.observed > 120:
                        conn_status = "Cached (stale)"
            except Exception:
                pass

        if usage is None and error_detail:
            conn_status = f"Disconnected ({error_detail})"
        elif usage is None:
            conn_status = "Disconnected"

        # --- Projects & Conversations catalog ---
        catalog = _build_catalog(store)
        home_str = str(Path.home())
        active_projects = []
        is_mock = projects_mod._override_metadata is not None
        for p in catalog.projects.values():
            if not p.conversations:
                continue
            if not is_mock:
                try:
                    if not Path(p.path).exists():
                        continue
                    if p.path.startswith("/Users/") or p.path.startswith("/home/"):
                        if not (p.path == home_str or p.path.startswith(home_str + "/")):
                            continue
                except OSError:
                    continue
            active_projects.append(p)
        active_projects.sort(key=lambda p: (-p.activity, p.name.lower()))

        # --- Queue jobs ---
        all_rows = store.rows() if store else []
        active_rows = [r for r in all_rows if r['status'] in ('queued', 'running', 'paused')]

        lines = []

        if not effective_color:
            # Clean unstyled output
            lines.append("=== Codex Queue (CLI Mode) ===")
            lines.append("")

            # 1. Account / Usage Overview
            lines.append("=== Account / Usage Overview ===")
            lines.append(f"Connection Status: {conn_status}")
            if usage:
                five_rem = max(0, min(100, round(100 - usage.five.used, 4))) if usage.five else 0
                week_rem = max(0, min(100, round(100 - usage.weekly.used, 4))) if usage.weekly else 0
                if usage.five and usage.five.reset:
                    reset_dt = _safe_local_dt(usage.five.reset)
                    reset_str = reset_dt.strftime('%a %H:%M:%S %Z') if reset_dt else "Unknown"
                    secs = max(0, int(usage.five.reset - time.time()))
                    countdown_str = f"in {secs // 3600}h {(secs % 3600) // 60}m" if secs > 0 else "reset reached"
                    lines.append(f"5-Hour Allowance: {five_rem:g}% usage remaining · Reset Time: {reset_str} ({countdown_str})")
                else:
                    lines.append(f"5-Hour Allowance: {five_rem:g}% usage remaining · Reset Time: Unknown")
                if usage.weekly:
                    lines.append(f"Weekly Usage: {usage.weekly.used:g}% used ({week_rem:g}% remaining)")
                else:
                    lines.append("Weekly Usage: Unknown")
            else:
                lines.append("5-Hour Allowance: Unknown (usage not available)")
                lines.append("Weekly Usage: Unknown")

            lines.append("")

            # 2. Projects & Conversations
            lines.append("=== Projects & Conversations ===")
            if not active_projects:
                lines.append("  (no active projects)")
            else:
                for p in active_projects[:5]:
                    lines.append(f"  • {p.name} ({p.path})")
                    chats = sorted(p.conversations, key=lambda c: c.activity, reverse=True)
                    lines.append("    Recent chats:")
                    for c in chats[:3]:
                        title = resolve_chat_title(c.id) if c.id else (c.title or "Untitled chat")
                        if title == c.id and c.title and c.title != c.id:
                            title = c.title
                        title = sanitize_chat_title(title)
                        lines.append(f"      - {title}")

            lines.append("")

            # 3. Queue
            lines.append("=== Queue ===")
            if not active_rows:
                lines.append("  (no active or queued jobs)")
            else:
                for r in active_rows:
                    p_name = resolve_project_name(r['repo'])
                    chat_title = sanitize_chat_title(resolve_chat_title(r.get('session_id'))) if r.get('session_id') else ''

                    if r['mode'] == 'next-reset' or r.get('saved_reset'):
                        reset_ts = r.get('saved_reset')
                        if not reset_ts and usage and usage.five and usage.five.reset:
                            reset_ts = usage.five.reset
                        if reset_ts:
                            secs = max(0, int(reset_ts - time.time()))
                            countdown_str = f"in {secs // 3600}h {(secs % 3600) // 60}m" if secs > 0 else "Reset time reached"
                        else:
                            countdown_str = "Waiting for reset timestamp"
                    else:
                        countdown_str = "N/A"

                    lines.append(f"  • Job #{r['id']} [{r['status']}]")
                    lines.append(f"    Project: {p_name} ({r['repo']})")
                    if chat_title:
                        lines.append(f"    Chat: {chat_title}")
                    lines.append(f"    Status: {r['status']}")
                    lines.append(f"    Mode: {r['mode']}")
                    lines.append(f"    Reset Countdown: {countdown_str}")

                    prompt_text = r.get('prompt') or ''
                    if r['mode'] == 'next-reset':
                        prompt_header = "Prompt job to send after reset"
                    else:
                        prompt_header = "Prompt job to send"

                    if not prompt_text and r.get('session_id'):
                        prompt_text = f"(continue session {chat_title or r['session_id']})"

                    display_prompt = prompt_text or "(empty prompt)"
                    prompt_lines = display_prompt.splitlines()
                    if len(prompt_lines) <= 1:
                        lines.append(f"    {prompt_header}: {display_prompt}")
                    else:
                        lines.append(f"    {prompt_header}: {prompt_lines[0]}")
                        max_preview = 4
                        for pline in prompt_lines[1:max_preview]:
                            lines.append(f"      {pline}")
                        if len(prompt_lines) > max_preview:
                            lines.append(f"      ... ({len(prompt_lines) - max_preview} more lines)")

            return "\n".join(lines)

        # Styled output with modern accents, nerd font glyphs, and personality
        lines.append(f"  {style.cyan(f'{Glyphs.LOGO}  Codex Queue', bold=True)}  {style.slate(Glyphs.DIAMOND)}  {style.blue('CLI Mode')}")
        lines.append(f"  {style.divider()}")
        lines.append("")

        # 1. Account / Usage Overview
        lines.append(f"  {style.cyan(f'{Glyphs.GAUGE} Account / Usage Overview', bold=True)}")
        lines.append(f"  {style.divider()}")
        lines.append(f"  {style.slate('Connection Status:')} {style.connection_pill(conn_status)}")

        if usage:
            five_rem = max(0, min(100, round(100 - usage.five.used, 4))) if usage.five else 0
            week_rem = max(0, min(100, round(100 - usage.weekly.used, 4))) if usage.weekly else 0
            bar_5h = style.allowance_bar(five_rem, width=12)
            bar_wk = style.usage_bar(usage.weekly.used if usage.weekly else 0, width=12)

            if five_rem == 0:
                rem_str = style.amber(f"{five_rem:g}% usage remaining", bold=True)
            elif five_rem < 20:
                rem_str = style.amber(f"{five_rem:g}% usage remaining")
            else:
                rem_str = style.green(f"{five_rem:g}% usage remaining")

            if usage.five and usage.five.reset:
                reset_dt = _safe_local_dt(usage.five.reset)
                reset_str = reset_dt.strftime('%a %H:%M:%S %Z') if reset_dt else "Unknown"
                secs = max(0, int(usage.five.reset - time.time()))
                countdown_str = f"in {secs // 3600}h {(secs % 3600) // 60}m" if secs > 0 else "reset reached"
                lines.append(
                    f"  {style.slate('5-Hour Allowance:')} {bar_5h} {rem_str} "
                    f"{style.slate('·')} {style.slate('Reset Time:')} {style.amber(f'{Glyphs.CLOCK} {reset_str}')} "
                    f"{style.slate(f'({countdown_str})')}"
                )
            else:
                lines.append(f"  {style.slate('5-Hour Allowance:')} {bar_5h} {rem_str} {style.slate('· Reset Time: Unknown')}")

            if usage.weekly:
                used_str = style.blue(f"{usage.weekly.used:g}% used")
                lines.append(f"  {style.slate('Weekly Usage:    ')} {bar_wk} {used_str} {style.slate(f'({week_rem:g}% remaining)')}")
            else:
                lines.append(f"  {style.slate('Weekly Usage:    ')} {style.slate('Unknown')}")
        else:
            lines.append(f"  {style.slate('5-Hour Allowance:')} {style.slate('Unknown (usage not available)')}")
            lines.append(f"  {style.slate('Weekly Usage:    ')} {style.slate('Unknown')}")

        lines.append("")

        # 2. Projects & Conversations
        lines.append(f"  {style.cyan(f'{Glyphs.FOLDER} Projects & Conversations', bold=True)}")
        lines.append(f"  {style.divider()}")
        if not active_projects:
            lines.append(f"  {style.slate('(no active projects)')}")
        else:
            for p in active_projects[:5]:
                disp_path = format_display_path(p.path)
                lines.append(f"  {style.cyan(Glyphs.DIAMOND)} {style.bright(p.name)} {style.slate(f'({disp_path})')}")
                chats = sorted(p.conversations, key=lambda c: c.activity, reverse=True)
                lines.append(f"    {style.slate('Recent chats:')}")
                for c in chats[:3]:
                    title = resolve_chat_title(c.id) if c.id else (c.title or "Untitled chat")
                    if title == c.id and c.title and c.title != c.id:
                        title = c.title
                    title = sanitize_chat_title(title)
                    lines.append(f"      {style.blue(Glyphs.CHAT)} {title}")

        lines.append("")

        # 3. Queue
        lines.append(f"  {style.cyan(f'{Glyphs.LOGO} Queue', bold=True)}")
        lines.append(f"  {style.divider()}")
        if not active_rows:
            lines.append(f"  {style.slate('(no active or queued jobs)')}")
        else:
            for r in active_rows:
                p_name = resolve_project_name(r['repo'])
                chat_title = sanitize_chat_title(resolve_chat_title(r.get('session_id'))) if r.get('session_id') else ''
                pill = style.status_pill(r['status'])

                if r['mode'] == 'next-reset' or r.get('saved_reset'):
                    reset_ts = r.get('saved_reset')
                    if not reset_ts and usage and usage.five and usage.five.reset:
                        reset_ts = usage.five.reset
                    if reset_ts:
                        secs = max(0, int(reset_ts - time.time()))
                        countdown_str = f"in {secs // 3600}h {(secs % 3600) // 60}m" if secs > 0 else "Reset time reached"
                    else:
                        countdown_str = "Waiting for reset timestamp"
                else:
                    countdown_str = "N/A"

                job_label = f"Job #{r['id']}"
                disp_repo = format_display_path(r['repo'])
                repo_label = f"({disp_repo})"
                lines.append(f"  {style.cyan('•')} {style.bright(job_label)}  {pill}")
                lines.append(f"    {style.slate('Project:')} {style.bright(p_name)} {style.slate(repo_label)}")
                if chat_title:
                    lines.append(f"    {style.slate('Chat:   ')} {style.blue(Glyphs.CHAT)} {chat_title}")
                lines.append(f"    {style.slate('Status: ')} {pill}")
                lines.append(f"    {style.slate('Mode:   ')} {style.purple(r['mode'])}")
                if countdown_str != "N/A":
                    lines.append(f"    {style.slate('Reset Countdown:')} {style.amber(f'{Glyphs.CLOCK} {countdown_str}')}")
                else:
                    lines.append(f"    {style.slate('Reset Countdown:')} {style.slate('N/A')}")

                prompt_text = r.get('prompt') or ''
                if r['mode'] == 'next-reset':
                    prompt_header = "Prompt job to send after reset"
                else:
                    prompt_header = "Prompt job to send"

                if not prompt_text and r.get('session_id'):
                    prompt_text = f"(continue session {chat_title or r['session_id']})"

                display_prompt = prompt_text or "(empty prompt)"
                prompt_lines = display_prompt.splitlines()
                if len(prompt_lines) <= 1:
                    lines.append(f"    {style.slate(f'{prompt_header}:')} {display_prompt}")
                else:
                    lines.append(f"    {style.slate(f'{prompt_header}:')} {prompt_lines[0]}")
                    max_preview = 4
                    for pline in prompt_lines[1:max_preview]:
                        lines.append(f"      {pline}")
                    if len(prompt_lines) > max_preview:
                        lines.append(f"      {style.slate(f'... ({len(prompt_lines) - max_preview} more lines)')}")

        return "\n".join(lines)
    finally:
        if close_store and store:
            store.close()


class CliController:
    """Interactive command-line controller for Codex Queue."""

    def __init__(self, state=None, config=None, fixture=None, provider=None, store=None, stream=None, color=None):
        self.state = state
        self.config_path = config or os.environ.get("CODEX_QUEUE_CONFIG", str(config_path()))
        self.fixture = fixture
        self.provider = provider
        self.store = store
        self.stream = stream or sys.stdout
        self.color = color if color is not None else should_color(self.stream)
        self.style = Style(self.color)

    def display_overview(self):
        output = render_cli_overview(
            state=self.state,
            config=self.config_path,
            fixture=self.fixture,
            provider=self.provider,
            store=self.store,
            color=self.color,
            stream=self.stream,
        )
        print(output, file=self.stream)

    def view_manage_queue(self):
        if self.color:
            print(f"\n  {self.style.cyan(f'{Glyphs.LOGO} Queue Management', bold=True)}", file=self.stream)
            print(f"  {self.style.divider()}", file=self.stream)
        else:
            print("\n=== Queue Management ===", file=self.stream)

        if not self.store:
            print("  Store not available.", file=self.stream)
            return

        rows = self.store.rows()
        if not rows:
            print(f"  {self.style.slate('(no jobs in queue)')}" if self.color else "  (no jobs in queue)", file=self.stream)
        else:
            for r in rows:
                p_name = resolve_project_name(r['repo'])
                chat_title = sanitize_chat_title(resolve_chat_title(r.get('session_id'))) if r.get('session_id') else ''
                summary = chat_title or sanitize_chat_title(r.get('prompt') or 'New Prompt')
                if self.color:
                    pill = self.style.status_pill(r['status'])
                    job_num = f"#{r['id']}"
                    mode_label = f"({r['mode']})"
                    print(f"  {self.style.bright(job_num)} {pill} {self.style.bright(p_name)} {self.style.purple(mode_label)} {self.style.slate('-')} {summary}", file=self.stream)
                else:
                    print(f"  #{r['id']} [{r['status']}] {p_name} ({r['mode']}) - {summary}", file=self.stream)

        if self.color:
            actions_help = (
                f"\n  {self.style.slate('Actions:')} "
                f"{self.style.cyan('[v <id>]')} View  "
                f"{self.style.amber('[p <id>]')} Pause  "
                f"{self.style.green('[r <id>]')} Resume  "
                f"{self.style.red('[c <id>]')} Cancel  "
                f"{self.style.purple('[t <id>]')} Retry  "
                f"{self.style.slate('[b]')} Back"
            )
            print(actions_help, file=self.stream)
            prompt_q = f"  {self.style.cyan(Glyphs.PROMPT, bold=True)} Enter queue action: "
        else:
            print("\nQueue Actions: [v <id>] View details  [p <id>] Pause  [r <id>] Resume  [c <id>] Cancel  [t <id>] Retry  [b] Back", file=self.stream)
            prompt_q = "Enter queue action: "

        try:
            cmd = input(prompt_q).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not cmd or cmd.lower() in ('b', 'back', 'q'):
            return

        parts = cmd.split(maxsplit=1)
        action = parts[0].lower()
        job_arg = parts[1] if len(parts) > 1 else None

        if not job_arg:
            id_prompt = f"  {self.style.cyan(Glyphs.PROMPT, bold=True)} Enter job ID: " if self.color else "Enter job ID: "
            try:
                job_arg = input(id_prompt).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not job_arg:
                return

        job_id = job_arg.strip().lstrip("#")
        if not job_id:
            return

        try:
            exact = self.store.db.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not exact:
                safe_prefix = job_id.replace("%", "").replace("_", "")
                if safe_prefix:
                    matches = self.store.db.execute("SELECT id FROM jobs WHERE id LIKE ?", (safe_prefix + "%",)).fetchall()
                    if len(matches) == 1:
                        job_id = matches[0][0]
                    elif len(matches) > 1:
                        print(f"Ambiguous job ID prefix: '{job_id}' (matches {len(matches)} jobs)", file=self.stream)
                        return
                    else:
                        print(f"Job not found: '{job_id}'", file=self.stream)
                        return
                else:
                    print(f"Job not found: '{job_id}'", file=self.stream)
                    return
        except Exception:
            pass

        if action in ('v', 'view', 'show'):
            self._show_job_details(job_id)
        elif action in ('p', 'pause'):
            self._transition_job(job_id, 'pause')
        elif action in ('r', 'resume'):
            self._transition_job(job_id, 'resume')
        elif action in ('c', 'cancel'):
            self._transition_job(job_id, 'cancel')
        elif action in ('t', 'retry'):
            self._transition_job(job_id, 'retry')
        else:
            print(f"Unknown action: '{action}'. Allowed: [v]iew, [p]ause, [r]esume, [c]ancel, [t]retry, [b]ack", file=self.stream)

    def _show_job_details(self, job_id: str):
        try:
            job = self.store.get(job_id)
        except Exception as e:
            print(f"Error fetching job #{job_id}: {e}", file=self.stream)
            return

        p_name = resolve_project_name(job['repo'])
        chat_title = sanitize_chat_title(resolve_chat_title(job.get('session_id'))) if job.get('session_id') else ''

        if self.color:
            disp_repo = format_display_path(job['repo'])
            repo_label = f"({disp_repo})"
            print(f"\n  {self.style.cyan(f'{Glyphs.DIAMOND} Job #{job_id} Details', bold=True)}", file=self.stream)
            print(f"  {self.style.divider()}", file=self.stream)
            print(f"  {self.style.slate('Status:    ')} {self.style.status_pill(job['status'])}", file=self.stream)
            print(f"  {self.style.slate('Mode:      ')} {self.style.purple(job['mode'])}", file=self.stream)
            print(f"  {self.style.slate('Project:   ')} {self.style.bright(p_name)} {self.style.slate(repo_label)}", file=self.stream)
            print(f"  {self.style.slate('CWD:       ')} {job.get('cwd', '.')}", file=self.stream)
            if job.get('reason'):
                print(f"  {self.style.slate('Reason:    ')} {self.style.amber(job['reason'])}", file=self.stream)
            if job.get('session_id'):
                print(f"  {self.style.slate('Session ID:')} {job['session_id']}", file=self.stream)
                if chat_title:
                    print(f"  {self.style.slate('Chat Title:')} {self.style.blue(Glyphs.CHAT)} {chat_title}", file=self.stream)
            if job.get('saved_reset'):
                reset_dt = _safe_local_dt(job['saved_reset'])
                reset_fmt = reset_dt.strftime('%a %H:%M:%S %Z') if reset_dt else "Unknown"
                print(f"  {self.style.slate('Saved Reset:')} {self.style.amber(f'{Glyphs.CLOCK} {reset_fmt}')}", file=self.stream)
            if job.get('prompt'):
                print(f"  {self.style.slate('Prompt:')}\n    {job['prompt']}", file=self.stream)
        else:
            print(f"\n--- Job #{job_id} Details ---", file=self.stream)
            print(f"  Status: {job['status']}", file=self.stream)
            print(f"  Mode: {job['mode']}", file=self.stream)
            print(f"  Project: {p_name} ({job['repo']})", file=self.stream)
            print(f"  CWD: {job.get('cwd', '.')}", file=self.stream)
            if job.get('reason'):
                print(f"  Reason: {job['reason']}", file=self.stream)
            if job.get('session_id'):
                print(f"  Session ID: {job['session_id']}", file=self.stream)
                if chat_title:
                    print(f"  Chat Title: {chat_title}", file=self.stream)
            if job.get('saved_reset'):
                reset_dt = _safe_local_dt(job['saved_reset'])
                reset_fmt = reset_dt.strftime('%a %H:%M:%S %Z') if reset_dt else "Unknown"
                print(f"  Saved Reset: {reset_fmt}", file=self.stream)
            if job.get('prompt'):
                print(f"  Prompt:\n    {job['prompt']}", file=self.stream)

        attempts = [dict(r) for r in self.store.db.execute("SELECT * FROM attempts WHERE job=? ORDER BY number", (job_id,))]
        if attempts:
            if self.color:
                print(f"  {self.style.slate(f'Attempt History ({len(attempts)}):')}", file=self.stream)
                for a in attempts:
                    status_str = a.get('status') or 'unknown'
                    started_dt = _safe_local_dt(a['started']) if a.get('started') else None
                    started_str = started_dt.strftime('%Y-%m-%d %H:%M:%S') if started_dt else 'unknown'
                    pill = self.style.status_pill(status_str)
                    details = f"{pill} {self.style.slate(f'started={started_str}')}"
                    if a.get('ended') and a.get('started'):
                        dur = max(0, int(a['ended'] - a['started']))
                        details += f" {self.style.slate(f'duration={dur}s')}"
                    if a.get('branch'):
                        branch_str = f"branch={a['branch']}"
                        details += f" {self.style.purple(branch_str)}"
                    print(f"    {self.style.cyan('•')} Attempt #{a.get('number')}: {details}", file=self.stream)
            else:
                print(f"  Attempt History ({len(attempts)}):", file=self.stream)
                for a in attempts:
                    status_str = a.get('status') or 'unknown'
                    started_dt = _safe_local_dt(a['started']) if a.get('started') else None
                    started_str = started_dt.strftime('%Y-%m-%d %H:%M:%S') if started_dt else 'unknown'
                    details = f"status={status_str} started={started_str}"
                    if a.get('ended') and a.get('started'):
                        dur = max(0, int(a['ended'] - a['started']))
                        details += f" duration={dur}s"
                    if a.get('branch'):
                        details += f" branch={a['branch']}"
                    print(f"    - Attempt #{a.get('number')}: {details}", file=self.stream)

    def _transition_job(self, job_id: str, action: str):
        try:
            self.store.transition(job_id, action)
            updated = self.store.get(job_id)
            if self.color:
                print(f"  {self.style.green(Glyphs.CHECK, bold=True)} Job #{job_id} status updated to: {self.style.status_pill(updated['status'])}", file=self.stream)
            else:
                print(f"✓ Job #{job_id} status updated to: {updated['status']}", file=self.stream)
        except Exception as e:
            if self.color:
                print(f"  {self.style.red(Glyphs.CROSS, bold=True)} Failed to {action} job #{job_id}: {e}", file=self.stream)
            else:
                print(f"✗ Failed to {action} job #{job_id}: {e}", file=self.stream)

    def view_projects_and_chats(self):
        if self.color:
            print(f"\n  {self.style.cyan(f'{Glyphs.FOLDER} Active Projects & Recent Chats', bold=True)}", file=self.stream)
            print(f"  {self.style.divider()}", file=self.stream)
        else:
            print("\n=== Active Projects & Recent Chats ===", file=self.stream)

        catalog = _build_catalog(self.store)

        home_str = str(Path.home())
        active_projects = []
        is_mock = projects_mod._override_metadata is not None
        for p in catalog.projects.values():
            if not p.conversations:
                continue
            if not is_mock:
                try:
                    if not Path(p.path).exists():
                        continue
                    if p.path.startswith("/Users/") or p.path.startswith("/home/"):
                        if not (p.path == home_str or p.path.startswith(home_str + "/")):
                            continue
                except OSError:
                    continue
            active_projects.append(p)

        active_projects.sort(key=lambda p: (-p.activity, p.name.lower()))
        if not active_projects:
            print(f"  {self.style.slate('(no active projects)')}" if self.color else "  (no active projects)", file=self.stream)
        else:
            for p in active_projects:
                if self.color:
                    disp_path = format_display_path(p.path)
                    print(f"  {self.style.cyan(Glyphs.DIAMOND)} {self.style.bright(p.name)} {self.style.slate(f'({disp_path})')}", file=self.stream)
                else:
                    print(f"  • {p.name} ({p.path})", file=self.stream)
                chats = sorted(p.conversations, key=lambda c: c.activity, reverse=True)
                for c in chats[:5]:
                    title = resolve_chat_title(c.id) if c.id else (c.title or "Untitled chat")
                    if title == c.id and c.title and c.title != c.id:
                        title = c.title
                    title = sanitize_chat_title(title)
                    if self.color:
                        print(f"      {self.style.blue(Glyphs.CHAT)} {title}", file=self.stream)
                    else:
                        print(f"      - {title}", file=self.stream)

    def add_prompt_job(self):
        if self.color:
            print(f"\n  {self.style.cyan(f'{Glyphs.LOGO} Add Prompt Job', bold=True)}", file=self.stream)
            print(f"  {self.style.divider()}", file=self.stream)
            prompt_q = f"  {self.style.cyan(Glyphs.PROMPT, bold=True)} Enter prompt text (or @/path/to/file): "
        else:
            print("\n=== Add Prompt Job ===", file=self.stream)
            prompt_q = "Enter prompt text (or @/path/to/file): "

        if not self.store:
            print("  Store not available.", file=self.stream)
            return

        try:
            raw_prompt = input(prompt_q).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not raw_prompt:
            print(f"  {self.style.red('Prompt cannot be empty.')}" if self.color else "Prompt cannot be empty.", file=self.stream)
            return

        prompt = raw_prompt
        if raw_prompt.startswith("@"):
            fpath = Path(raw_prompt[1:].strip()).expanduser()
            try:
                prompt = fpath.read_text()
            except OSError as e:
                print(f"  {self.style.red(f'Failed to read prompt file: {e}')}" if self.color else f"Failed to read prompt file: {e}", file=self.stream)
                return
            if not prompt.strip():
                print(f"  {self.style.red('Prompt file is empty.')}" if self.color else "Prompt file is empty.", file=self.stream)
                return

        default_repo = os.getcwd()
        repo_q = f"  {self.style.cyan(Glyphs.PROMPT, bold=True)} Enter repository path [default: {default_repo}]: " if self.color else f"Enter repository path [default: {default_repo}]: "
        try:
            repo_input = input(repo_q).strip()
        except (EOFError, KeyboardInterrupt):
            return
        repo = str(Path(repo_input).expanduser().resolve()) if repo_input else default_repo

        if self.color:
            print(f"  {self.style.slate('Select execution mode:')}", file=self.stream)
            print(f"    {self.style.cyan('[1]')} manual {self.style.slate('(default)')}", file=self.stream)
            print(f"    {self.style.cyan('[2]')} immediate", file=self.stream)
            print(f"    {self.style.cyan('[3]')} next-reset", file=self.stream)
            mode_q = f"  {self.style.cyan(Glyphs.PROMPT, bold=True)} Enter choice [1-3]: "
        else:
            print("Select execution mode:", file=self.stream)
            print("  [1] manual (default)", file=self.stream)
            print("  [2] immediate", file=self.stream)
            print("  [3] next-reset", file=self.stream)
            mode_q = "Enter choice [1-3]: "

        try:
            mode_choice = input(mode_q).strip()
        except (EOFError, KeyboardInterrupt):
            return

        mode_map = {
            "1": "manual", "manual": "manual",
            "2": "immediate", "immediate": "immediate",
            "3": "next-reset", "next-reset": "next-reset", "reset": "next-reset",
        }
        mode = mode_map.get(mode_choice.strip().lower(), "manual")

        reset = None
        if mode == "next-reset":
            try:
                cached = self.store.db.execute("SELECT value FROM metadata WHERE key='latest_usage'").fetchone()
                if cached:
                    cdata = json.loads(cached[0])
                    cand_reset = cdata.get('five', {}).get('reset')
                    if cand_reset and cand_reset > time.time():
                        reset = cand_reset
            except Exception:
                pass
            if not reset and self.provider and not getattr(self.provider, 'live', False):
                try:
                    u = self.provider.read()
                    if u and u.five and u.five.reset and u.five.reset > time.time():
                        reset = u.five.reset
                except Exception:
                    pass
            if not reset:
                err_msg = "Cannot schedule with 'next-reset': no future reset timestamp available. Refresh usage first or choose manual/immediate."
                if self.color:
                    print(f"  {self.style.red(Glyphs.CROSS, bold=True)} {err_msg}", file=self.stream)
                else:
                    print(f"✗ {err_msg}", file=self.stream)
                return

        try:
            job_id = self.store.add(prompt=prompt, repo=repo, cwd=".", mode=mode, reset=reset)
            p_name = resolve_project_name(repo)
            if self.color:
                print(f"  {self.style.green(Glyphs.CHECK, bold=True)} Job #{job_id} successfully queued ({self.style.purple(mode)}) for project '{self.style.bright(p_name)}'!", file=self.stream)
            else:
                print(f"✓ Job #{job_id} successfully queued ({mode}) for project '{p_name}'!", file=self.stream)
        except Exception as e:
            if self.color:
                print(f"  {self.style.red(Glyphs.CROSS, bold=True)} Failed to queue job: {e}", file=self.stream)
            else:
                print(f"✗ Failed to queue job: {e}", file=self.stream)

    def refresh_usage(self):
        if self.color:
            print(f"\n  {self.style.cyan(f'{Glyphs.LOGO} Refreshing Usage', bold=True)}", file=self.stream)
            print(f"  {self.style.divider()}", file=self.stream)
        else:
            print("\n=== Refreshing Usage ===", file=self.stream)

        cfg = load_settings(self.config_path)
        codex = cfg.get("codex", "codex")
        bucket = cfg.get("bucket", "codex")

        provider = self.provider
        if provider is None:
            if self.fixture:
                from .cli import Fixture
                provider = Fixture(self.fixture, bucket)
            else:
                provider = AppServer(codex, bucket, timeout=4)

        try:
            usage = provider.read()
            if self.store and usage:
                cdata = json.dumps({
                    'five': vars(usage.five),
                    'weekly': vars(usage.weekly),
                    'observed': usage.observed,
                    'last_refresh': time.time()
                })
                with self.store.db:
                    self.store.db.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('latest_usage', ?)", (cdata,))
            if self.color:
                print(f"  {self.style.green(Glyphs.CHECK, bold=True)} Usage refreshed successfully.", file=self.stream)
                if usage.five:
                    five_rem = max(0, min(100, round(100 - usage.five.used, 4)))
                    bar = self.style.allowance_bar(five_rem, width=12)
                    print(f"    {self.style.slate('5-Hour Allowance:')} {bar} {five_rem:g}% remaining", file=self.stream)
                if usage.weekly:
                    week_rem = max(0, min(100, round(100 - usage.weekly.used, 4)))
                    bar = self.style.usage_bar(usage.weekly.used, width=12)
                    print(f"    {self.style.slate('Weekly Usage:    ')} {bar} {usage.weekly.used:g}% used ({week_rem:g}% remaining)", file=self.stream)
            else:
                print("✓ Usage refreshed successfully.", file=self.stream)
                if usage.five:
                    five_rem = max(0, min(100, round(100 - usage.five.used, 4)))
                    print(f"  5-Hour Allowance: {five_rem:g}% remaining", file=self.stream)
                if usage.weekly:
                    week_rem = max(0, min(100, round(100 - usage.weekly.used, 4)))
                    print(f"  Weekly Usage: {usage.weekly.used:g}% used ({week_rem:g}% remaining)", file=self.stream)
        except Exception as e:
            if self.color:
                print(f"  {self.style.red(Glyphs.CROSS, bold=True)} Failed to refresh usage: {e}. Retaining cached usage.", file=self.stream)
            else:
                print(f"✗ Failed to refresh usage: {e}. Retaining cached usage.", file=self.stream)

    def manage_settings(self):
        if self.color:
            print(f"\n  {self.style.cyan('⚙ Settings', bold=True)}", file=self.stream)
            print(f"  {self.style.divider()}", file=self.stream)
        else:
            print("\n=== Settings ===", file=self.stream)

        try:
            cfg = load_settings(self.config_path)
        except Exception as e:
            print(f"Error loading config: {e}", file=self.stream)
            return

        if self.color:
            print(f"  {self.style.slate('Config file:       ')} {self.config_path}", file=self.stream)
            print(f"  {self.style.slate('Interface Mode:    ')} {self.style.bright(cfg.get('interface_mode', 'tui'))}", file=self.stream)
            print(f"  {self.style.slate('CLI Mode:          ')} {self.style.bright(str(cfg.get('cli_mode', False)))}", file=self.stream)
            print(f"  {self.style.slate('Poll Interval:     ')} {cfg.get('poll_interval_seconds', 60)}s", file=self.stream)
            print(f"  {self.style.slate('Job Timeout:       ')} {cfg.get('job_timeout_seconds', 3600)}s", file=self.stream)
            print(f"  {self.style.slate('Execution Enabled: ')} {self.style.bright(str(cfg.get('execution_enabled', False)))}", file=self.stream)
            print(f"\n  {self.style.slate('Actions:')} {self.style.cyan('[t]')} Toggle interface mode  {self.style.amber('[e]')} Toggle execution  {self.style.slate('[b]')} Back", file=self.stream)
            prompt_q = f"  {self.style.cyan(Glyphs.PROMPT, bold=True)} Enter action: "
        else:
            print(f"  Config file: {self.config_path}", file=self.stream)
            print(f"  Interface Mode: {cfg.get('interface_mode', 'tui')}", file=self.stream)
            print(f"  CLI Mode: {cfg.get('cli_mode', False)}", file=self.stream)
            print(f"  Poll Interval: {cfg.get('poll_interval_seconds', 60)}s", file=self.stream)
            print(f"  Job Timeout: {cfg.get('job_timeout_seconds', 3600)}s", file=self.stream)
            print(f"  Execution Enabled: {cfg.get('execution_enabled', False)}", file=self.stream)
            print("\nActions: [t] Toggle interface mode (CLI <-> TUI)  [e] Toggle execution enabled  [b] Back", file=self.stream)
            prompt_q = "Enter action: "

        try:
            action = input(prompt_q).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return

        if action in ('t', 'toggle'):
            try:
                new_cfg = toggle_cli_mode(self.config_path)
                if self.color:
                    print(f"  {self.style.green(Glyphs.CHECK, bold=True)} Interface mode toggled to: {self.style.bright(str(new_cfg.get('interface_mode')))}", file=self.stream)
                else:
                    print(f"✓ Interface mode toggled to: {new_cfg.get('interface_mode')}", file=self.stream)
            except Exception as e:
                if self.color:
                    print(f"  {self.style.red(Glyphs.CROSS, bold=True)} Failed to toggle settings: {e}", file=self.stream)
                else:
                    print(f"✗ Failed to toggle settings: {e}", file=self.stream)
        elif action in ('e', 'exec', 'execution'):
            try:
                cfg['execution_enabled'] = not cfg.get('execution_enabled', False)
                save_settings(self.config_path, cfg)
                if self.color:
                    print(f"  {self.style.green(Glyphs.CHECK, bold=True)} Execution permission set to: {self.style.bright(str(cfg['execution_enabled']))}", file=self.stream)
                else:
                    print(f"✓ Execution permission set to: {cfg['execution_enabled']}", file=self.stream)
            except Exception as e:
                if self.color:
                    print(f"  {self.style.red(Glyphs.CROSS, bold=True)} Failed to update execution setting: {e}", file=self.stream)
                else:
                    print(f"✗ Failed to update execution setting: {e}", file=self.stream)

    def run_loop(self) -> int:
        while True:
            self.display_overview()
            print("", file=self.stream)
            if self.color:
                print(f"  {self.style.cyan('Commands:', bold=True)}", file=self.stream)
                print(f"    {self.style.cyan('[1]')} View/Manage Queue", file=self.stream)
                print(f"    {self.style.cyan('[2]')} Projects & Chats", file=self.stream)
                print(f"    {self.style.cyan('[3]')} Add Prompt Job", file=self.stream)
                print(f"    {self.style.cyan('[4]')} Refresh Usage", file=self.stream)
                print(f"    {self.style.cyan('[5]')} Settings", file=self.stream)
                print(f"    {self.style.red('[q]')} Quit", file=self.stream)
                print("", file=self.stream)
                prompt_q = f"  {self.style.cyan(Glyphs.PROMPT, bold=True)} Enter choice [1-5, q]: "
            else:
                print("Commands:", file=self.stream)
                print("  [1] View/Manage Queue", file=self.stream)
                print("  [2] Projects & Chats", file=self.stream)
                print("  [3] Add Prompt Job", file=self.stream)
                print("  [4] Refresh Usage", file=self.stream)
                print("  [5] Settings", file=self.stream)
                print("  [q] Quit", file=self.stream)
                print("", file=self.stream)
                prompt_q = "Enter choice [1-5, q]: "

            try:
                choice = input(prompt_q).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{self.style.slate('Exiting Codex Queue.')}" if self.color else "\nExiting Codex Queue.", file=self.stream)
                return 0

            if choice in ("1", "queue", "manage", "view"):
                self.view_manage_queue()
            elif choice in ("2", "projects", "chats"):
                self.view_projects_and_chats()
            elif choice in ("3", "add", "prompt", "new"):
                self.add_prompt_job()
            elif choice in ("4", "refresh", "usage"):
                self.refresh_usage()
            elif choice in ("5", "settings", "config"):
                self.manage_settings()
            elif choice in ("q", "quit", "exit"):
                print(f"  {self.style.slate('Exiting Codex Queue.')}" if self.color else "Exiting Codex Queue.", file=self.stream)
                return 0
            elif choice == "":
                continue
            else:
                msg = f"Unknown command: '{choice}'. Enter 1-5 or q."
                print(f"  {self.style.amber(msg)}" if self.color else msg, file=self.stream)


def run_cli_mode(state=None, config=None, fixture=None, provider=None, store=None, stream=None, interactive=None, color=None) -> int:
    """Run CLI mode: if interactive, run command loop; otherwise print overview and return 0."""
    out_stream = stream or sys.stdout
    if interactive is None:
        interactive = (stream is None or stream is sys.stdout) and hasattr(sys.stdin, "isatty") and sys.stdin.isatty() and hasattr(out_stream, "isatty") and out_stream.isatty()

    effective_color = color if color is not None else should_color(out_stream)

    if not interactive:
        output = render_cli_overview(state=state, config=config, fixture=fixture, provider=provider, store=store, color=effective_color, stream=out_stream)
        print(output, file=out_stream)
        return 0

    close_store = False
    if store is None:
        st_path = state or os.environ.get("CODEX_QUEUE_STATE", str(state_path()))
        try:
            store = Store(st_path)
            close_store = True
        except Exception as e:
            print(f"Error opening store: {e}", file=out_stream)
            return 1

    try:
        controller = CliController(state=state, config=config, fixture=fixture, provider=provider, store=store, stream=out_stream, color=effective_color)
        return controller.run_loop()
    finally:
        if close_store and store:
            store.close()
