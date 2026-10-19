"""Tasteful ANSI styling, colors, and nerd-font glyphs for minimal CLI mode."""
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Optional

ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    if not text:
        return text
    return ANSI_REGEX.sub("", text)


def get_divider_width(default: int = 62, max_width: int = 68) -> int:
    """Calculate terminal divider width that avoids ugly soft-wrapping in narrow windows."""
    try:
        cols = shutil.get_terminal_size((default, 24)).columns
        if cols <= 0:
            return default
        return max(32, min(cols - 4, max_width))
    except Exception:
        return default


def format_display_path(path_str: str) -> str:
    """Format path for clean CLI display, replacing home directory with ~."""
    if not path_str:
        return ""
    try:
        home = str(Path.home())
        if path_str == home:
            return "~"
        if path_str.startswith(home + "/"):
            return "~/" + path_str[len(home) + 1:]
    except Exception:
        pass
    return str(path_str)


def should_color(stream=None, color_arg: Optional[bool] = None) -> bool:
    """Determine whether color should be enabled.

    Honors explicit override, NO_COLOR (standard), FORCE_COLOR, TERM=dumb,
    and checks isatty() on the target stream.
    """
    if color_arg is not None:
        return bool(color_arg)
    if os.environ.get("NO_COLOR"):
        return False
    force = os.environ.get("FORCE_COLOR", "").lower()
    if force in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    if stream is None:
        return False
    return hasattr(stream, "isatty") and stream.isatty()


class Glyphs:
    """Unicode and Nerd Font symbols with ASCII/plain fallbacks."""
    LOGO = "⚡"
    GAUGE = "󱥸"
    FOLDER = "📁"
    CHAT = "💬"
    CLOCK = "⏱"
    DIAMOND = "◈"
    PROMPT = "❯"
    CHECK = "✓"
    CROSS = "✗"
    DOT = "●"
    HALF_DOT = "◐"
    CIRCLE = "○"
    DOTTED = "◌"
    DIVIDER = "─"
    BAR_FILL = "█"
    BAR_EMPTY = "░"


