# codex-queue

A lightweight, interactive, usage-aware task queue and session scheduler for OpenAI Codex CLI. Local-first, foreground-first, and user-controlled. An independent community project, **not an official OpenAI product**.

**Experimental preview:** APIs and behavior may change. Automated tests use mocked Codex responses and harmless local fixtures. Real Codex execution, session continuation, and native scheduler operation are not fully qualified. Keep execution disabled until you have reviewed the settings and tested on a disposable project you own.

Open your terminal and run `codex-queue`. Choose a project and conversation (or Start New), edit a prompt, select a schedule and review it before queuing. Open **Watch**, then explicitly press **Start monitoring** for the default foreground mode. Nothing monitors or executes on startup. Closing stops its watcher and interrupts Direct Codex work with logs and partial changes preserved. Optional Herdr jobs survive stopping monitoring and require a separate confirmed interruption.

## Features

- Textual dashboard, progressive project/conversation navigation, multiline editing, preview/review, queue management and live execution logs.
- Optional isolated per-user launchd, systemd timer and Task Scheduler integrations. Configure and install disabled; explicit opt-in to enable.
- One scheduler shared by the TUI and existing CLI worker; SQLite state and attempt history.
- Manual, immediate-when-available, and next-five-hour-reset scheduling.
- Exact-session continuation in its original checkout; no silent new-session fallback.
- Fresh quota checks, weekly limits, persistent network backoff and conservative crash recovery.
- New prompt jobs use isolated Git worktrees. No automatic commits, merges, pushes or publication.
- Mock usage and a fixed harmless simulation for development without Codex inference.
- Optional **Extensions → Herdr** backend: managed per-job tabs with explicit directories, exact session continuation, private exit receipts and no automatic prompt replay. Disabled by default; local POSIX adapter is experimental and mock-tested against Herdr 0.9.3/protocol 22.
- Reference dashboard with horizontal numbered tabs, one three-column allowance panel, thin usage tracks and balanced Quick Actions / Queue State panels. Shared dark styling, muted selection and [keyboard focus controls](docs/keyboard.md). See [actual renders and review](docs/reference-ui-review.md).

## Requirements and installation

Python 3.11+, Git, a Textual-compatible terminal, and your own authenticated Codex CLI for real usage/execution. Ghostty is suitable. No shared developer key or mandatory cloud service is required by this application.

The package is **not published to PyPI yet**. Install a downloaded, reviewed wheel without cloning:

```sh
pipx install ./codex_queue-0.2.0-py3-none-any.whl
uv tool install ./codex_queue-0.2.0-py3-none-any.whl
# Or use a virtual environment:
python -m venv .venv
.venv/bin/python -m pip install ./codex_queue-0.2.0-py3-none-any.whl
```

