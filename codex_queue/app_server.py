"""Readonly JSON-lines App Server client. Never starts a thread or turn."""
import json
import queue
import subprocess
import threading
import time
from .usage import UsageError, parse


class AppServer:
    live = True

    def __init__(self, codex="codex", bucket="codex", timeout=20):
        self.codex, self.bucket, self.timeout = codex, bucket, timeout

    def read_thread(self, session_id):
        return self.read("thread/read", {"threadId": session_id, "includeTurns": False}).get("thread", {})

    def list_threads(self, cursor=None):
        params = {"limit": 100, "useStateDbOnly": True, "sortKey": "updated_at", "sourceKinds": ["cli", "vscode", "exec", "appServer", "unknown"]}
        if cursor: params['cursor'] = cursor
        return self.read("thread/list", params)

    def read(self, method="account/rateLimits/read", params=None):
        try:
            with subprocess.Popen([self.codex, "app-server", "--listen", "stdio://"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True) as process:
                messages = queue.Queue()

                def read_lines():
                    for line in process.stdout:
                        messages.put(line)
                    messages.put(None)

                thread = threading.Thread(target=read_lines, daemon=True)
                thread.start()
                sequence = 0

                def send(method, params=None, notify=False):
                    nonlocal sequence
                    sequence += 1
                    request = {"method": method}
                    if params is not None:
                        request["params"] = params
                    if not notify:
                        request["id"] = sequence
                    process.stdin.write(json.dumps(request) + "\n")
                    process.stdin.flush()
                    if notify:
                        return None
                    deadline = time.monotonic() + self.timeout
                    while True:
                        line = messages.get(timeout=max(0.001, deadline - time.monotonic()))
                        if line is None:
                            raise UsageError("App Server disconnected")
                        data = json.loads(line)
                        if "method" in data and "id" in data:
                            # External-token refresh/login requests require manual authentication.
                            process.stdin.write(json.dumps({"id": data["id"], "error": {
                                "code": -32601, "message": "manual authentication required"}}) + "\n")
                            process.stdin.flush()
                            raise UsageError("authentication requires manual action")
                        if data.get("id") == sequence:
                            if "error" in data:
                                raise UsageError("App Server request rejected; check authentication/quota")
                            return data["result"]
                        if time.monotonic() >= deadline:
                            raise UsageError("App Server response timed out")

                try:
                    send("initialize", {"clientInfo": {"name": "codex_queue", "title": "Codex Queue", "version": "0.1.0"}})
                    send("initialized", notify=True)
                    if method == "account/rateLimits/read":
                        account = send("account/read", {"refreshToken": False}).get("account")
                        if not account or account.get("type") != "chatgpt":
                            raise UsageError("ChatGPT account authentication required")
                    data = send(method, params)
                    now = time.time()
                    return parse(data, now, now, self.bucket) if method == "account/rateLimits/read" else data
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    thread.join(timeout=1)
        except (OSError, queue.Empty, ValueError, KeyError) as error:
            raise UsageError("App Server unavailable or incompatible; no job submitted") from error
