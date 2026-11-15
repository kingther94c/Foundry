"""A scripted MCP client for `foundry mcp-server`.

    python verify/mcp_client.py REPO CALLS.json

Starts `python -m foundry mcp-server` in REPO with this process's environment,
initializes without elicitation, lists the tools, then makes each call in
CALLS.json in turn: [{"name": "foundry", "arguments": {...}}, ...]. In a
foundry-reply call, "sessionId": "$last" is the sessionId the latest foundry
call answered with. Prints every response and notification, one JSON line
each, then closes stdin and waits for the server to exit.
"""

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


def main(repo, calls_path):
    calls = json.loads(Path(calls_path).read_text(encoding="utf-8"))
    server = subprocess.Popen([sys.executable, "-m", "foundry", "mcp-server"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=repo)
    inbox: "queue.Queue[dict]" = queue.Queue()

    def read():
        for raw in server.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                inbox.put(json.loads(line))
            except ValueError:
                print(json.dumps({"kind": "not_json", "line": line[:300]}), flush=True)
        inbox.put({"kind": "eof"})

    def drain_err():
        for raw in server.stderr:
            sys.stderr.write(raw.decode("utf-8", "replace"))

    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=drain_err, daemon=True).start()
    ids = iter(range(1, 1000))

    def send(message):
        server.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        server.stdin.flush()

    def request(method, params, timeout=5400):
        qid = next(ids)
        send({"jsonrpc": "2.0", "id": qid, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                got = inbox.get(timeout=5)
            except queue.Empty:
                continue
            if got.get("kind") == "eof":
                print(json.dumps({"kind": "server_ended_early", "method": method}), flush=True)
                return None
            if got.get("id") == qid and ("result" in got or "error" in got):
                print(json.dumps({"kind": "response", "method": method, "message": got}),
                      flush=True)
                return got
            print(json.dumps({"kind": "other", "message": got}), flush=True)
        print(json.dumps({"kind": "timeout", "method": method}), flush=True)
        return None

    request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "verify-client", "version": "1"}}, timeout=60)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    request("tools/list", {}, timeout=60)
    last = None
    for call in calls:
        arguments = dict(call["arguments"])
        if arguments.get("sessionId") == "$last":
            arguments["sessionId"] = last
        got = request("tools/call", {"name": call["name"], "arguments": arguments})
        result = (got or {}).get("result") or {}
        session = (result.get("structuredContent") or {}).get("sessionId")
        if session:
            last = session
    server.stdin.close()
    try:
        code = server.wait(timeout=120)
    except subprocess.TimeoutExpired:
        server.kill()
        code = "killed"
    print(json.dumps({"kind": "server_exit", "code": code}), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1], sys.argv[2])
