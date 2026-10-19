# Installation, local verification, and deferred server deployment

## Interactive installation and native scheduling

Install the reviewed wheel using pipx, uv tool, or a Python virtual environment as described in [README](../README.md). Run `codex-queue`; follow the project → conversation → prompt → schedule → review workflow in [QUICKSTART](../QUICKSTART.md). Foreground mode is the default. No account query or worker starts on launch.

For optional per-user native scheduling, see [Scheduler Management](schedulers.md). Configure and Install disabled are separate from Enable; mode switches disable/stop the other managed runner first. The adapters are mock-tested only, with no native activation during development. Linux uses a systemd user timer (no automatic cron fallback); Windows uses an interactive-token Task Scheduler task. Do not activate jobs before real-execution authorization and disposable-platform qualification.

The older dedicated-server scripts below are retained for reviewed legacy deployment; they are not adopted by the TUI. Do not run them alongside a managed timer/watcher.

## Milestone A: local Mac

No server connection, inference request, or API credentials are required. From the checkout:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q codex_queue
sh -n deploy/install.sh deploy/uninstall.sh deploy/diagnose.sh
python3 -m codex_queue.cli --state /tmp/queue-doctor doctor
```

For an end-to-end disposable CLI simulation, create a temporary committed Git repository and Markdown prompt. Generate a fresh fixture (expires after 120 seconds):

```sh
python3 - <<'PY'
import json, time
now = time.time()
window = lambda minutes: {'usedPercent': 0, 'windowDurationMins': minutes, 'resetsAt': int(now)+minutes*60}
json.dump({'observed_at': now, 'response': {'rateLimits': {'limitId': 'codex', 'primary': window(300), 'secondary': window(10080)}}}, open('/tmp/queue-usage.json', 'w'))
PY
python3 -m codex_queue.cli --state /tmp/queue-demo --fixture /tmp/queue-usage.json add /tmp/prompt.md --repo /tmp/demo-repo --mode immediate
python3 -m codex_queue.cli --state /tmp/queue-demo --fixture /tmp/queue-usage.json worker --once --dry-run
python3 -m codex_queue.cli --state /tmp/queue-demo --fixture /tmp/queue-usage.json worker --once --simulate
python3 -m codex_queue.cli --state /tmp/queue-demo list
```

The simulation writes only `queue-simulation.txt` in a new isolated worktree. It cannot run arbitrary commands or Codex. Tests create all Git fixtures themselves and cover the reset boundary, authentication errors, worktree isolation, and recovery. Completed worktrees remain for review. To inspect, use `show JOB` for the path, then `git -C WORKTREE diff` and `git -C WORKTREE status`.

Optional disposable Linux verification, when an existing Docker daemon is available:

```sh
docker build -f deploy/Dockerfile.test -t codex-queue-test .
docker run --rm codex-queue-test
```

Docker was unavailable on the development Mac; this was NOT RUN. GitHub Actions is prepared for macOS/Linux/Windows and Python 3.11/3.13/3.14, but has not run remotely because no push was performed.

## Milestone B: home server (deferred; do not run now)

1. Restore the server. Install Python 3.11+ with venv, Git, and the official Codex CLI for its architecture. Check `codex --version`, `codex app-server --help`, and `codex exec --help`. Never use an API key as a workaround for unavailable ChatGPT allowance.
2. Transfer/review this repository independently. Run the unit tests on the actual server. Review `deploy/install.sh`, then run `sudo sh deploy/install.sh`. It creates the dedicated user and installs files without enabling, starting, or daemon-reloading a service. It preserves existing config during upgrades; check its gate remains false.
3. Authenticate interactively as the service user with its own protected home: `sudo -u codex-queue -H env CODEX_HOME=/var/lib/codex-queue/.codex codex login`. Use Codex's supported login flow shown by its CLI; never copy browser cookies/tokens into Git or logs. Authentication may require a browser/device flow and account eligibility. Do not read or share auth.json. Directory mode 0700; use umask 077.
4. Put repository clones under `/var/lib/codex-queue/repos/`. Use a Git credential with **read-only** access for cloning private repositories; remove ambient write/deployment credentials. All source needed by a job must be committed before queuing. Do not point the worker at a dirty interactive development checkout. Queue state belongs in `/var/lib/codex-queue/state`, outside all target repos.
5. Set the actual Codex executable in `/etc/codex-queue/config.json`; leave `execution_enabled` false. The environment template contains paths only. Run doctor as the dedicated user with the same HOME/CODEX_HOME as the service. Check the unit's writable paths match repository paths, local disk locking, and Codex's Linux sandbox requirements.
6. Read usage only: `sudo -u codex-queue -H env CODEX_HOME=/var/lib/codex-queue/.codex /opt/codex-queue/venv/bin/codex-queue --config /etc/codex-queue/config.json usage`. This must return the intended bucket plus 300- and 10080-minute windows. If the account reports another bucket/duration, update the reviewed adapter/config or keep blocked; do not guess the reset. No inference is submitted by this command. Confirm auth/no-quota errors fail closed.
7. Validate `sudo systemd-analyze verify /etc/systemd/system/codex-queue.service` after installation. Test sandboxed manual operation on a disposable repository, including stopping a running job and preserving its worktree, **only after explicit approval for real inference**. Validate a reboot stops descendants and pauses interrupted jobs. Never disable the sandbox to make it run.
8. **Approval gate:** only after the user explicitly approves unattended operation, change external `execution_enabled` to true and run `sudo systemctl daemon-reload` followed by `sudo systemctl enable --now codex-queue.service`. Neither this implementation nor the install script runs these commands. Start a foreground/manual check first. Record real server evidence before marking Milestone B done.

Jobs may be added as the same dedicated user. Manual jobs remain excluded from automatic worker pickup. `run JOB --execute` also needs the external execution gate. A failed run is not retried automatically. Review private logs and preserved worktrees, fix the cause, then explicitly `retry JOB`. An interrupted job requires retry rather than resume. Retry starts fresh from the originally pinned commit; it does not carry partial edits into the next attempt.

Diagnostic command: `sudo -u codex-queue -H sh /opt/codex-queue/source/deploy/diagnose.sh` if deployment artifacts were copied there, or run the reviewed script from the repository. Installer copies runtime source only; keep the deployment checkout for diagnostics/uninstallation. Use `journalctl -u codex-queue.service` privately. For failures, inspect `list`, `show JOB`, and state/events.jsonl. No external notification service is included.

Pause unattended operation with `sudo systemctl stop codex-queue.service` and restore the config gate to false. Manual uninstallation: `sudo sh deploy/uninstall.sh`; it disables/stops/removes only the unit and preserves data, auth, repositories, worktrees, and user. Remove retained directories/user only after manual review and backups.

Prerequisites requiring manual action: server restoration; CLI install and ChatGPT sign-in under the service user; private Git read access; reviewed source commits; actual-server mock tests/unit verification/sandbox and reboot tests; explicit activation approval. No production credentials are requested.

Use `codex-queue` and `CODEX_QUEUE_STATE` / `CODEX_QUEUE_CONFIG`; the installer preserves existing external configuration and environment files. Place state outside every Git checkout to avoid nested worktrees.

## Mac installation

See [Mac installation and disabled reset scheduling](macos.md). Preparation does not invoke Codex or load a launchd agent.
