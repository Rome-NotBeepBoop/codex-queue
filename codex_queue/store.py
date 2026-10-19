"""Durable jobs, explicit transitions, and immutable attempt history."""
from contextlib import contextmanager
import os
if os.name == "nt":
    import msvcrt
else:
    import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid


class QueueError(RuntimeError):
    pass


class WriterBusy(QueueError):
    pass


def git(repo, *args):
    env = dict(os.environ)
    if "GIT_CONFIG_GLOBAL" not in env:
        gitconfig = Path.home() / ".gitconfig"
        try:
            if not gitconfig.exists() or not os.access(gitconfig, os.R_OK):
                env["GIT_CONFIG_GLOBAL"] = os.devnull
        except OSError:
            env["GIT_CONFIG_GLOBAL"] = os.devnull
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if result.returncode:
        raise QueueError("Git operation failed: " + result.stderr.strip())
    return result.stdout.strip()


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+") as handle:
        os.chmod(path, 0o600)
        try:
            if os.name == "nt":
                handle.seek(0); handle.write("0"); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise WriterBusy("another writer holds the queue/repository lock") from error
        try:
            yield handle
        finally:
            if os.name == "nt":
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, state):
        self.state = Path(state).expanduser().resolve()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state, 0o700)
        self.db = sqlite3.connect(self.state / "queue.sqlite", timeout=10)
        os.chmod(self.state / "queue.sqlite", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, repo TEXT NOT NULL,
          cwd TEXT NOT NULL, base TEXT NOT NULL, prompt TEXT NOT NULL,
          mode TEXT NOT NULL, saved_reset INTEGER, status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, requested TEXT,
          reason TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
          job TEXT NOT NULL, number INTEGER NOT NULL, worktree TEXT, branch TEXT,
          status TEXT NOT NULL, started REAL NOT NULL, ended REAL, log TEXT,
          PRIMARY KEY(job, number));
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS backend_runs (
          job TEXT NOT NULL, number INTEGER NOT NULL, backend TEXT NOT NULL,
          request TEXT NOT NULL, token TEXT NOT NULL, tab TEXT, pane TEXT,
          PRIMARY KEY(job, number));
        ''')

        columns = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for name, definition in {"kind": "TEXT NOT NULL DEFAULT 'prompt'", "session_id": "TEXT", "session_cwd": "TEXT", "ownership_confirmed": "INTEGER NOT NULL DEFAULT 0", "backend": "TEXT NOT NULL DEFAULT 'default'"}.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        if 'session_id' not in {r[1] for r in self.db.execute("PRAGMA table_info(attempts)")}:
            self.db.execute("ALTER TABLE attempts ADD COLUMN session_id TEXT")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_session_job ON jobs(session_id) WHERE session_id IS NOT NULL AND status IN ('queued','running','paused')")
        self.db.commit()

    def add_session(self, session_id, repo, cwd=".", prompt="", mode="manual", reset=None, ownership=False):
        from .sessions import identifier
        session_id = identifier(session_id)
        if not ownership:
            raise QueueError("confirm the original session is stopped and the checkout is exclusively available")
        absolute = Path(repo).resolve() / cwd
        if self.db.execute("SELECT 1 FROM jobs WHERE session_id=? AND status IN ('queued','running','paused')", (session_id,)).fetchone():
            raise QueueError("this session already has an unfinished queue job")
        key = hashlib.sha256(json.dumps([session_id, str(absolute), prompt, mode, reset]).encode()).hexdigest()
        return self.add(prompt or "Continue the original unfinished task; preserve existing work.", repo, cwd, mode, reset, key,
                        {"id": session_id, "cwd": str(absolute.resolve())})

    def close(self):
        self.db.close()

    def rows(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM jobs ORDER BY created")]

    def get(self, job):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise QueueError("unknown job")
        return dict(row)

    def add(self, prompt, repo, cwd=".", mode="manual", reset=None, key=None, session=None):
        if mode not in ("manual", "immediate", "next-reset"):
            raise QueueError("unknown execution mode")
        if not prompt.strip():
            raise QueueError("empty prompt")
        if mode == "next-reset" and (type(reset) is not int or reset <= time.time()):
            raise QueueError("next-reset needs a fresh future reset timestamp")
        repo = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
        relative = Path(cwd)
        if relative.is_absolute() or ".." in relative.parts or not (repo / relative).resolve().is_relative_to(repo):
            raise QueueError("working directory must stay inside the repository")
        if not (repo / relative).is_dir():
            raise QueueError("working directory does not exist")
        base = git(repo, "rev-parse", "HEAD")
        payload = json.dumps([str(repo), str(relative), base, prompt, mode, reset])
        dedupe = key if key else hashlib.sha256(payload.encode()).hexdigest()
        job = uuid.uuid4().hex[:16]
        now = time.time()
        try:
            with self.db:
                self.db.execute("INSERT INTO jobs(id,dedupe,repo,cwd,base,prompt,mode,saved_reset,status,created,updated,kind,session_id,session_cwd,ownership_confirmed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (job, dedupe, str(repo), str(relative), base, prompt, mode, reset, "queued", now, now, "session" if session else "prompt", session["id"] if session else None, session["cwd"] if session else None, int(bool(session))))
        except sqlite3.IntegrityError:
            existing = self.db.execute("SELECT id FROM jobs WHERE dedupe=?", (dedupe,)).fetchone()
            if existing: return existing[0]
            raise QueueError("this session already has an unfinished queue job")
        return job

    def transition(self, job, action):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(job)
            if row["status"] == "running":
                if action not in ("pause", "cancel"):
                    raise QueueError("running job can only be paused or cancelled")
                self.db.execute("UPDATE jobs SET requested=?,updated=? WHERE id=?", (action, time.time(), job))
                return
            allowed = {"pause": ("queued",), "resume": ("paused",),
                       "cancel": ("queued", "paused", "failed"), "retry": ("failed", "paused", "cancelled")}
            if action not in allowed or row["status"] not in allowed[action]:
                raise QueueError("invalid state transition")
            if action == "resume" and row["reason"].startswith("interrupted"):
                raise QueueError("review interrupted worktree, then explicitly retry")
            status = {"pause": "paused", "resume": "queued", "cancel": "cancelled", "retry": "queued"}[action]
            self.db.execute("UPDATE jobs SET status=?,requested=NULL,reason='',updated=? WHERE id=?", (status, time.time(), job))

    def recover(self):
        # Caller MUST hold the worker lock. Uncertain attempts never rerun automatically.
        from .herdr import reconcile
        reconcile(self)
        with self.db:
            self.db.execute("UPDATE attempts SET status='interrupted',ended=? WHERE status='running' AND NOT EXISTS(SELECT 1 FROM backend_runs b WHERE b.job=attempts.job AND b.number=attempts.number)", (time.time(),))
            self.db.execute("UPDATE jobs SET status='paused',reason='interrupted: review attempt before retry',requested=NULL,updated=? WHERE status='running' AND NOT EXISTS(SELECT 1 FROM backend_runs b WHERE b.job=jobs.id AND b.number=jobs.attempts)", (time.time(),))

    def claim(self, job):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(job)
            if row["status"] != "queued":
                raise QueueError("job is not queued")
            if self.db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                raise QueueError("another job is running")
            number = row["attempts"] + 1
            self.db.execute("UPDATE jobs SET status='running',attempts=?,updated=? WHERE id=?", (number, time.time(), job))
            self.db.execute("INSERT INTO attempts(job,number,status,started) VALUES(?,?,'running',?)", (job, number, time.time()))
        return self.get(job)

    def finish(self, job, status, reason=""):
        with self.db:
            row = self.get(job)
            self.db.execute("UPDATE jobs SET status=?,reason=?,requested=NULL,updated=? WHERE id=?", (status, reason, time.time(), job))
            self.db.execute("UPDATE attempts SET status=?,ended=? WHERE job=? AND number=?", (status, time.time(), job, row["attempts"]))

    def event(self, event, **fields):
        path = self.state / "events.jsonl"
        with path.open("a") as output:
            os.chmod(path, 0o600)
            output.write(json.dumps({"time": time.time(), "event": event, **fields}) + "\n")

    def backoff(self, now, failed=False, reset=False, interval=60):
        with self.db:
            row = self.db.execute("SELECT value FROM metadata WHERE key='poll'").fetchone()
            value = json.loads(row[0]) if row else {"failures": 0, "next": 0}
            if reset:
                value = {"failures": 0, "next": 0}
            else:
                value["failures"] = min(6, value["failures"] + 1) if failed else 0
                delay = min(3600, interval * 2 ** value["failures"]) if failed else interval
                value["next"] = now + delay
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('poll',?)", (json.dumps(value),))
            return value

    def next_poll(self):
        row = self.db.execute("SELECT value FROM metadata WHERE key='poll'").fetchone()
        return json.loads(row[0])["next"] if row else 0
