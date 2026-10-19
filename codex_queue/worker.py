"""Single writer; isolated attempts, conservative recovery, bounded processes."""
import os
from pathlib import Path
import signal
import subprocess
import time
from .store import QueueError, WriterBusy, git, lock
from .usage import UsageError
from .processes import terminate, WindowsJob
from . import sessions
from .backends import BackendUnavailable
import json
import sys

POLICY = '''Work only in this isolated worktree. Do not merge, push, publish, deploy,
modify other checkouts, or enable services. Do not access credentials. Preserve
existing assets. Make code changes and run appropriate local validation; leave
changes uncommitted for human review. Report limitations honestly.\n\n'''


class Worker:
    def __init__(self, store, provider, codex="codex", simulation=False, timeout=3600, observer=None, interval=60, config=None):
        self.store, self.provider, self.codex = store, provider, codex
        self.simulation, self.timeout, self.stopping = simulation, timeout, False
        self.observer, self.interval = observer, interval
        self.last_usage = None
        self.extra_fds = ()
        self.monitor_idle = False
        self.config = config or {}

    def report(self, **fields):
        if self.observer:
            self.observer(fields)

    def stop(self, *_):
        self.stopping = True

    def once(self, job_id=None, dry_run=False):
        if self.stopping:
            return {"state": "stopped", "reason": "monitoring stopped; no further jobs launched"}
        with lock(self.store.state / "worker.lock") as worker_lock:
            self.worker_lock = worker_lock
            self.store.recover()
            if any(j['status'] == 'running' for j in self.store.rows()):
                return {'state': 'running', 'reason': 'Managed job still running or outcome unknown; no duplicate dispatch.'}
            jobs = [self.store.get(job_id)] if job_id else self.store.rows()
            candidates = [j for j in jobs if j["status"] == "queued" and (job_id or j["mode"] != "manual")]
            if not candidates and not self.monitor_idle:
                return {"state": "idle"}
            now = time.time()
            if self.store.next_poll() > now:
                return {"state": "waiting", "reason": "poll backoff", "next": self.store.next_poll()}
            try:
                usage = self.provider.read()
                self.last_usage = usage
                self.report(state="usage", usage=usage)
                if not candidates:
                    self.store.backoff(time.time(), interval=self.interval)
                    return {"state":"idle", "next":self.store.next_poll()}
                for job in candidates:
                    reset = job["saved_reset"] if job["mode"] == "next-reset" else None
                    eligible, reason = usage.eligible(time.time(), reset)
                    if eligible:
                        if dry_run:
                            return {"state": "dry-run", "job": job["id"], "base": job["base"], "would_execute": True}
                        if not self.simulation and not getattr(self.provider, "live", False):
                            raise QueueError("fixture usage cannot authorize a real Codex run")
                        try:
                            result = self.execute(job)
                        except BackendUnavailable as error:
                            reason = str(error)
                            self.store.event('backend_unavailable', job=job['id'], reason=reason)
                            continue
                        except WriterBusy:
                            reason = "another writer holds a repository lock"
                            continue
                        except QueueError as error:
                            if self.store.get(job["id"])["status"] == "queued":
                                self.store.transition(job["id"], "pause")
                                with self.store.db:
                                    self.store.db.execute("UPDATE jobs SET reason=? WHERE id=?", (str(error), job["id"]))
                            self.store.event("job_blocked", job=job["id"], reason=str(error))
                            reason = str(error)
                            continue
                        self.store.backoff(time.time(), failed=result["state"] == "failed", interval=self.interval)
                        return result
                self.store.backoff(time.time(), interval=self.interval)
                return {"state": "waiting", "reason": reason, "next": self.store.next_poll()}
            except UsageError as error:
                self.store.event("usage_blocked", reason=str(error))
                self.store.backoff(time.time(), failed=True, interval=self.interval)
                return {"state": "blocked", "reason": str(error)}

    def execute(self, job):
        from .backends import resolve
        backend = resolve(self.store, self.config, job)
        if backend.persistent:
            backend.preflight()
        try:
            git(self.store.state, "rev-parse", "--show-toplevel")
            raise QueueError("queue state/worktrees must be outside every Git checkout")
        except QueueError as e:
            if "queue state/worktrees must be outside" in str(e):
                raise
        common = Path(git(job["repo"], "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = Path(job["repo"]) / common
        # Lock in common Git metadata also serializes workers using other state DBs.
        with lock(common.resolve() / "codex-queue.lock") as repo_lock:
            resuming = job.get("kind") == "session"
            if resuming and not self.simulation:
                sessions.check_compatibility(self.codex)
                sessions.validate_metadata(self.provider.read_thread(job['session_id']), job)
            if not resuming and git(job["repo"], "status", "--porcelain"):
                raise QueueError("source repository has uncommitted/untracked changes; review and commit first")
            row = self.store.claim(job["id"])
            number = row["attempts"]
            branch = git(row["repo"], "branch", "--show-current") if resuming else f"codex/queue-{row['id']}-{number}"
            tree = Path(row["session_cwd"]) if resuming else self.store.state / "worktrees" / f"{row['id']}-{number}"
            if not resuming and tree.is_relative_to(Path(row["repo"])):
                self.store.finish(row["id"], "failed", "state directory cannot be inside target repository")
                raise QueueError("worktree state must be outside the target repository")
            logs = self.store.state / "logs"
            logs.mkdir(mode=0o700, exist_ok=True)
            log = logs / f"{row['id']}-{number}-{int(time.time())}.jsonl"
            with self.store.db:
                self.store.db.execute("UPDATE attempts SET worktree=?,branch=?,log=? WHERE job=? AND number=?",
                                      (str(tree), branch, str(log), row["id"], number))
            process = None
            ownership = None
            try:
                if not resuming:
                    git(row["repo"], "worktree", "add", "-b", branch, str(tree), row["base"])
                cwd = tree.resolve() if resuming else (tree / row["cwd"]).resolve()
                if not cwd.is_relative_to(tree) or not cwd.is_dir():
                    raise QueueError("worktree working directory missing or escapes checkout")
                # Recheck immediately before submitting, after Git setup.
                current = self.provider.read()
                self.last_usage = current
                self.report(state="usage", usage=current)
                ok, reason = current.eligible(time.time(), row["saved_reset"] if row["mode"] == "next-reset" else None)
                if not ok:
                    self.store.finish(row["id"], "paused", reason)
                    return {"state": "paused", "reason": reason}
                if resuming and not self.simulation:
                    sessions.validate_metadata(self.provider.read_thread(row["session_id"]), row)
                request = self.store.get(row["id"])["requested"]
                if self.stopping or request:
                    status = "cancelled" if request == "cancel" else "paused"
                    self.store.finish(row["id"], status, "interrupted before submission")
                    return {"state": status}
                self.store.event("attempt_started", job=row["id"], attempt=number, worktree=str(tree), simulation=self.simulation)
                self.report(state="running", job=row["id"], project=Path(row["repo"]).name, session_id=row.get("session_id"), log=str(log), started=time.time())
                if self.simulation:
                    # Fixed harmless command: cannot be configured into an arbitrary shell command.
                    command = [sys.executable if os.name == "nt" else "python3", "-c", "from pathlib import Path; Path('queue-simulation.txt').write_text('harmless queue validation\\n'); print('simulation completed')"]
                elif resuming:
                    command = sessions.command(self.codex, row["session_id"], log.with_suffix(".final.txt"))
                else:
                    command = [self.codex, "--ask-for-approval", "never", "exec", "--ignore-user-config",
                               "--sandbox", "workspace-write", "-c", "sandbox_workspace_write.network_access=false",
                               "--json", "--color", "never", "-o", str(log.with_suffix(".final.txt")), "-"]
                env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "SYSTEMROOT", "SystemRoot", "COMSPEC", "USERPROFILE", "TEMP", "TMP")}
                env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
                policy = POLICY.replace("Work only in this isolated worktree.", "Continue only the specified session in its original working directory; preserve existing uncommitted work.") if resuming else POLICY
                if backend.persistent:
                    result = backend.start(row, cwd, command, policy + row['prompt'], log, self.timeout, self.simulation)
                    self.report(**result)
                    return result
                with log.open("w") as output:
                    os.chmod(log, 0o600)
                    process = backend.start(command, cwd=cwd, stdin=subprocess.PIPE, stdout=output,
                                               stderr=subprocess.STDOUT, text=True, env=env,
                                               **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True, "pass_fds": (repo_lock.fileno(), self.worker_lock.fileno(), *self.extra_fds)}))
                    ownership = WindowsJob(process)
                    policy = POLICY.replace("Work only in this isolated worktree.", "Continue only the specified session in its original working directory; preserve existing uncommitted work.") if resuming else POLICY
                    process.stdin.write(policy + row["prompt"])
                    process.stdin.close()
                    deadline = time.monotonic() + self.timeout
                    status, reason = "completed", ""
                    offset = 0
                    def stream():
                        nonlocal offset
                        if log.exists():
                            with log.open(errors="replace") as source:
                                source.seek(offset); chunk = source.read(65536); offset = source.tell()
                            if chunk:
                                self.report(state="output", job=row["id"], text=chunk)
                    while process.poll() is None:
                        stream()
                        request = self.store.get(row["id"])["requested"]
                        if self.stopping or request or time.monotonic() >= deadline:
                            status = "cancelled" if request == "cancel" else "paused"
                            reason = "interrupted: partial work preserved; review before retry"
                            terminate(process)
                            break
                        time.sleep(0.1)
                    stream()
                    if status == "completed" and process.returncode != 0:
                        status, reason = "failed", f"runner exited {process.returncode}; review private attempt log"
                captured = row.get('session_id')
                for line in log.read_text(errors='replace').splitlines():
                    try:
                        event = json.loads(line)
                        if event.get('type') == 'thread.started':
                            reported = sessions.identifier(event.get('thread_id'))
                            if resuming and reported != row['session_id']:
                                status, reason = 'failed', 'runner reported a different session; review log before retry'
                            else:
                                captured = reported
                    except (ValueError, QueueError):
                        continue
                with self.store.db:
                    self.store.db.execute("UPDATE attempts SET session_id=? WHERE job=? AND number=?", (captured, row['id'], number))
                if status == 'failed':
                    diagnostic = log.read_text(errors='replace').lower()
                    if 'rate limit' in diagnostic or 'usage limit' in diagnostic:
                        reason = 'rate limited: review and retry explicitly after allowance resets'
                    elif 'authentication' in diagnostic or 'unauthorized' in diagnostic:
                        reason = 'authentication required: sign in with Codex separately, then retry'
                final = log.with_suffix(".final.txt")
                if final.exists():
                    os.chmod(final, 0o600)
                self.store.finish(row["id"], status, reason)
                self.store.event("attempt_finished", job=row["id"], attempt=number, status=status)
                return {"state": status, "job": row["id"], "worktree": str(tree), "log": str(log), "reason": reason}
            except Exception as error:
                if self.store.db.execute('SELECT 1 FROM backend_runs WHERE job=? AND number=?', (row['id'], number)).fetchone():
                    return {'state': 'running', 'job': row['id'], 'reason': 'Herdr outcome unknown; no automatic resubmission.'}
                if isinstance(error, BackendUnavailable):
                    self.store.finish(row['id'], 'queued', str(error))
                    return {'state': 'waiting', 'job': row['id'], 'reason': str(error)}
                self.store.finish(row["id"], "failed", type(error).__name__ + ": review logs/worktree")
                self.store.event("attempt_failed", job=row["id"], error=type(error).__name__)
                return {"state": "failed", "job": row["id"], "worktree": str(tree), "log": str(log), "reason": type(error).__name__}
            finally:
                if process is not None:
                    terminate(process)
                if ownership:
                    ownership.close()
