# Architecture

Target repositories own their source/tooling; codex-queue owns the scheduler. Arbitrary clean, committed Git repositories are supported.

```text
Markdown prompt + repository + relative working directory
    → SQLite job (pinned commit, idempotency key, observed reset)
    → foreground worker / optional systemd service
    → readonly Codex App Server account + usage snapshot
    → global worker lock + repository common-Git lock
    → new codex/queue-JOB-ATTEMPT branch and isolated worktree
    → second usage check → sandboxed codex exec
    → private logs, uncommitted changes, explicit human review
```

Python 3.11+ core uses the standard library. The optional interactive application uses Textual and portable platform directories. SQLite uses WAL, FULL synchronous durability, transactional claims, immutable attempt rows, and explicit transitions. One worker per state database and one writer per common Git directory are enforced by advisory file locks. A child inherits the repository lock so a surviving child cannot be overlapped by a replacement worker. Cross-host/shared-network-filesystem locking is not supported: keep state/repositories on one local disk/server. External tools and humans must coordinate their own writes.

`add` never executes. `manual` runs only with an explicit `run JOB`. `immediate` becomes eligible on the next foreground worker cycle. `next-reset` records the fresh 300-minute reset observed when added. Both wall time passing that saved timestamp and a later fresh server reset timestamp are required. Changing quota percentages alone, or an early earned reset, does not release that job. Retry keeps the original prompt, commit, and reset; add a new job with a new key to choose a new base/reset.

The parser prefers `rateLimitsByLimitId`; it will not fall back from an existing map with a missing selected bucket to legacy data. Legacy `rateLimits` must identify the selected bucket. It locates windows by 300 and 10080 minutes, irrespective of primary/secondary order, rejects malformed/missing/inconsistent timestamps and percentages, and requires both quotas below 100. Observations expire after 120 seconds; a past reset waits for a fresh advanced observation. Explicit reached-limit flags block submission. Reset credits are ignored. No purchase, redemption, undocumented ChatGPT endpoint, credential scraping, thread, or turn is involved in the watcher.

The App Server adapter starts `codex app-server --listen stdio://`, performs initialize/initialized, checks account/read for ChatGPT authentication, and calls account/rateLimits/read. RPC timeouts, authentication/token-refresh requests, server errors, and missing bucket/window information block jobs. Raw account responses and tokens are not logged. The separate execution child uses `codex --ask-for-approval never exec --ignore-user-config --sandbox workspace-write`, disables shell network access, and accepts the prompt on stdin. It does not load arbitrary user MCP/hooks configuration. Codex version compatibility and Linux sandbox operation must still be checked on the actual server.

Healthy waiting polls at most once per minute. Failures exponentially back off from 120 seconds to one hour; deadlines persist in SQLite across restarts. The foreground watcher refreshes usage even with an empty queue; CLI one-shot checks can exit idle without a quota query. The second usage check intentionally occurs immediately before every submission. Quota is shared with other devices, so a race remains possible after that check; a rejected/failed inference becomes a failed attempt and requires an explicit retry, rather than being resubmitted automatically.

Execution requires BOTH `--execute` and external `execution_enabled: true`. The checked-in template is false. Simulation requires fixture usage and runs only a fixed harmless Python writer; fixture usage can never authorize real Codex. Dry runs do not claim jobs, create worktrees, or invoke inference (startup can conservatively recover an already interrupted job to paused).

Running jobs can receive pause/cancel requests through SQLite. The worker stops the process group, waits, and kills it if needed. SIGTERM/SIGINT and timeout preserve partial work and pause for review. A lost Direct Codex worker/reboot turns uncertain attempts into paused/interrupted. Herdr dispatches remain running/unknown until a matching relay receipt proves the outcome; they never resume or resubmit automatically. Explicit retry creates a new branch/worktree and attempt, leaving old work available. Exactly-once external effects cannot be promised after a crash; avoiding automatic retry of uncertain attempts is deliberate. Finished attempts/worktrees are retained; no destructive automatic pruning is included.

Dirty source repositories pause for review while other repositories can proceed; a busy repository lock leaves the job queued with backoff. Launch/nonzero failures preserve the attempt and do not automatically retry.

The manager never commits, merges, pushes, force-pushes, publishes, or deploys. Prompt policy repeats these restrictions. This is a development scheduler, not a security boundary for hostile prompts or untrusted repositories. Use a dedicated user, reviewed prompts, Codex's working sandbox, read-only Git credentials, and no production/cloud credentials. Do not rely on prompt text alone to protect production. The proposed server restricts writable paths to its state, cloned repos, worktrees, and CLI auth directory. A platform sandbox failure must block deployment rather than be worked around with danger-full-access.

Manager events are timestamped JSONL; runner output and final responses are private attempt files. SQLite includes prompt bodies. State is 0700, database/logs are 0600, and the CLI sets umask 0077. Logs may contain source information; share only after redaction. Failure information is available through `list`, `show`, events.jsonl, and systemd journal. No email/Slack notifications or telemetry are configured.

Official protocol reference: https://learn.chatgpt.com/docs/app-server

## Repository independence

`codex-queue` is a standalone Git repository. Every job names its own target repository; no default target is required. The primary command and environment variables are `codex-queue`, `CODEX_QUEUE_STATE`, and `CODEX_QUEUE_CONFIG`; legacy environment aliases remain compatible. Queue state and generated worktrees must reside outside every Git checkout. The worker rejects nested checkout placement before claiming an attempt or creating a branch. Independent temporary repositories cover multi-project scheduling.

## Optional execution adapters

Worker owns eligibility, repository/session validation, isolation, and attempts. `backends.resolve` chooses Direct Codex or the optional Herdr adapter using an integration default or persisted per-job override. Scheduling mode is independent: foreground and native one-shot checks use the same Worker. The extension registry and lazy management screens do not introduce another scheduling engine.

Herdr uses structured CLI responses researched against installed 0.9.3/protocol 22. Creation is no-focus; explicit adoption is required for existing same-name workspaces. A random display-only ownership token prevents recycled workspace IDs from being trusted. Only newly created job tabs/panes receive commands. Exact cwd/topology and shell availability are checked before submission.

The adapter commits `backend_runs` before sending a safely quoted relay command to `pane run`. Private payloads carry prompts on disk, not command arguments. The pane relay acquires worker/common-Git locks after the dispatch process releases them, rechecks quota/session availability, then starts the same sandboxed Codex command and tees output to its Herdr terminal and private log. Its atomic `submitted` marker prohibits replay. An owned POSIX child inherits locks if the relay crashes.

Stopping the foreground watcher leaves Herdr runs intact. A separate confirmed interruption writes a private matching token request. Only matching receipts with actual exit codes reconcile completion. Unknown external outcomes retain the running reservation; no timeout makes them eligible again. Missing-receipt recovery is intentionally manual. Herdr native semantic states/hooks are not overridden. See [Herdr lifecycle and limitations](integrations/herdr.md).
