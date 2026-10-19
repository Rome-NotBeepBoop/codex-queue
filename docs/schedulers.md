# Optional managed execution modes

The default is a foreground watcher that runs only after Watch is selected and stops on app exit. Mode selection is separate from each job's manual/immediate/next-reset schedule. OS scheduling is optional and experimental; no native job was activated during development.

## User flow and permission boundaries

Open Settings → Execution Mode. Apply Foreground disables this queue's owned scheduler before changing mode; Watch is still a separate action. Apply OS stops the local watcher and saves an inactive OS selection. It does not install or enable a native job.

Open Scheduler Management:

1. Configure only stages inert definitions in the private state directory.
2. Type INSTALL, then Install disabled. This places/registers only a queue-specific per-user native task and saves the matching engine check interval. It requires user filesystem/native-task permissions, never elevation or stored passwords.
3. Verify native status and logs. Real execution must separately be permitted in Settings.
4. Type ENABLE, then Enable OS scheduler. This is the explicit background opt-in. Any active foreground/execution lease blocks it. No job is submitted by configuration or installation.
5. Disable stops future submissions and disables the owned native trigger. An already-running Windows task is allowed to finish its current attempt; the execution lease blocks foreground handoff until it exits. macOS unload and Linux stop signal the owned active worker, preserving interruption/recovery behavior.
6. Type UNINSTALL to remove verified owned definitions. Queue data, worktrees, logs and settings remain. Modified definitions or a loaded task from another origin are refused; resolve them manually rather than overriding ownership.

The control record is fail-closed even when a native disable command fails. A failed disable does not complete a foreground switch. Another process still holding a lease causes a clear busy error. Never delete locks to force a handoff. External/legacy service instances are not adopted; stop them separately before migration.

## Platform adapters

- macOS: a uniquely named LaunchAgent under the user's LaunchAgents directory. StartInterval, Disabled=true at installation, RunAtLoad=false, KeepAlive=false. Enable/disable/bootstrap/bootout target the exact owned GUI-domain label. Loaded origin is checked before changing it. Login/session behavior and activation remain untested on this machine.
- Linux: per-user systemd oneshot service plus timer in XDG_CONFIG_HOME/systemd/user. Install reloads user definitions without enabling; Enable enables/starts only the owned timer. The service performs one check, allows an attempt to finish, then exits. No sudo, global service, crontab rewrite or silent cron fallback. A working user systemd session is required. Native Linux execution is unqualified.
- Windows: an XML Task Scheduler task registered disabled, using the current user's interactive token, least privilege, no password and IgnoreNew overlap policy. The definition and native action must match before mutation. It runs while that user is logged in; do not assume logged-out/server operation. Minimum interval 60 seconds. Native Windows registration and execution are unqualified.

macOS/Linux accept intervals of 10–3600 seconds, Windows 60–3600. These are eligibility check intervals, not guarantees of exact reset-time launch. Sleep, logout, offline state, locks, allowance and error backoff may delay jobs. Native status is queried on demand and displayed separately from selected mode/permission; unavailable queries are never presented as verified disabled/enabled.

## Shared engine and crash safety

Every managed native invocation uses `scheduled-check`, which validates the real-execution config gate, OS mode and enabled flag, acquires the same execution lease and calls Worker.once once. It exits when that check/current attempt ends. There is no second queue engine or prompt-resubmission loop. Manual jobs stay queued for explicit CLI run in foreground mode.

A kernel-backed execution.lock is held for the foreground lifetime or one OS check. mode.lock serializes handoffs. worker.lock and common-Git locks preserve existing attempt/repository isolation. Unix children inherit worker and mode leases, so a killed parent cannot release execution ownership while its child continues. Windows children use an owned Job Object. Stale durable mode selection never overrides a live lease. On recovery, uncertain attempts pause and require explicit review/retry rather than silent replay.

Scheduler logs live under the private state scheduler directory. Check outcomes and errors append to checks.jsonl; macOS also captures native stdout/stderr. Attempt logs are available in Execution History. Logs may contain private repository information; never attach them to a public issue without review.

## Verification limits

Automated tests use mocked native commands, temporary homes/state directories, synthetic conversations, mocked usage and harmless Python executions. They cover configuration without activation, ownership refusal, disable/switch ordering, leases, crash release and one-check delegation. No real launchd/systemd/Task Scheduler activation, live account listing, prompt execution, session continuation or remote CI was performed. Actual native behavior requires a separately authorized disposable-platform qualification.

Adapter references: [Apple launchd jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html), [systemd timers](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html), [Microsoft Task Scheduler commands](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create). Session navigation follows the documented [Codex App Server thread listing/read](https://learn.chatgpt.com/docs/app-server).