class Style:
    """Terminal styling helper supporting colored and uncolored modes."""

    def __init__(self, color: bool = True, divider_width: Optional[int] = None):
        self.color = color
        self._divider_width = divider_width

    @property
    def divider_width(self) -> int:
        if self._divider_width is not None:
            return self._divider_width
        return get_divider_width()

    def divider(self, width: Optional[int] = None) -> str:
        w = width if width is not None else self.divider_width
        return self.divider_line("─" * w)

    def bold(self, text: str) -> str:
        return f"\033[1m{text}\033[0m" if self.color else str(text)

    def dim(self, text: str) -> str:
        return f"\033[2m{text}\033[0m" if self.color else str(text)

    def cyan(self, text: str, bold: bool = False) -> str:
        code = "\033[1;36m" if bold else "\033[38;5;44m"
        return f"{code}{text}\033[0m" if self.color else str(text)

    def green(self, text: str, bold: bool = False) -> str:
        code = "\033[1;32m" if bold else "\033[38;5;78m"
        return f"{code}{text}\033[0m" if self.color else str(text)

    def amber(self, text: str, bold: bool = False) -> str:
        code = "\033[1;33m" if bold else "\033[38;5;214m"
        return f"{code}{text}\033[0m" if self.color else str(text)

    def red(self, text: str, bold: bool = False) -> str:
        code = "\033[1;31m" if bold else "\033[38;5;203m"
        return f"{code}{text}\033[0m" if self.color else str(text)

    def purple(self, text: str, bold: bool = False) -> str:
        code = "\033[1;35m" if bold else "\033[38;5;141m"
        return f"{code}{text}\033[0m" if self.color else str(text)

    def blue(self, text: str, bold: bool = False) -> str:
        code = "\033[1;34m" if bold else "\033[38;5;75m"
        return f"{code}{text}\033[0m" if self.color else str(text)

    def slate(self, text: str) -> str:
        return f"\033[38;5;244m{text}\033[0m" if self.color else str(text)

    def divider_line(self, text: str) -> str:
        return f"\033[38;5;238m{text}\033[0m" if self.color else str(text)

    def bright(self, text: str) -> str:
        return f"\033[1;37m{text}\033[0m" if self.color else str(text)

    def section_header(self, title: str, glyph: str = "", width: Optional[int] = None) -> str:
        if not self.color:
            return f"=== {title} ==="
        badge = f"{glyph} " if glyph else ""
        header = f"\033[1;36m{badge}{title}\033[0m"
        line = self.divider(width)
        return f"  {header}\n  {line}"

    def allowance_bar(self, remaining_pct: float, width: int = 12) -> str:
        """Render a mini gauge for remaining quota allowance."""
        clamped = max(0.0, min(100.0, float(remaining_pct)))
        filled = int(round((clamped / 100.0) * width))
        empty = width - filled

        if not self.color:
            return f"[{'█' * filled}{'░' * empty}]"

        fill_str = "█" * filled
        empty_str = "░" * empty

        if clamped <= 0:
            return f"\033[38;5;240m[\033[0m\033[38;5;203m{empty_str}\033[0m\033[38;5;240m]\033[0m"
        elif clamped < 20:
            color_code = "\033[38;5;214m"  # warm amber
        elif clamped < 50:
            color_code = "\033[38;5;44m"   # teal
        else:
            color_code = "\033[38;5;78m"   # emerald green

        return (
            f"\033[38;5;240m[\033[0m"
            f"{color_code}{fill_str}\033[0m"
            f"\033[38;5;238m{empty_str}\033[0m"
            f"\033[38;5;240m]\033[0m"
        )

    def usage_bar(self, used_pct: float, width: int = 12) -> str:
        """Render a mini gauge for used quota."""
        clamped = max(0.0, min(100.0, float(used_pct)))
        filled = int(round((clamped / 100.0) * width))
        empty = width - filled

        if not self.color:
            return f"[{'█' * filled}{'░' * empty}]"

        fill_str = "█" * filled
        empty_str = "░" * empty

        if clamped > 80:
            color_code = "\033[38;5;203m"  # coral red
        elif clamped > 50:
            color_code = "\033[38;5;214m"  # warm amber
        else:
            color_code = "\033[38;5;78m"   # emerald green

        return (
            f"\033[38;5;240m[\033[0m"
            f"{color_code}{fill_str}\033[0m"
            f"\033[38;5;238m{empty_str}\033[0m"
            f"\033[38;5;240m]\033[0m"
        )

    def status_pill(self, status: str) -> str:
        s = (status or "").lower()
        if not self.color:
            return f"[{status}]"
        if s == "running":
            return f"\033[1;32m⚡ RUNNING\033[0m"
        elif s == "queued":
            return f"\033[38;5;75m◈ QUEUED\033[0m"
        elif s == "paused":
            return f"\033[1;33m⏸ PAUSED\033[0m"
        elif s == "cancelled":
            return f"\033[38;5;244m✗ CANCELLED\033[0m"
        elif s == "failed":
            return f"\033[1;31m✗ FAILED\033[0m"
        elif s == "done":
            return f"\033[1;32m✓ DONE\033[0m"
        else:
            return f"\033[38;5;244m[{status}]\033[0m"

    def connection_pill(self, conn_status: str) -> str:
        if not self.color:
            return conn_status
        cs = conn_status.lower()
        if "disconnected" in cs:
            return f"\033[1;31m{Glyphs.CROSS} {conn_status}\033[0m"
        elif "connected (stale)" in cs:
            return f"\033[1;33m{Glyphs.HALF_DOT} {conn_status}\033[0m"
        elif "connected" in cs:
            return f"\033[1;32m{Glyphs.DOT} {conn_status}\033[0m"
        elif "cached (stale)" in cs:
            return f"\033[38;5;214m{Glyphs.DOTTED} {conn_status}\033[0m"
        elif "cached" in cs:
            return f"\033[38;5;75m{Glyphs.CIRCLE} {conn_status}\033[0m"
        else:
            return f"\033[38;5;244m· {conn_status}\033[0m"
