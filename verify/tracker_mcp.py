"""A small issue tracker served as an MCP server over streamable HTTP.

    python verify/tracker_mcp.py PORT TOKEN LOGFILE

Tools: get_issue(id). Resources: an issue and the contributing guidelines.
It wants `Authorization: Bearer TOKEN`, hands out a session id, and writes one
JSON line per message to LOGFILE (method, whether the token was right, and
the arguments), so the runner can see what Foundry asked for.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, TOKEN, LOG = int(sys.argv[1]), sys.argv[2], sys.argv[3]
SESSION = "tracker-session-1"
LOCK = threading.Lock()

ISSUES = {
    "ISSUE-7": ("ISSUE-7: calc has no mean()\n"
                "Reporter: data team\n\n"
                "We need `mean(values)` in src/calc.py: the arithmetic mean of a list of "
                "numbers. An empty list must raise ValueError with the message "
                "\"mean of an empty list\". Please follow the contributing guidelines."),
    "ISSUE-8": "ISSUE-8: README typo (closed)",
}
GUIDELINES = ("Contributing\n\n"
              "1. Every public function in src/ has a one-line docstring.\n"
              "2. Every new function gets at least one test in tests/test_calc.py, and an "
              "error case gets its own test.\n"
              "3. Run `python -m pytest tests -q` and say how many tests passed.\n"
              "4. Mention the issue id in your final summary.")
RESOURCES = [
    {"uri": "tracker://guidelines/contributing", "name": "contributing",
     "title": "Contributing guidelines", "mimeType": "text/plain"},
    {"uri": "tracker://issues/ISSUE-7", "name": "ISSUE-7", "mimeType": "text/plain",
     "description": "calc has no mean()"},
]
TOOLS = [{"name": "get_issue", "description": "Fetch one issue from the tracker by its id.",
          "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                          "required": ["id"]}}]


def log(entry):
    with LOCK, open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", kind="application/json", headers=()):
        self.send_response(status)
        if kind:
            self.send_header("Content-Type", kind)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        log({"method": "DELETE", "session": self.headers.get("mcp-session-id")})
        self._send(200, b"", "")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        message = json.loads(body)
        token_ok = self.headers.get("authorization") == f"Bearer {TOKEN}"
        method, mid = message.get("method"), message.get("id")
        params = message.get("params") or {}
        log({"method": method, "token_ok": token_ok,
             "session_ok": method == "initialize"
             or self.headers.get("mcp-session-id") == SESSION, "params": params})
        if not token_ok:
            return self._send(401, b'{"error": "unauthorized"}')
        if mid is None or method is None:
            return self._send(202, b"", "")
        extra = [("Mcp-Session-Id", SESSION)] if method == "initialize" else []
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18",
                      "capabilities": {"tools": {}, "resources": {}},
                      "serverInfo": {"name": "tracker", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            issue = ISSUES.get(str((params.get("arguments") or {}).get("id", "")).upper())
            result = {"content": [{"type": "text", "text": issue or "no such issue"}],
                      "isError": issue is None}
        elif method == "resources/list":
            result = {"resources": RESOURCES}
        elif method == "resources/templates/list":
            result = {"resourceTemplates": [{"uriTemplate": "tracker://issues/{id}",
                                             "name": "issue", "description": "An issue by id"}]}
        elif method == "resources/read":
            uri = params.get("uri", "")
            if uri == "tracker://guidelines/contributing":
                text = GUIDELINES
            elif uri.startswith("tracker://issues/") and uri.split("/")[-1].upper() in ISSUES:
                text = ISSUES[uri.split("/")[-1].upper()]
            else:
                return self._send(200, json.dumps({"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32002, "message": f"no resource {uri}"}}).encode())
            result = {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}
        else:
            return self._send(200, json.dumps({"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32601, "message": "no such method"}}).encode())
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}).encode(),
                   headers=extra)


class Server(ThreadingHTTPServer):
    daemon_threads = True


Server(("127.0.0.1", PORT), Handler).serve_forever()
