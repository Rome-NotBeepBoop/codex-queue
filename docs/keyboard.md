# Keyboard navigation

Keyboard navigation works by default; mouse actions are still available.

| Key | Action |
| --- | --- |
| Up / Down | Move highlighted list/menu/table row |
| Left / Right | Move dashboard tabs; table columns / selector and editing controls |
| Enter | Activate list selection, focused button or selector; newline in prompt editor |
| Tab / Shift+Tab | Next / previous focusable control; focused controls scroll into view |
| Escape | Back, close selector/dialog, cancel confirmation; wizard retains prompt edits |
| Ctrl+P | Textual command palette |
| 1–5 | Switch primary tabs outside text-entry controls and confirmation dialogs |
| ? | Keyboard help outside text-entry controls |
| Ctrl+C | Stop watching and exit; dispatched Herdr jobs can remain running |

The multiline editor uses Enter for newlines and supports terminal paste. Tab to the explicit **Choose schedule** button and Enter to continue; a portable Ctrl+Enter encoding is not assumed. Selection focus uses a muted teal indicator; button/input focus has a visible accent border. Confirmation dialogs focus **Cancel** first; Tab to Confirm and Enter deliberately.

All primary sections share the same fixed title and horizontal numbered tabs. Switching sections replaces the body and retains project/prompt drafts in memory. Opening Watch is passive; Start monitoring is explicit. At 80 columns its content panels stack and scroll; at 100 columns and above the metrics remain in a shared three-column container and Quick Actions / Queue State sit side by side. At 120×35 the dashboard fits without scrolling. More… retains history, usage, scheduler management and extensions. All controls remain in the same keyboard focus order. The dashboard does not query an account or start a watcher on launch. Project entries show project name, directory, then activity and session count on separate lines.
