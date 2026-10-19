# Herdr integration and TUI review

This enhancement stays inside the existing queue repository. Existing uncommitted work was preserved; no commit, merge, push, release, account query, inference, scheduler activation, or live Herdr workspace mutation was performed.

## Implemented

- Optional extension registry and lazy Extensions → Herdr management screen, default disabled.
- Shared Direct/Herdr backend selection for foreground and OS checks; persisted per-job overrides and explicit non-running job backend changes.
- Managed workspace ownership tokens, explicit adoption of an existing workspace, per-job tabs/root panes, absolute cwd/shell validation, no focus stealing by default.
- Exact-session resume command with no replacement session on failure; existing ownership/quota/worktree checks retained.
- Private pane relay, stdin prompts, actual terminal output, durable submission fence, inherited local repository/worker locks and exit receipts.
- Unknown outcomes never automatically resubmit. Stop watching leaves Herdr jobs running; interruption is a separate confirmed action.
- Compact charcoal/navy dashboard and teal selections, queue semantic colors, responsive layout, pinned narrow-terminal totals, usage view, help, visible focus and preserved prompt edits.

## Verification boundaries

Baseline: **117 existing tests passed** before implementation. Final full suite: **145 tests passed**. Targeted checks also passed: **17 backend tests** and **2 keyboard/detail regression tests**. Coverage includes a large prompt against a child that never reads stdin. Compilation and `git diff --check` passed.

Textual Pilot tests exercise keyboard new-job and exact-session flows; prompt newlines and question marks; selectors; Tab/Shift+Tab; list/table arrows; Enter/Escape; queue details/actions/backend selection; integration opt-in; safe confirmation cancellation/confirmation; usage refresh; disabled watcher permission; settings/scheduler/extension navigation; command palette and keyboard help. Mouse selection is checked separately. Terminal dimensions tested: 80×24, 100×30, 120×40, 160×45.

Herdr CLI discovery used the actual installed **0.9.3** version, command help and bundled **protocol 22** schema. All workspace/pane dispatch tests used mocked CLI responses. Pane relay tests ran harmless Python children in disposable repositories. **No real Herdr/Codex job was launched.** Native hooks/status detection, live inference, Linux operation and remote forwarding are untested. The Herdr execution adapter explicitly rejects Windows.

Wheel/source builds, Twine metadata checks, and a temporary no-dependency wheel installation/import/CLI-help check passed. The existing local installation is editable and points to this repository. Its source changes appear after restarting `codex-queue`.

The basic publication audit reports clean working tree and repository checks. History was not rewritten.

## Changed files for this enhancement

New: `codex_queue/backends.py`, `herdr.py`, `herdr_runner.py`, `extensions_ui.py`, `tests/test_herdr.py`, `docs/integrations/herdr.md`, `docs/keyboard.md`, this review, and `docs/screenshots/` captures.

Updated: `codex_queue/worker.py`, `store.py`, `foreground.py`, `cli.py`, `settings.py`, `navigation.py`, `tui.py`; `tests/test_interactive.py`; `README.md`, `QUICKSTART.md`, `CONTRIBUTING.md`, `docs/architecture.md`, and `MANIFEST.in`. Other dirty files predate this enhancement.

## Update/install

For the existing editable development installation: close and reopen `codex-queue`. No reinstall is needed. To reproduce in a portable checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/codex-queue
```

For the built wheel (not published):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./dist/codex_queue-0.2.0-py3-none-any.whl
.venv/bin/codex-queue
```

On Windows use `.venv\Scripts\python.exe` and `.venv\Scripts\codex-queue.exe`; this is standard syntax, not a claim of Windows Herdr qualification.

## User workflow

1. Extensions → Herdr → configure executable/local session → Check connection → explicitly enable → Save. This enables neither inference nor OS scheduling. Follow official hooks setup manually if diagnostics show it missing.
2. For a same-name existing workspace, explicitly adopt its exact ID with `ADOPT`, or choose a separate workspace name before first dispatch. User-owned tabs remain intact.
3. New conversation: select project → Start New Conversation → write/load prompt → schedule/backend → review → Add to queue.
4. Existing conversation: select the original listed session or paste its UUID → preserve original cwd → confirm the prior owner stopped → prompt/schedule/review → Add to queue.
5. Only after execution is separately authorized, Settings → Permit real execution, then explicitly Watch Queued Jobs. Scheduling and backend choice are independent.
6. Queue → Details → Open in Herdr focuses the managed job tab; the real relay/child output is visible there. No manual `cd` or duplicate Codex launch is needed.
7. Stop monitoring or Ctrl+C stops new dispatches and preserves queued jobs. Active Herdr jobs continue. To stop one, use Details → Interrupt managed job and confirm.
8. Review interrupted/failed work before retrying. Unknown/missing receipts cannot be forced into a retry through the normal API. See the documented manual-recovery limitation.

## Actual render captures

These are genuine Textual headless render captures of synthetic empty state, converted from exported SVG to PNG; they are not captures of a live Herdr job.

- [Wide dashboard](screenshots/dashboard-120x40.png)
- [Narrow dashboard](screenshots/dashboard-80x24.png)
- [Herdr settings](screenshots/herdr-100x30.png)

Read [Herdr integration](integrations/herdr.md), [keyboard controls](keyboard.md), and [quickstart](../QUICKSTART.md) for full behavior and remaining limitations.
