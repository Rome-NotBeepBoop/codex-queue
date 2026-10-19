# Interactive quickstart

1. Install the reviewed wheel and run `codex-queue`. The dashboard shows queue totals and initially unknown usage. Startup reads only this queue's local state; no watcher, native scheduler or Codex query starts.
2. Press **2 Projects**, or select **Continue a session** / **New prompt** in Quick Actions. Choose **Discover Codex projects** to request read-only local session metadata, or enter a Git repository path and **Add repository**. No GitHub integration is needed. Discovery is paginated; Load more adds another page. Project rows show project name, then directory, then last activity and session count.
3. Choose a project. **Start New Conversation** is always first. Search existing titles/sources/IDs, highlight a conversation to see its supplied preview, then press Enter or **Continue with selected conversation**. No ID needs to be copied for listed conversations. **Use pasted session ID** is only for a UUID typed/pasted into the manual box, not the highlighted row. Busy/unavailable sessions cannot be selected. Existing sessions must retain their original working directory and be released by their previous owner.
4. Write or paste into the multiline prompt editor. Optionally load a Markdown/text file. Review and edit its content. For an existing session, confirm exclusive ownership. New jobs need a clean committed repository and execute in an isolated worktree; existing sessions preserve dirty original files.
5. Choose **Manual**, **Immediately when eligible**, or **After next five-hour reset**. The schedule is independent of execution mode. Next-reset reads the real quota boundary when Add is pressed (or uses a development fixture); fresh usage and an advanced reset are required before execution. Manual jobs use the explicit CLI `run JOB --execute` while in foreground mode.
6. Review project, conversation, directory, prompt, schedule and current execution mode. **Back** preserves edits. **Add to queue** persists the job; it never starts execution. Missing sessions fail without creating a replacement.
7. Default **Foreground Watcher**: open Settings, deliberately permit real execution only when authorized, then open **Watch Queued Jobs** and press **Start monitoring**. The TUI remains open and executes eligible jobs through the shared engine. Stop monitoring or Ctrl+C stops launches; it interrupts Direct Codex children, but dispatched Herdr jobs remain running. Partial changes/logs and queued jobs survive.
8. Optional **OS Scheduler**: open **5 Settings → Execution Mode** and select OS Scheduler. This stops the watcher but does not enable scheduled execution. In **More… → Scheduler Management**, choose a check interval and **Configure only**, then type `INSTALL` and **Install disabled**. Review actual status and logs. Only after authorization, permit real execution in Settings, type `ENABLE` and **Enable OS scheduler**. Every native invocation checks the queue once and exits after the eligible attempt ends.
9. To return to foreground, Apply Foreground Watcher. The app disables its managed native scheduler before switching, and refuses if another process still owns the execution lease. Watch still requires a separate start action. **Disable** preserves scheduler definitions; type `UNINSTALL` to remove only this queue's verified owned task and retain queue data/logs.
10. Use **Queue Management** and **Execution History** for Details, Pause, Resume, Cancel and Retry. Queue Resume unpauses a job; conversation selection resumes context. Interrupted jobs require review and explicit Retry.

Native scheduling has mocked tests only; do not treat Linux/Windows or live macOS activation as qualified. Keep real execution disabled during development. No server connection, inference or service activation is required for the automated tests.

## Optional visible Herdr execution

Open **More… → Extensions → Herdr**, configure the local session, check the connection, then explicitly enable and Save. This starts no jobs and enables no scheduler. Existing `codex-queue` workspaces require explicit typed adoption, or a separate name; user-owned panes remain untouched. During the Schedule step, choose an integration default or per-job Direct/Herdr override. Schedule and backend are independent.

New conversation: choose project → Start New → edit prompt → schedule/backend → review → Add to queue. Existing conversation: select its original listed conversation (or paste its complete UUID), preserve the original directory, confirm it is stopped and exclusively available, then follow the same review workflow. No fallback creates a replacement session.

After real execution is separately authorized, choose Watch and press Start monitoring. Eligible jobs become visible in managed Herdr tabs. Open **Queue Management → Details → Open in Herdr** to focus the tab. Stop monitoring prevents future dispatches; use **Interrupt managed job**, with confirmation, to stop an active Herdr child. An unknown receipt/dispatch never automatically retries.

Read [Herdr prerequisites and limitations](docs/integrations/herdr.md) and [keyboard controls](docs/keyboard.md). Use arrows/Enter for lists and Tab/Shift+Tab for controls; Enter inserts prompt newlines. `?` opens help outside text fields. Mouse remains supported.

## CLI examples

```sh
codex-queue add prompt.md --repo /path/to/project --mode manual
codex-queue continue-session SESSION_UUID --repo /path/to/project --mode next-reset --confirm-ownership
codex-queue --config /path/to/config.json worker --once --execute
codex-queue --fixture /path/to/mock-usage.json --state /path/to/test-state
```

The last command opens fixture/simulation mode. Watch executes only a fixed harmless writer in disposable repositories. Fixtures expire after 120 seconds. Never select an externally owned checkout for simulation. `scheduled-check` is reserved for the managed native integration and requires both OS mode/permission and the execution config gate.

## Dashboard navigation

Use Left/Right and Enter on the horizontal tabs, or 1–5 outside text fields and confirmation dialogs. Tab/Shift+Tab moves between tabs, Quick Actions and queue buttons. Up/Down selects Quick Actions; Enter opens one. More… keeps history, usage, scheduler management, extensions and Exit available. Escape goes back and ? shows help outside editors. A 120×35 terminal shows the complete dashboard; smaller terminals scroll focused controls into view. See the [rendered reference review](docs/reference-ui-review.md).

The CODEX / QUEUE title and numbered tab bar stay at the top of Projects, Queue, Watch and Settings. Switching tabs changes the body below and preserves unfinished project/prompt edits for this application run. Watch opens stopped; tab switching starts no worker. Saved jobs remain persistent across restarts; an unsubmitted editor draft is retained only while the application remains open.
