# Project navigation and execution modes — local review

Updated 2026-10-11. Work remains inside the existing standalone codex-queue repository. No other project runtime was accessed or modified.

## Delivered

- Dashboard, Projects & Conversations, Add Job, Watch Queued Jobs, Queue Management, Execution History, Usage & Reset Times, Scheduler Management, Settings and Exit.
- Progressive project → conversation/new → multiline prompt → independent schedule → review → queue workflow, with breadcrumbs, back/edit preservation, search, metadata preview and file loading.
- Explicit read-only session discovery, pagination, project counts/activity/source/title, manual repositories/UUIDs and queue history. No live sessions were queried during development.
- Existing-session validation at submission and execution, original directory/context, ownership acknowledgement, no replacement-session fallback. New jobs use fresh exec in an isolated worktree.
- Default foreground watcher plus optional, isolated per-user launchd/systemd timer/Task Scheduler adapters. Configure/Install disabled/Enable are separate actions; mode selection starts no worker. Scheduler status/logs are available through the TUI.
- One shared Worker engine; OS checks call it once and exit. Mode/worker/Git leases protect against overlap and uncertain attempts require review after crashes. Native mutations verify both definition and loaded origin/action; permission failures are reported without overwriting unrelated tasks.
- Updated README, interactive quickstart, installation, scheduler, security and contribution guides, plus mocked cross-platform CI.

## Validation and boundaries

115 local tests passed on macOS Apple Silicon/Python 3.14, using synthetic session metadata, mock usage, temporary Git repositories/homes, harmless Python children and mocked native commands. Includes TUI navigation/search/review, prompt loading/back/resize, exact session submission, mode handoffs, crash leases, ownership refusal, installation rollback and one-shot delegation. Python compilation, installed CLI help, package build/metadata and whitespace checks passed.

The installed source environment is editable and uses these changes. The actual Mac config was checked: real execution false, foreground mode, managed OS scheduling false. No native task was installed/enabled/disabled/uninstalled on this Mac. No real Codex prompt, account/session query, server connection, commit, push or publication occurred during this extension.

Native macOS/Linux/Windows activation, logged-in/out behavior, Windows child containment, real session continuation and physical Ghostty interaction remain unqualified. Remote CI has not run. Native adapters are implemented and mock-tested, not certified by those tests. Use a separately approved disposable platform before activating them.

Current tracked and nonignored files pass the configured basic patterns. History remains unchanged. This is a basic pattern audit, not a comprehensive credential scan.

See [Interactive quickstart](../QUICKSTART.md) and [Scheduler guide](schedulers.md). Launch the installed application with `codex-queue`; creating/reviewing a job never starts execution.