On Windows, the virtual environment executables are under `.venv\Scripts\`. These are standard installation methods; local wheel/pip installation is verified on macOS. pipx/uv and Windows/Linux installation await their own qualification. Once a PyPI release is approved and published, these commands may use `codex-queue` as the package name instead of a wheel path.

## Quickstart

```sh
codex-queue
codex-queue --help
codex-queue add --help
codex-queue worker --help
```

See [QUICKSTART.md](QUICKSTART.md) for the complete workflow. Startup performs local queue reads only; account usage initially shows **Unknown**. Select **More… → Usage & Reset Times** to refresh through Codex App Server, or start monitoring explicitly. Those actions start the user's Codex CLI for a supported read-only account query.

See [Herdr setup and lifecycle](docs/integrations/herdr.md) before enabling the optional backend. It never installs hooks, starts a Herdr server, adopts an existing workspace, or enables OS scheduling silently. Backend choice is independent of scheduling. Real execution remains separately gated. The adapter has not been qualified with live Codex/Herdr inference; Windows execution through it is unavailable.

The TUI uses ↑/↓ and Enter for the menu, Tab for form fields, Esc to go back and Ctrl+C for graceful exit. Real execution requires `execution_enabled=true` in Settings (type `ENABLE`) **and** explicitly starting Watch, or separately opting into managed OS scheduling. Changing Settings alone starts no worker. Manual jobs are excluded from automatic pickup and use `run JOB --execute` explicitly.

## CLI compatibility

All existing subcommands and flags are preserved: `add`, `list`, `show`, `pause`, `resume`, `cancel`, `retry`, `usage`, `doctor`, `run`, `worker`. Use the `codex-queue` entry point and `CODEX_QUEUE_STATE` / `CODEX_QUEUE_CONFIG` environment variables.

```sh
codex-queue add prompt.md --repo /path/to/project --mode manual
codex-queue list
codex-queue show JOB_ID
codex-queue pause JOB_ID
codex-queue resume JOB_ID
codex-queue cancel JOB_ID
codex-queue retry JOB_ID
codex-queue run JOB_ID --dry-run
```

Global `--state`, `--config` and `--fixture` options precede the subcommand. `run`/`worker` retain `--dry-run`, `--simulate`, `--execute`, and worker retains `--once`. Simulation requires a fresh fixture and cannot authorize real execution. CLI real execution needs `--execute` plus the external config gate.

## Reset monitoring

`next-reset` records a fresh five-hour reset when queued. Execution requires both elapsed time and a fresh response reporting that the reset advanced. Already-available quota at enqueue time does not bypass the saved boundary. Missing/stale/inconsistent data, unknown buckets, authentication errors and weekly exhaustion block execution. Polling defaults to 60 seconds, with exponential error backoff up to one hour. No credits are bought or reset allowances consumed.

These are one-shot jobs, not recurring prompt resubmission. Sleep/offline periods delay execution. In Foreground Watcher mode the application must stay open. OS Scheduler mode invokes the same engine for one check and exits after any eligible job completes. A scheduled check can run for the configured job timeout; it is not an always-running daemon.

## Projects and conversations

**Projects & Conversations** and **Add Job** open the same progressive flow: Project → Conversation → Prompt → Schedule → Review → Add. Discovery is explicit; the app does not scan Codex data at startup. The documented read-only `thread/list` method loads one page at a time, covering local CLI, editor, exec and App Server sources. Metadata stays in memory. The queue's own history and manually registered repositories also appear. No GitHub connection is used.

Projects group by reported project/repository root when supplied, otherwise by Codex's original working directory. Different checkouts remain distinct. Search titles, source names or UUIDs; the first option always starts a new conversation. Previews use the summary supplied by Codex, with a clear unavailable message if absent. Discovery support varies with installed Codex versions; manual repository and UUID entry remains available. Only the explicitly selected repository is checked by Git.

The built-in multiline editor supports terminal paste and loading UTF-8 `.md`, `.markdown` or `.txt` files up to 1 MB. Review shows the original/new conversation, prompt, directory, schedule and current execution mode. Back preserves edits. No job is persisted before Add. Existing sessions are validated at submission and again immediately before execution; new jobs never use resume.

## Execution modes and managed scheduling

**Settings → Execution Mode** selects Foreground Watcher (default) or OS Scheduler without launching anything. **Scheduler Management** provides Configure only, Install disabled, Enable, Disable, Uninstall, actual native status and private logs. Type `INSTALL`, `ENABLE` or `UNINSTALL` for those respective operations. Enabling also requires the real-execution gate. Windows check intervals must be at least 60 seconds; macOS/Linux accept 10–3600 seconds. Installation saves the engine's matching polling interval.

The app manages one state-specific, per-user scheduler object: launchd on macOS, a systemd user timer/oneshot service on Linux, and an interactive-token Task Scheduler task on Windows. It never installs a root/system-wide service, stores passwords, uses SYSTEM, or rewrites a crontab. A missing systemd user session is reported as unavailable; no cron fallback is silently installed. Native integration is experimental and has only mocked tests on this Mac.

Switching to foreground disables the owned native scheduler first. Switching to OS stops the local watcher before selection or enable. A durable mode record and kernel file lease serialize worker lifetimes across TUI/CLI/OS processes. Active leases block switching; do not delete lock files. Unix children inherit worker/mode leases, while Windows children use owned Job Objects. Crash recovery preserves jobs and pauses uncertain attempts for review. Modified or unowned definitions are rejected. Native status comes from the OS query, with errors/unknown states shown separately from desired mode. Other schedulers and earlier unmanaged deployment services are not adopted or changed; stop legacy workers yourself before migrating. See [scheduler guide](docs/schedulers.md).

## Session continuation and ownership

Select an existing conversation in **Projects & Conversations**, or:

```sh
codex-queue continue-session SESSION_UUID --repo /path/to/original/project --cwd . --mode manual --confirm-ownership
```

This is distinct from `resume JOB_ID`, which still unpauses a queue job. Discover accessible sessions explicitly, select one from queue history, or enter the complete UUID. No session rollout files or credential files are scanned by this app. A continuation runs `codex exec resume SESSION_UUID` in the original directory, retaining context and existing changes. Missing IDs, mismatched directories, active/unknown session status or incompatible CLI interfaces block it.

**You must stop the original session and confirm exclusive ownership.** Queue locks coordinate queue workers; unrelated editors/agents do not automatically honor those locks. App Server status is scoped to its runtime and is not universal proof that every external agent has stopped. Do not queue a checkout owned by another agent. If ownership is uncertain, leave it paused and resolve that uncertainty yourself. Retries continue the same session with partial work preserved; they never create a replacement session.

Codex CLI `0.162.0` help and the [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode) establish the resume syntax. Actual authenticated resumption and enforcement of resumed-session sandbox policy remain **not verified with real inference**; test on a disposable owned project before relying on unattended continuation.

## Privacy and security

Configuration, SQLite, logs and worktrees default to platform-specific user directories outside the repository. `CODEX_QUEUE_STATE` and `CODEX_QUEUE_CONFIG` override defaults. Prompts/logs may contain private source information: keep them private. No auth tokens are stored in queue settings; Codex handles the user's supported authentication. Source credentials and deployment credentials should not be ambient to the worker. Review [SECURITY.md](SECURITY.md).

## Platform support and limitations

macOS Apple Silicon: mocked scheduler tests, Textual headless interaction, packaging and installation tested. Cross-platform GitHub Actions validates mocked tests, packaging, and CLI help on Linux, macOS, and Windows with Python 3.11, 3.13, and 3.14. These checks do not qualify native platform execution. Intel macOS, Linux x86/ARM, and Windows 10/11 remain targets requiring real execution testing. Physical Ghostty interaction and real account/quota/inference tests remain open.

Rate-limited/authentication-failed jobs are recorded and require explicit review/retry; they are not automatically resubmitted. No cloud-backed Codex conversations, browser ChatGPT chats or missing local sessions are converted into new sessions. Session compatibility is checked before real continuation. An interrupted prompt job retries from its pinned commit in a new worktree; an interrupted session job reuses the original checkout.

## Troubleshooting

- **Unknown usage:** refresh explicitly; missing data never authorizes work.
- **Execution disabled:** enable the config gate deliberately, then choose Watch or use CLI `--execute`.
- **Another writer / ambiguous ownership:** stop the original agent and review checkout ownership; never force a lock.
- **Interrupted:** review attempt logs and partial work before Retry.
- **Session unavailable:** check the UUID, original directory, local availability and CLI version. There is no fallback.
- **Command missing:** use your installer-managed executable or add its bin directory to PATH.

## Development

```sh
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -v
python -m build
```

Use the environment's Python (`.venv/bin/python` or `.venv\Scripts\python`). See [CONTRIBUTING.md](CONTRIBUTING.md). Tests use mocked usage and harmless temporary Git fixtures; they must not touch unrelated projects or real Codex sessions. MIT license; dependencies retain their own licenses.
