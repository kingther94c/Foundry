"""MCP servers reached over streamable HTTP (D-081): a ``url`` in the user's
``[mcp_servers.<name>]`` instead of a ``command``, with the bearer token in
the environment variable ``bearer_token_env_var`` names, as codex's.

A real HTTP server in this process speaks the protocol here. What must hold:
the reply comes as JSON or as an event stream, the session id the server
hands out goes with every message after initialize and the session is ended
on close, the token is sent only as the Authorization header and never shown,
a server's own requests get a ping and nothing else, a call given up on is
cut and the server told, and a server that fails costs a warning, never the
session -- the tools themselves are offered and policed as a program's are.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from foundry.core import mcp as mcp_module
from foundry.core.config import load_config
from foundry.core.config_mcp import McpServerConfig
from foundry.core.errors import Cancelled, ConfigError, ToolError
from foundry.core.mcp import McpError, StreamableHttpClient, start_servers
from foundry.core.redaction import Redactor
from foundry.core.tools.base import ToolContext, ToolKind
from foundry.core.tools.registry import default_registry

from test_mcp import _config
from test_resume import _wire, repo  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

TOKEN = "tok-3f9a2c7e5b1d4a8f"
TOOLS = [
    {"name": "echo", "description": "Echo the text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "add.numbers", "description": "Add two numbers."},
    {"name": "slow", "description": "Takes its time."},
]


class _Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass   # a connection the client cut: what the tests do on purpose


class Fake:
    """A streamable-HTTP MCP server on a free loopback port. ``reply`` is
    "json" or "events"; ``token`` is the bearer token it wants, if any."""

    def __init__(self, *, reply="json", token="", session="sess-42"):
        self.reply, self.token, self.session = reply, token, session
        self.posts: list[tuple[dict, dict]] = []      # (headers, message)
        self.deletes: list[dict] = []
        self.answers: list[dict] = []                 # replies to its own requests
        self.ended = False                            # the session is gone: 404
        self.ping = False                             # ask the client first
        self.huge = 0                                 # pad a reply to this many bytes
        self.flood = 0                                # an event of this many short lines
        self.release = threading.Event()
        self.lock = threading.Condition()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                fake.post(self)

            def do_DELETE(self):
                with fake.lock:
                    fake.deletes.append({k.lower(): v for k, v in self.headers.items()})
                self._send(200, b"", "")

            def _send(self, status, body, kind, headers=()):
                self.send_response(status)
                if kind:
                    self.send_header("Content-Type", kind)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.handler = Handler
        self.httpd = _Quiet(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def methods(self):
        return [m.get("method") for _, m in self.posts]

    def post(self, h):
        headers = {k.lower(): v for k, v in h.headers.items()}
        message = json.loads(h.rfile.read(int(headers.get("content-length", 0))))
        with self.lock:
            self.posts.append((headers, message))
            if "method" not in message:
                self.answers.append(message)
                self.lock.notify_all()
        if self.token and headers.get("authorization") != f"Bearer {self.token}":
            return h._send(401, b'{"error": "who are you"}', "application/json")
        method, mid = message.get("method"), message.get("id")
        if method != "initialize" and self.session and (
                self.ended or headers.get("mcp-session-id") != self.session):
            return h._send(404, b"no such session", "text/plain")
        if mid is None or method is None:
            return h._send(202, b"", "")
        extra = [("Mcp-Session-Id", self.session)] if method == "initialize" and self.session else []
        events = []
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name, args = message["params"]["name"], message["params"].get("arguments") or {}
            said = ""
            if self.ping:
                events += [{"jsonrpc": "2.0", "id": "server-1", "method": "ping"},
                           {"jsonrpc": "2.0", "id": "server-2",
                            "method": "sampling/createMessage", "params": {}},
                           {"jsonrpc": "2.0", "method": "notifications/message",
                            "params": {"level": "info", "data": "working"}}]
            if name == "slow":
                self.release.wait(10)
                said = "late"
            elif name == "echo":
                said = args["text"] + "x" * self.huge
            elif name == "add.numbers":
                said = str(args.get("a", 0) + args.get("b", 0))
            result = {"content": [{"type": "text", "text": said}]}
        else:
            return h._send(200, json.dumps({"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32601, "message": "no such method"}}).encode(), "application/json")
        reply = {"jsonrpc": "2.0", "id": mid, "result": result}
        if self.reply == "json" and not events:
            return h._send(200, json.dumps(reply).encode(), "application/json", extra)
        # An event stream: what the server has to say first, then the reply.
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        for name, value in extra:
            h.send_header(name, value)
        h.send_header("Connection", "close")
        h.end_headers()
        h.wfile.write(b": a comment\n\nevent: message\nid: 1\n")
        if self.flood:
            h.wfile.write(b"data: 0123456789\n" * self.flood + b"\n")
        for event in events:
            h.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            h.wfile.flush()
        if self.ping:
            with self.lock:
                self.lock.wait_for(lambda: len(self.answers) >= 2, timeout=5)
            got = " ".join("result" if "result" in a else f"error {a['error']['code']}"
                           for a in self.answers)
            reply["result"]["content"][0]["text"] = f"{got} | " + reply["result"]["content"][0]["text"]
        # Over several data lines, which the client joins with newlines.
        lines = json.dumps(reply, indent=1).splitlines()
        h.wfile.write("".join(f"data: {line}\n" for line in lines).encode() + b"\n")
        h.wfile.flush()
        h.close_connection = True


@pytest.fixture()
def fake():
    servers = []

    def make(**kw):
        server = Fake(**kw)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


@pytest.fixture()
def started(tmp_path):
    opened = []

    def start(*servers, environ=None):
        registry = default_registry()
        found = start_servers({s.name: s for s in servers}, tmp_path, registry,
                              environ=environ if environ is not None else {})
        opened.append(found)
        return found, registry

    yield start
    for found in opened:
        found.close()


def _remote(server, name="web", **extra) -> McpServerConfig:
    return McpServerConfig(name=name, url=server.url, startup_timeout_s=5, **extra)


def _run(registry, name, args, **ctx):
    tool = registry.get(name)
    return tool.execute(tool.validate(args),
                        ToolContext(workspace=None, artifacts=None, read_tracker=None, **ctx))


# --- the setting -------------------------------------------------------------------------


def test_the_users_config_names_a_server_by_its_address(tmp_path):
    config = load_config(home=_config(tmp_path / "home", (
        '[mcp_servers.docs]\nurl = "https://mcp.example.com/mcp"\n'
        'bearer_token_env_var = "DOCS_TOKEN"\nread_only = true\n')))
    server = config.mcp_servers["docs"]
    assert (server.url, server.bearer_token_env_var, server.command) == (
        "https://mcp.example.com/mcp", "DOCS_TOKEN", "")
    assert server.read_only and server.shown_as() == "https://mcp.example.com"


@pytest.mark.parametrize("entry, message", [
    ('url = "https://x.example/mcp"\ncommand = "node"\n', "url and command do not go together"),
    ('url = "https://x.example/mcp"\nenv_vars = ["A"]\n', "url and env_vars do not go together"),
    ('command = "node"\nbearer_token_env_var = "A"\n', "is for a server reached by url"),
    ('url = "ftp://x.example/mcp"\n', "url must be an http:// or https:// address"),
    ('url = "https://x.example:port/mcp"\n', "url must be an http:// or https:// address"),
    ('url = 7\n', "url must be an http:// or https:// address"),
    ('url = "https://me:pw@x.example/mcp"\n', "may not carry a user name or password"),
    ('url = "http://x.example/mcp"\nbearer_token_env_var = "A"\n',
     "a bearer token is sent only over https, or to this machine"),
    ('url = "https://x.example/mcp"\nbearer_token_env_var = "sk-live.abc"\n',
     "must be the name of an environment variable, not the token itself"),
])
def test_a_bad_entry_is_refused(tmp_path, entry, message):
    with pytest.raises(ConfigError, match=message) as caught:
        load_config(home=_config(tmp_path / "home", "[mcp_servers.x]\n" + entry))
    assert "sk-live" not in str(caught.value) and "pw@" not in str(caught.value)


def test_plain_http_is_for_this_machine_or_without_a_token(tmp_path):
    config = load_config(home=_config(tmp_path / "home", (
        '[mcp_servers.a]\nurl = "http://127.0.0.1:8123/mcp"\nbearer_token_env_var = "A"\n'
        '[mcp_servers.b]\nurl = "http://intranet.example/mcp"\n')))
    assert config.mcp_servers["a"].shown_as() == "http://127.0.0.1:8123"
    assert config.mcp_servers["b"].shown_as() == "http://intranet.example"


# --- the transport -----------------------------------------------------------------------


@pytest.mark.parametrize("reply", ["json", "events"])
def test_a_server_comes_up_and_its_tools_are_offered(fake, started, reply):
    server = fake(reply=reply)
    found, registry = started(_remote(server))
    assert found.failures == {}
    assert found.tools["web"] == ["mcp__web__echo", "mcp__web__add_numbers", "mcp__web__slow"]
    # Under the policy as a program's are: a mutator unless the user vouched.
    assert registry.get("mcp__web__echo").kind is ToolKind.MUTATOR
    assert _run(registry, "mcp__web__echo", {"text": "hello"}).content == "hello"
    assert _run(registry, "mcp__web__add_numbers", {"a": 2, "b": 3}).content == "5"
    assert server.methods() == ["initialize", "notifications/initialized", "tools/list",
                                "tools/call", "tools/call"]


def test_the_session_goes_with_every_message_after_initialize(fake, started):
    server = fake()
    found, registry = started(_remote(server))
    _run(registry, "mcp__web__echo", {"text": "a"})
    first, *rest = [headers for headers, _ in server.posts]
    assert "mcp-session-id" not in first and "mcp-protocol-version" not in first
    assert {h["mcp-session-id"] for h in rest} == {"sess-42"}
    assert {h["mcp-protocol-version"] for h in rest} == {"2025-06-18"}
    assert {h["accept"] for h in [first, *rest]} == {"application/json, text/event-stream"}
    found.close()
    assert [d["mcp-session-id"] for d in server.deletes] == ["sess-42"]


def test_a_server_that_keeps_no_session_is_not_sent_one(fake, started):
    server = fake(session="")
    found, registry = started(_remote(server))
    assert _run(registry, "mcp__web__echo", {"text": "a"}).content == "a"
    assert all("mcp-session-id" not in h for h, _ in server.posts)
    found.close()
    assert server.deletes == []


def test_the_token_is_the_authorization_header_and_never_shown(fake, started):
    server = fake(token=TOKEN)
    redactor = Redactor()
    client = StreamableHttpClient(_remote(server, bearer_token_env_var="WEB_TOKEN"),
                                  {"WEB_TOKEN": TOKEN}, redactor=redactor)
    client.start()
    try:
        assert {h["authorization"] for h, _ in server.posts} == {f"Bearer {TOKEN}"}
        assert redactor.scrub(f"the server said {TOKEN}") == "the server said [redacted]"
    finally:
        client.close()


def test_a_token_variable_that_is_not_set_costs_the_server(fake, started):
    server = fake(token=TOKEN)
    found, _ = started(_remote(server, bearer_token_env_var="WEB_TOKEN"), environ={})
    assert found.failures == {"web": "WEB_TOKEN, which bearer_token_env_var names, is not set"}
    assert server.posts == []


@pytest.mark.parametrize("environ, said", [
    ({"WEB_TOKEN": "wrong-token-123"}, "the server refused the token (HTTP 401)"),
    ({}, "the server refused the request (HTTP 401); it may want a token, named by "
         "bearer_token_env_var"),
])
def test_a_refused_token_is_said(fake, started, environ, said):
    server = fake(token=TOKEN)
    entry = _remote(server, bearer_token_env_var="WEB_TOKEN" if environ else "")
    found, _ = started(entry, environ=environ)
    assert found.failures == {"web": said}


def test_a_token_a_header_cannot_carry_is_refused_unquoted(fake, started):
    server = fake(token=TOKEN)
    found, _ = started(_remote(server, bearer_token_env_var="WEB_TOKEN"),
                       environ={"WEB_TOKEN": "first-line\nsecond-line"})
    assert found.failures == {"web": "the token in WEB_TOKEN has characters a header "
                                     "cannot carry"}
    assert server.posts == []


def test_a_session_id_a_header_cannot_carry_is_refused(fake, started):
    server = fake(session="sess 42")
    found, _ = started(_remote(server))
    assert found.failures == {"web": "its session id cannot be sent back in a header"}


def test_the_servers_own_requests_get_a_ping_and_nothing_else(fake, started):
    server = fake(reply="events")
    _, registry = started(_remote(server))
    server.ping = True
    assert _run(registry, "mcp__web__echo", {"text": "done"}).content == (
        "result error -32601 | done")
    assert [a["id"] for a in server.answers] == ["server-1", "server-2"]
    assert all(h["mcp-session-id"] == "sess-42" for h, m in server.posts if "id" in m
               and "method" not in m)


def test_a_call_that_takes_too_long_is_cut_and_the_server_told(fake, started):
    server = fake()
    _, registry = started(_remote(server, tool_timeout_s=0.5))
    began = time.monotonic()
    with pytest.raises(ToolError, match="tools/call timed out after 0.5s"):
        _run(registry, "mcp__web__slow", {})
    assert time.monotonic() - began < 3
    _told(server, "timed out")
    _cut()


def test_ctrl_c_cuts_a_call(fake, started):
    server = fake(reply="events")
    _, registry = started(_remote(server))
    stop_at = time.monotonic() + 0.3
    with pytest.raises(Cancelled):
        _run(registry, "mcp__web__slow", {}, cancelled=lambda: time.monotonic() > stop_at)
    _told(server, "interrupted")
    _cut()


def _cut():
    """The connection was cut: the thread that was reading it is gone too,
    long before its own timeout."""
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and any(
            t.name == "mcp-web-http" for t in threading.enumerate()):
        time.sleep(0.02)
    assert not any(t.name == "mcp-web-http" for t in threading.enumerate())


def _told(server, reason):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        told = [m for _, m in server.posts if m.get("method") == "notifications/cancelled"]
        if told:
            assert told[0]["params"] == {"requestId": 3, "reason": reason}
            return
        time.sleep(0.02)
    pytest.fail("the server was not told")


def test_a_session_the_server_ended_stops_its_tools(fake, started):
    server = fake()
    found, registry = started(_remote(server))
    server.ended = True
    with pytest.raises(ToolError, match=r"the server ended this session \(HTTP 404\)"):
        _run(registry, "mcp__web__echo", {"text": "a"})
    client = found.clients[0]
    assert not client.running
    posts = len(server.posts)
    with pytest.raises(ToolError, match="the server ended this session"):
        _run(registry, "mcp__web__echo", {"text": "a"})
    assert len(server.posts) == posts                     # not asked again
    found.close()
    assert server.deletes == []


@pytest.mark.parametrize("reply", ["json", "events"])
def test_a_reply_too_big_is_refused(fake, started, monkeypatch, reply):
    server = fake(reply=reply)
    _, registry = started(_remote(server))
    monkeypatch.setattr(mcp_module, "MAX_MESSAGE_BYTES", 4_000)
    server.huge = 10_000
    with pytest.raises(ToolError, match="over 4000 bytes"):
        _run(registry, "mcp__web__echo", {"text": "a"})


def test_an_event_of_many_short_lines_is_bounded_too(fake, started, monkeypatch):
    server = fake(reply="events")
    _, registry = started(_remote(server))
    monkeypatch.setattr(mcp_module, "MAX_MESSAGE_BYTES", 4_000)
    server.flood = 1_000
    with pytest.raises(ToolError, match="an event over 4000 bytes"):
        _run(registry, "mcp__web__echo", {"text": "a"})


def test_a_server_that_is_not_there_costs_a_warning(started):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    found, registry = started(McpServerConfig(name="gone", url=f"http://127.0.0.1:{port}/mcp",
                                              startup_timeout_s=5))
    assert found.failures["gone"].startswith("the connection failed")
    assert found.tools == {}


class _Raw:
    """A server that answers every POST with the same thing."""

    def __init__(self, status, kind, body):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(status)
                if kind:
                    self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"


@pytest.mark.parametrize("status, kind, body, said", [
    (200, "text/html", b"<html>", "its reply was text/html, not JSON or an event stream"),
    (200, "application/json", b"not json", "its reply was not JSON"),
    (200, "application/json", b'{"jsonrpc": "2.0", "id": 99, "result": {}}',
     "its reply answered another request"),
    (202, "", b"", "initialize was accepted with no reply"),
    (500, "text/plain", b"  out   of\ncheese ", "HTTP 500: out of cheese"),
    (200, "text/event-stream", b"data: {}\n\n", "its event stream ended without the reply"),
])
def test_a_reply_foundry_cannot_use_is_said(started, status, kind, body, said):
    raw = _Raw(status, kind, body)
    try:
        found, _ = started(McpServerConfig(name="odd", url=raw.url, startup_timeout_s=5))
        assert found.failures == {"odd": said}
    finally:
        raw.httpd.shutdown()
        raw.httpd.server_close()


# --- shown -------------------------------------------------------------------------------


def test_mcp_shows_the_host_not_the_path(fake, repo, tmp_path):
    server = fake()
    home = _config(tmp_path / "home",
                   f'[mcp_servers.web]\nurl = "{server.url}/k-sekrit?key=sekrit"\n')
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/mcp"))
    host = server.url.removesuffix("/mcp")
    assert f"[bold]web[/bold] [dim]({host})[/dim] [green]running[/green]" in shown
    assert "mcp__web__echo" in shown and "sekrit" not in shown
