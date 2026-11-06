"""A small MCP server over stdio for tests/test_mcp.py, standard library only.

The first argument picks how it misbehaves: normal, noinit (never answers
initialize), notools (no tools capability), paged (tools/list in two pages),
noisy (log lines on stdout and stderr), crash (exits on a call), slow (a call
that never returns in time), ping (asks the client for a ping first), big (a
huge result), spawn (starts a child that would outlive it), many (150 tools).
"""

import json
import os
import subprocess
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
TOOLS = [
    {"name": "echo", "description": "Echo the text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "add.numbers", "description": "Add two numbers.",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"},
                                                      "b": {"type": "number"}}}},
    {"name": "env", "description": "Report environment variables.",
     "inputSchema": {"type": "object", "properties": {"names": {"type": "array"}}}},
    {"name": "mixed", "description": "Several kinds of content."},
    {"name": "fails", "description": "Always reports an error."},
]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result(mid, value):
    send({"jsonrpc": "2.0", "id": mid, "result": value})


def text(mid, value, *, error=False):
    result(mid, {"content": [{"type": "text", "text": value}], "isError": error})


for line in sys.stdin:
    if MODE == "noisy":
        print("server log: got a line", flush=True)
        sys.stderr.write("debug: something on stderr\n")
        sys.stderr.flush()
    message = json.loads(line)
    method, mid = message.get("method"), message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        if MODE == "noinit":
            continue
        result(mid, {"protocolVersion": "2025-06-18",
                     "capabilities": {} if MODE == "notools" else {"tools": {}},
                     "serverInfo": {"name": "fake", "version": "1"}})
    elif method == "notifications/initialized" or method == "notifications/cancelled":
        continue
    elif method == "tools/list":
        if MODE == "many":
            start = int(params.get("cursor") or 0)
            page = [{"name": f"t{i}"} for i in range(start, min(start + 60, 150))]
            result(mid, {"tools": page, **({"nextCursor": str(start + 60)}
                                           if start + 60 < 150 else {})})
        elif MODE == "paged" and not params.get("cursor"):
            result(mid, {"tools": TOOLS[:2], "nextCursor": "page-2"})
        elif MODE == "paged":
            result(mid, {"tools": TOOLS[2:]})
        else:
            result(mid, {"tools": TOOLS})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if MODE == "crash":
            sys.stderr.write("fatal: out of cheese\n")
            sys.stderr.flush()
            sys.exit(3)
        if MODE == "slow":
            time.sleep(60)
        if MODE == "spawn":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            text(mid, str(child.pid))
            continue
        pong = ""
        if MODE == "ping":
            send({"jsonrpc": "2.0", "id": "server-1", "method": "ping"})
            send({"jsonrpc": "2.0", "id": "server-2", "method": "sampling/createMessage",
                  "params": {}})
            send({"jsonrpc": "2.0", "method": "notifications/message",
                  "params": {"level": "info", "data": "working"}})
            replies = [json.loads(sys.stdin.readline()) for _ in range(2)]
            pong = " ".join("result" if "result" in r else f"error {r['error']['code']}"
                            for r in replies) + " | "
        if MODE == "big":
            text(mid, "x" * 200_000)
        elif name == "echo":
            text(mid, pong + args["text"])
        elif name == "add.numbers":
            text(mid, str(args.get("a", 0) + args.get("b", 0)))
        elif name == "env":
            text(mid, json.dumps({n: os.environ.get(n) for n in args.get("names", [])}))
        elif name == "mixed":
            result(mid, {"content": [
                {"type": "text", "text": "first"},
                {"type": "image", "data": "aGk=", "mimeType": "image/png"},
                {"type": "resource_link", "uri": "file:///x.txt", "name": "x"},
                {"type": "resource", "resource": {"uri": "file:///y.txt", "text": "inline"}},
                {"type": "resource", "resource": {"uri": "file:///z.bin", "blob": "AA=="}},
                {"type": "hologram"},
            ]})
        elif name == "fails":
            text(mid, "bad input", error=True)
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602,
                                                        "message": f"unknown tool {name}"}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no such method"}})
