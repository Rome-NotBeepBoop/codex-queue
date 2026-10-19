# Optional Herdr execution backend

Herdr is disabled by default. Direct Codex remains usable without it. This is an experimental **local POSIX** adapter, researched against installed Herdr 0.9.3 and its bundled protocol 22 schema. macOS tests use mocked CLI responses and harmless Python executions. Real Codex dispatch and native agent detection have not been qualified. Linux runtime is untested; Windows execution through this adapter is explicitly unavailable. No remote machine forwarding is implemented.

## Setup and enabling

Install Herdr separately and manually open its intended local session. Queue does not install Herdr, start its persistent server, modify Codex hooks, or enable OS scheduling. Official references: [CLI](https://herdr.dev/docs/cli-reference/), [socket API](https://herdr.dev/docs/socket-api/), [integrations](https://herdr.dev/docs/integrations/), [source](https://github.com/herdrdev/herdr).

1. Start `codex-queue` and open **Extensions → Herdr** using arrows and Enter.
2. Set its executable, local session (normally `default`), and workspace name (normally `codex-queue`). Save configuration with the integration switch off if configuring only.
3. Choose **Check connection**. It checks version/schema, lists workspace metadata, and reports the official Codex integration's `current`, `outdated`, or `not_installed` state. It does not inspect other terminal output or start agents. Follow Herdr's official integration setup manually if hooks are missing; noninteractive queue execution does not depend on those hooks, and native semantic agent status may remain unknown.
4. Turn the integration switch on and Save. This selects Herdr as the default execution backend, but does not permit real execution or start watching. Keep real execution disabled until explicitly authorized.
5. Leave automatic focusing off unless you want new jobs to steal focus. **Open workspace** focuses only the recorded managed workspace; it does not launch/attach a Herdr client.

Disabling the integration selects Direct Codex for future jobs using the default backend. It does not interrupt already dispatched jobs. Stop watching before changing integration settings. At the job's Schedule step, **Direct Codex** and **Herdr** override the integration default independently of Manual / Immediate / Next reset. For a non-running job, **Queue Management → Details → Save backend choice** lets you explicitly change backends without starting it.

## Existing workspaces and ownership

An existing workspace with the configured name is never automatically adopted. A recorded ID is reused only in the recorded local session and with its matching queue ownership token. Recycled IDs or missing/changed ownership markers block dispatch. Missing IDs block dispatch. If a name collision exists, **Check connection / Diagnostics** shows workspace IDs. Paste the intended ID, type `ADOPT`, and choose **Adopt workspace** to explicitly allow adding new queue tabs. Existing tabs/panes/processes are left alone. Alternatively, choose a different workspace name before the first managed workspace is created.

The first job creates the workspace without changing focus, using its initial tab/root pane. Subsequent jobs create their own tab/root pane. Each tab gets the job's validated absolute working directory, not the workspace root. Before submitting a command, the adapter verifies pane/workspace/tab identity, cwd, and that its shell alone owns the foreground. It never sends `cd` to an existing terminal, closes a workspace, or terminates an unowned pane. Workspace/tab/pane IDs stay in private state outside Git.

## Execution and resumption

The shared Worker decides eligibility, checks fresh five-hour and weekly allowance, acquires queue/repository locks, validates session ownership, and prepares the existing isolation policy. New conversations run in a new isolated worktree pinned to the queued commit. Existing conversations retain their original exact session UUID and checkout, including uncommitted files; busy/unavailable/mismatched sessions block without falling back to a new conversation.

The backend uses documented `pane run` to launch a private Python relay **inside the newly managed Herdr pane**. That relay starts the same sandboxed noninteractive `codex exec` or `codex exec resume UUID` command as Direct Codex. Noninteractive execution supplies reliable exit codes and JSON output; it intentionally does not provide an interactive Codex input UI. Prompt text is loaded from a 0600 request file into stdin, never shell arguments. The relay tees output to the actual Herdr terminal and private attempt log. Shell arguments contain only safely quoted Python/relay/request paths.

Before inference, the relay rechecks allowance and session availability. Its atomic submission fence prevents a repeated shell command from submitting the prompt twice. It holds the same worker and common-Git locks; its child inherits them on POSIX. The dispatch fence is committed before `pane run`. A timeout/disconnection after dispatch is **unknown**, not a failed job eligible for automatic retry. No invisible Direct Codex fallback is allowed.

## Watching, results, and interruption

Choose **Watch Queued Jobs** explicitly after execution is authorized. Foreground monitoring and OS scheduled checks both call Worker.once and select the same backend. Herdr never activates an OS scheduler. Native scheduled checks dispatch and exit; later checks reconcile receipts.

**Stop monitoring / Ctrl+C:** stops additional dispatches, releases the foreground lease, preserves queued jobs, and leaves dispatched Herdr processes running in Herdr's persistent server. Direct Codex retains its previous behavior: stopping monitoring interrupts its directly owned child. Do not confuse these actions.

**Queue Management → Details → Open in Herdr:** focuses the selected job's managed tab. **Interrupt managed job** requires a confirmation, then sends a private token-matched request to the relay. Running Herdr Pause/Cancel buttons also require confirmation. Interruption is cooperative; if the relay is gone, the application cannot promise the child has stopped. It never sends keys to another user's process. Private logs/partial work survive.

Only a matching private runner receipt with a recorded exit code can finish an attempt. Exit code zero means completed; nonzero means failed; explicit interruption/timeouts preserve work and pause/cancel. Herdr's visual `done` state alone is never used as proof. Resume identity mismatches fail. The TUI/next worker check reconciles receipts after reopening. Unknown/unconfirmed attempts stay running with attention information and cannot be retried through the normal transition API, preventing overlap.

## Limitations and recovery

- Development used no live Herdr workspaces and no Codex inference. Real execution, hooks and Linux operation need separately authorized acceptance tests in a disposable repository; Windows shell execution and SSH targets are unsupported here.
- A server reboot, closed pane, relay crash, or lost create response can leave a managed/orphan tab or an unknown run. There is no automatic pane cleanup, restart or prompt replay. Review the private request/log/receipt and managed pane. No automatic “mark successful” or force-unlock is provided. A definitive recovery workflow for an absent receipt remains manual; do not reset/retry an uncertain run while its child might survive.
- JSON output is visible in Herdr rather than an interactive Codex interface. Herdr native working/idle/session restore signals are best-effort and never overwritten by this adapter. Queue does not register a restart/resume command that could replay a prompt after a server restart.
- Locks protect cooperating queue workers on one local host/disk. They cannot establish ownership of an unrelated editor or agent; the existing explicit ownership/session checks still apply.
- Store/request/log files contain private prompts and project paths. Never commit or publish them. The public screenshots are real Textual headless render captures using empty synthetic state.
