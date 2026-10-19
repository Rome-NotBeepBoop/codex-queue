"""CLI, explicit execution gate, and a foreground worker suitable for systemd."""
import argparse
from contextlib import ExitStack
from .execution import runner_lease
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
from .app_server import AppServer
from .store import Store, QueueError
from .usage import UsageError, parse
from .worker import Worker
from .paths import state_path, config_path


class Fixture:
    live = False

    def __init__(self, path, bucket):
        self.path, self.bucket = path, bucket

    def read(self):
        data = json.loads(Path(self.path).read_text())
        return parse(data["response"], data["observed_at"], time.time(), self.bucket)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="codex-queue")
    parser.add_argument("--state", default=os.environ.get("CODEX_QUEUE_STATE", str(state_path())))
    parser.add_argument("--config", default=os.environ.get("CODEX_QUEUE_CONFIG", str(config_path())))
    parser.add_argument("--fixture", help="mock usage JSON; never authorizes real Codex execution")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--cli", action="store_true", help="run minimal CLI mode instead of TUI")
    mode_group.add_argument("--tui", action="store_true", help="force TUI mode even if CLI mode is configured")
    color_group = parser.add_mutually_exclusive_group()
    color_group.add_argument("--color", action="store_true", help="force colored output in CLI mode")
    color_group.add_argument("--no-color", action="store_true", help="disable colored output in CLI mode")
    parser.add_argument("--non-interactive", action="store_true", help="run minimal CLI mode non-interactively (print overview and exit)")
    parser.add_argument("--toggle-cli", action="store_true", help="toggle CLI mode in config and exit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("cli", help="display core features in minimal CLI mode")
    settings_cmd = sub.add_parser("settings", help="view or update settings")
    settings_cmd.add_argument("--toggle-cli", action="store_true", help="toggle CLI mode")
    settings_cmd.add_argument("--interface-mode", choices=["tui", "cli"], help="set interface mode")
    settings_cmd.add_argument("--cli-mode", choices=["true", "false", "on", "off", "tui", "cli"], help="set CLI mode")
    add = sub.add_parser("add")
    add.add_argument("prompt")
    add.add_argument("--repo", required=True)
    add.add_argument("--cwd", default=".")
    add.add_argument("--mode", choices=["manual", "immediate", "next-reset"], default="manual")
    add.add_argument("--key")
    continuation = sub.add_parser("continue-session", help="queue an existing Codex conversation; resume still unpauses jobs")
    continuation.add_argument("session_id")
    continuation.add_argument("--repo", required=True)
    continuation.add_argument("--cwd", default=".")
    continuation.add_argument("--prompt", default="")
    continuation.add_argument("--mode", choices=["manual", "immediate", "next-reset"], default="manual")
    continuation.add_argument("--confirm-ownership", action="store_true", help="original session is stopped; you exclusively own this checkout")
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("job")
    for name in ("pause", "resume", "cancel", "retry"):
        action = sub.add_parser(name)
        action.add_argument("job")
    sub.add_parser("usage")
    sub.add_parser("doctor")
    sub.add_parser("scheduled-check", help="managed OS scheduler: one gated check, then exit")
    for name in ("run", "worker"):
        run = sub.add_parser(name)
        if name == "run":
            run.add_argument("job")
        else:
            run.add_argument("--once", action="store_true")
        run.add_argument("--dry-run", action="store_true")
        run.add_argument("--simulate", action="store_true", help="fixed harmless writer, never invokes Codex")
        run.add_argument("--execute", action="store_true", help="explicit real execution, also requires config gate")
    args = parser.parse_args(argv)
    store = None
    leases = ExitStack()
    try:
        if getattr(args, "toggle_cli", False):
            from .settings import toggle_cli_mode
            cfg = toggle_cli_mode(args.config)
            print(f"CLI mode toggled: interface_mode={cfg.get('interface_mode')}, cli_mode={cfg.get('cli_mode')}")
            return 0
        if args.command == "settings":
            from .settings import load as load_settings, save as save_settings, toggle_cli_mode
            if getattr(args, "toggle_cli", False):
                cfg = toggle_cli_mode(args.config)
                print(json.dumps(cfg, indent=2))
                return 0
            cfg = load_settings(args.config)
            modified = False
            if getattr(args, "interface_mode", None):
                cfg["interface_mode"] = args.interface_mode
                cfg["cli_mode"] = (args.interface_mode == "cli")
                modified = True
            if getattr(args, "cli_mode", None):
                val = args.cli_mode.lower() in ("true", "on", "1", "cli")
                cfg["cli_mode"] = val
                cfg["interface_mode"] = "cli" if val else "tui"
                modified = True
            if modified:
                save_settings(args.config, cfg)
            print(json.dumps(cfg, indent=2))
            return 0
        color_opt = None
        if getattr(args, "no_color", False):
            color_opt = False
        elif getattr(args, "color", False):
            color_opt = True

        if args.command is None:
            from .settings import load as load_settings, is_cli_mode
            config = load_settings(args.config)
            use_cli = getattr(args, "cli", False) or (not getattr(args, "tui", False) and is_cli_mode(config))
            if use_cli:
                from .cli_mode import run_cli_mode
                return run_cli_mode(args.state, args.config, args.fixture, interactive=False if getattr(args, "non_interactive", False) else None, color=color_opt)
            from .tui import QueueApp
            QueueApp(args.state, args.config, args.fixture).run()
            return 0
        config = {}
        if args.config:
            try:
                cp = Path(args.config)
                if cp.is_file():
                    config = json.loads(cp.read_text())
            except OSError:
                config = {}
        codex, bucket = config.get("codex", "codex"), config.get("bucket", "codex")
        interval = config.get("poll_interval_seconds", 60)
        if type(interval) is not int or not 10 <= interval <= 3600:
            raise QueueError("poll_interval_seconds must be between 10 and 3600")
        timeout = config.get("job_timeout_seconds", 3600)
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            raise QueueError("job_timeout_seconds must be an integer between 1 and 86400")
        provider = Fixture(args.fixture, bucket) if args.fixture else AppServer(codex, bucket)
        store = Store(args.state)
        if args.command == "add":
            reset = None
            if args.mode == "next-reset":
                reset = provider.read().five.reset
            result = {"job": store.add(Path(args.prompt).read_text(), args.repo, args.cwd, args.mode, reset, args.key)}
        elif args.command == "continue-session":
            reset = provider.read().five.reset if args.mode == "next-reset" else None
            result = {"job": store.add_session(args.session_id, args.repo, args.cwd, args.prompt, args.mode, reset, args.confirm_ownership)}
        elif args.command == "list":
            from .projects import resolve_project_name, resolve_chat_title, sanitize_chat_title
            result = []
            for row in store.rows():
                d = {k: row[k] for k in ("id", "repo", "kind", "session_id", "mode", "status", "attempts", "saved_reset", "reason")}
                d["project_name"] = resolve_project_name(row["repo"])
                d["chat_title"] = resolve_chat_title(row.get("session_id")) if row.get("session_id") else (sanitize_chat_title(row.get("prompt"), 35) if row.get("prompt") else "New Prompt")
                result.append(d)
        elif args.command == "show":
            from .projects import resolve_project_name, resolve_chat_title, sanitize_chat_title
            result = store.get(args.job)
            result["project_name"] = resolve_project_name(result["repo"])
            result["chat_title"] = resolve_chat_title(result.get("session_id")) if result.get("session_id") else (sanitize_chat_title(result.get("prompt"), 35) if result.get("prompt") else "New Prompt")
            result["attempt_history"] = [dict(r) for r in store.db.execute("SELECT * FROM attempts WHERE job=? ORDER BY number", (args.job,))]
        elif args.command in ("pause", "resume", "cancel", "retry"):
            store.transition(args.job, args.command)
            result = {"job": args.job, "status": store.get(args.job)["status"]}
        elif args.command == "usage":
            usage = provider.read()
            result = {"bucket": bucket, "observed_at": usage.observed,
                      "five_hour": vars(usage.five), "weekly": vars(usage.weekly),
                      "availability": usage.eligible(time.time())}
        elif args.command == "cli":
            from .cli_mode import run_cli_mode
            return run_cli_mode(args.state, args.config, args.fixture, provider=provider, store=store, interactive=False if getattr(args, "non_interactive", False) else None, color=color_opt)
        elif args.command == "doctor":
            result = {"python": sys.version.split()[0], "git": shutil.which("git"), "codex": shutil.which(codex),
                      "state": str(store.state), "execution_enabled": config.get("execution_enabled") is True,
                      "note": "No credentials read; no inference, deployment, or service changes performed."}
        else:
            scheduled = args.command == 'scheduled-check'
            if scheduled:
                args.simulate, args.execute, args.dry_run, args.once = False, True, False, True
                if args.fixture: raise QueueError('managed scheduled execution cannot use fixtures')
            if args.simulate and not args.fixture:
                raise QueueError("simulation requires fixture usage")
            if args.execute and (args.fixture or args.simulate):
                raise QueueError("real execution cannot use fixtures or simulation")
            if not args.dry_run and not args.simulate and not (args.execute and config.get("execution_enabled") is True):
                raise QueueError("execution disabled: needs --execute AND execution_enabled=true in external config")
            if args.command == "worker" and args.dry_run and not args.once:
                raise QueueError("dry-run worker requires --once")
            lease = leases.enter_context(runner_lease(store.state, "os" if scheduled else "foreground"))
            worker = Worker(store, provider, codex, args.simulate, timeout, interval=config.get("poll_interval_seconds", 60), config=config)
            worker.extra_fds = () if os.name == "nt" else (lease.fileno(),)
            signal.signal(signal.SIGTERM, worker.stop)
            signal.signal(signal.SIGINT, worker.stop)
            while True:
                result = worker.once(getattr(args, "job", None), args.dry_run)
                if scheduled:
                    log = store.state / "scheduler" / "checks.jsonl"
                    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with log.open("a") as output:
                        os.chmod(log, 0o600); output.write(json.dumps({"time":time.time(), **result}) + "\n")
                print(json.dumps(result), flush=True)
                if args.command == "run" or args.once or worker.stopping:
                    return 0 if result["state"] not in ("failed", "blocked") else 1
                delay = max(1, min(60, store.next_poll() - time.time()))
                # Short chunks let SIGTERM stop promptly without active terminal input.
                for _ in range(int(delay)):
                    if worker.stopping:
                        break
                    time.sleep(1)
        print(json.dumps(result, indent=2))
        return 0
    except (QueueError, UsageError, OSError, ValueError) as error:
        if args.command=='scheduled-check' and store:
            target=store.state/'scheduler'/'checks.jsonl';target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with target.open('a') as output:
                os.chmod(target,0o600);output.write(json.dumps({'time':time.time(),'state':'blocked','reason':str(error)})+'\n')
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    finally:
        leases.close()
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
