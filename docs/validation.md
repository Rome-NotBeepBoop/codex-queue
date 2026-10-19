# Local verification evidence — 2026-10-10

Milestone A only. No SSH or live inference/account/quota requests were made by the queue. All App Server interactions in tests used fake executables; usage responses were simulated. `codex --help`, `codex exec --help`, `codex app-server --help` and `generate-json-schema` were read locally, without authenticated usage requests or model turns.

- Python standard-library unit suite: **55 tests passed** on macOS/Python 3.14.7.
- Coverage includes bucket/window selection, malformed/stale/reset/weekly data, original-reset preservation, actual reset boundary advancement, early credit-reset rejection, auth/timeout/protocol handling, durable SQLite persistence/backoff, duplicate enqueue and atomic claim, global/repository locks, worktree/base isolation, manual exclusion, CLI execution gate, fixture-to-real prohibition, failed launch and nonzero process, timeout pausing, interrupted/reboot recovery, state transitions, private permissions, and no-write dry run.
- Mock JSONL protocol tests only accept initialize/initialized/account/read/account/rateLimits/read; no thread or turn can be submitted.
- Mock Codex execution tests use harmless local Python executables, not the installed Codex binary. Simulation writes a single proof file only inside the attempt worktree.
- `python3 -m compileall -q codex_queue`: PASS.
- `sh -n deploy/install.sh deploy/uninstall.sh deploy/diagnose.sh`: PASS. Scripts were not executed.
- CLI end-to-end: PASS — queued immediate job, dry run with zero attempts, fixed harmless simulation completed in a new worktree, original checkout unchanged. Python wheel build/install in a disposable virtual environment and the installed codex-queue doctor entry point: PASS; execution_enabled=false.

**NOT RUN:** actual ChatGPT account bucket/reset responses, real Codex inference from the queue, systemd installation/activation/reboot, actual Linux server, Docker Linux container (daemon unavailable), and remote GitHub Actions. No server integration is claimed. All work remains local and unpushed.

## Architecture follow-up — 2026-10-10

59 unittest cases PASS locally, including scheduling two unrelated Git repositories, rejecting worktree placement within an unrelated checkout, and environment configuration. Shell syntax and Python compilation PASS. The primary CLI is codex-queue. Actual Linux deployment and real inference remain NOT RUN. GitHub metadata access returned 404 for the repository through the connected account, so current remote privacy could not be independently certified; its configured remote URL was preserved.

## Interactive queue update — 2026-10-11

See [local review readiness](local-review.md) for the current 84-test run, installation, packaging, and remaining platform/publication limits. Earlier counts below describe earlier work.

## Projects and execution modes — 2026-10-11

The current [local review](local-review.md) supersedes earlier counts: 115 local tests, mocked native scheduling/session discovery, no real execution or service activation. Native platform and publication blockers remain explicit.
