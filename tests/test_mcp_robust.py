"""An MCP server cannot hang or end a session (D-155).

P2's review of the MCP client found:

* Over stdio, a request wrote to the server's input on the caller's thread:
  a server that stopped reading filled the pipe, and the write held the
  request past its timeout, Ctrl+C and close -- and the reader too, which
  answers the server's pings on the same lock.
* Over HTTP, ending the session waited QUIET_TIMEOUT for each read, so a
  server that trickled its answer held the session's exit.
* JSON's `\\ud800` decodes to a lone surrogate, which no UTF-8 can carry: a
  server's name or a model's call holding one raised at the next print or
  journal write.
* A check that met a type it did not expect -- `{"server": []}`, a schema's
  `"required": 5` -- raised a TypeError, which ended the session.
* A server named `a__b` ran into its tools' names.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from foundry.core.config import load_config
from foundry.core.config_mcp import McpServerConfig
from foundry.core.conversation import ToolUseBlock, without_lone_surrogates
from foundry.core.errors import ConfigError, InvalidToolCall
from foundry.core.mcp import (
    MAX_UNREAD_MESSAGES,
    QUIET_TIMEOUT,
    McpError,
    McpTool,
    StdioClient,
    StreamableHttpClient,
)
from foundry.core.mcp_resources import LIST_RESOURCES, ResourceTool
from foundry.core.tools.base import Operation, ToolKind, ToolOutput
from foundry.core.winapi import IS_WINDOWS

from test_hooks import (  # noqa: F401  (fixtures)
    Scripted,
    _group,
    _hooks_json,
    _notices,
    _result,
    _wire,
    call,
    home,
    machine,
    repo,
    turn,
)
from test_mcp import SERVER, _config


def _within(seconds, action):
    """``action``'s outcome, or a failure if it is still running after
    ``seconds``: a hang fails the test instead of the suite."""
    outcome: dict = {}

    def run():
        try:
            outcome["value"] = action()
        except BaseException as exc:  # noqa: BLE001 - reported below
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"still running after {seconds}s"
    return outcome


def _stdio(tmp_path, mode, **extra):
    server = McpServerConfig(name="fake", command=sys.executable, args=(SERVER, mode),
                             startup_timeout_s=10, tool_timeout_s=1, **extra)
    client = StdioClient(server, tmp_path)
    client.start()
    return client


# --- a server that stops reading -----------------------------------------------------------


def test_a_server_that_reads_nothing_times_a_call_out_and_is_closed(tmp_path):
    client = _stdio(tmp_path, "deaf")
    try:
        outcome = _within(30, lambda: client.call_tool("echo", {"text": "x" * 4_000_000}))
        assert isinstance(outcome.get("error"), McpError)
        assert "timed out" in str(outcome["error"])
    finally:
        started = time.monotonic()
        _within(30, client.close)
    assert time.monotonic() - started < 15


def test_a_server_flooding_requests_it_never_reads_answers_to(tmp_path):
    client = _stdio(tmp_path, "flood")
    try:
        outcome = _within(30, lambda: client.call_tool("echo", {"text": "hi"}))
        assert isinstance(outcome.get("error"), McpError)
        again = _within(30, lambda: client.call_tool("echo", {"text": "again"}))
        assert isinstance(again.get("error"), McpError)
    finally:
        _within(30, client.close)


def test_a_request_is_refused_once_the_server_has_left_enough_unread(tmp_path):
    client = StdioClient(McpServerConfig(name="fake", command="x"), tmp_path)
    client._process = SimpleNamespace(stdin=object())     # started; nothing is written
    for _ in range(MAX_UNREAD_MESSAGES):
        client._send({"jsonrpc": "2.0", "method": "notifications/progress"})
    with pytest.raises(McpError, match=f"not reading what it is sent: {MAX_UNREAD_MESSAGES}"):
        client._send({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})


def test_a_server_whose_input_is_closed_fails_a_call_at_once(tmp_path):
    client = _stdio(tmp_path, "noinput")
    try:
        outcome = _within(30, lambda: client.call_tool("echo", {"text": "x" * 100_000}))
        assert isinstance(outcome.get("error"), McpError)
        assert "the server is not running" in str(outcome["error"])
    finally:
        _within(30, client.close)


def test_a_server_that_reads_still_answers_and_ends_when_its_input_closes(tmp_path):
    client = _stdio(tmp_path, "normal")
    process = client._process
    try:
        reply = client.call_tool("echo", {"text": "hello"})
        assert reply["content"][0]["text"] == "hello"
    finally:
        client.close()
    assert process.returncode == 0          # it saw the end of its input, and ended


# --- an HTTP server that trickles ----------------------------------------------------------


def _trickling_server():
    """A server that answers a request with its body a byte at a time."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n")
            for _ in range(200):
                try:
                    conn.sendall(b"x")
                except OSError:
                    return
                time.sleep(0.5)

    threading.Thread(target=serve, daemon=True).start()
    return listener


def test_ending_an_http_session_waits_quiet_timeout_in_all():
    listener = _trickling_server()
    try:
        port = listener.getsockname()[1]
        client = StreamableHttpClient(McpServerConfig(name="web",
                                                      url=f"http://127.0.0.1:{port}/mcp"),
                                      environ={})
        client._session = "s1"
        started = time.monotonic()
        _within(30, client.close)
        assert time.monotonic() - started < QUIET_TIMEOUT + 1.5
    finally:
        listener.close()


# --- lone surrogates -----------------------------------------------------------------------


def test_a_lone_surrogate_becomes_a_replacement_character():
    assert without_lone_surrogates({"a\ud800": ["b\udfff", 1, None], "c": "\U0001f600"}) == {
        "a�": ["b�", 1, None], "c": "\U0001f600"}
    parsed = ToolUseBlock(call_id="c", name="t",
                          arguments='{"path": "a\\ud800.py", "ok": "\\ud83d\\ude00"}'
                          ).parse_arguments()
    assert parsed == {"path": "a�.py", "ok": "\U0001f600"}


def test_a_server_s_lone_surrogates_are_replaced(tmp_path):
    client = _stdio(tmp_path, "surrogate")
    try:
        assert client.server_info["name"] == "fake �"
        (tool,) = client.list_tools()
        assert tool["description"] == "half � a pair"
        json.dumps(tool, ensure_ascii=False).encode("utf-8")
    finally:
        client.close()


def test_an_http_server_s_lone_surrogates_are_replaced():
    client = StreamableHttpClient(McpServerConfig(name="web", url="http://127.0.0.1:9/mcp"),
                                  environ={})
    reply = client._event('{"jsonrpc": "2.0", "id": 7, "result": {"text": "a\\ud800"}}', 7)
    assert reply["result"]["text"] == "a�"

    class _Body:
        def read(self, size):
            return b'{"jsonrpc": "2.0", "id": 8, "result": {"text": "b\\udfff"}}'

    assert client._json_reply(_Body(), 8)["result"]["text"] == "b�"


def test_a_model_s_lone_surrogate_does_not_end_the_turn(repo, home):
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a\ud800.py"})),
                                turn(text="done")], approve=None)
    wiring.runtime.run_turn("read it")
    assert _result(wiring, "c1").is_error
    assert wiring.session.path.read_text(encoding="utf-8")


# --- a check that meets a type it did not expect -------------------------------------------


def test_a_server_list_that_is_not_a_name_is_an_invalid_call():
    tool = ResourceTool(LIST_RESOURCES, {"docs": object()})
    with pytest.raises(InvalidToolCall, match="server is a server's name: docs"):
        tool.validate({"server": []})


def test_a_schema_whose_required_is_not_a_list_requires_nothing():
    tool = McpTool(name="mcp__docs__x", kind=ToolKind.MUTATOR, client=None, server="docs",
                   remote="x", description="", parameters={"type": "object", "required": 5})
    assert tool.validate({"q": 1}).args == {"q": 1}


class _Fussy:
    """A tool whose own check raises TypeError, as a bug in one would."""

    name = "fussy"
    kind = ToolKind.READ_ONLY

    def schema(self):
        from foundry.core.conversation import ToolSchema
        return ToolSchema(name=self.name, description="", parameters={"type": "object"})

    def validate(self, args) -> Operation:
        if args.get("key") == "ok":
            return Operation(tool=self.name, kind=self.kind, args=args, display="fussy",
                             target="fussy")
        return {}[args["key"]]          # a list is unhashable: TypeError

    def execute(self, op, ctx):
        return ToolOutput(content="ran")


def test_a_type_error_in_a_check_is_an_invalid_call_not_the_end(repo, home):
    wiring = _wire(repo, home, [turn(call("c1", "fussy", {"key": []})), turn(text="done")],
                   approve=None)
    wiring.runtime.registry.register(_Fussy())
    wiring.runtime.run_turn("try it")
    result = _result(wiring, "c1")
    assert result.is_error and result.content.startswith("invalid tool call:")


@pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")
def test_a_hook_s_rewrite_that_raises_type_error_leaves_the_call_as_made(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "updatedInput": {"key": []}}})
    _hooks_json(home, {"PreToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "fussy", {"key": "ok"})), turn(text="done")])
    wiring.runtime.registry.register(_Fussy())
    wiring.runtime.run_turn("try it")
    assert _result(wiring, "c1").content == "ran"
    assert any("rewrite is not a valid call" in text for text in _notices(wiring))


# --- a server's name -----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a__b", "docs_", "__x"])
def test_a_server_name_may_not_run_into_its_tools(tmp_path, name):
    with pytest.raises(ConfigError, match="may not hold __ or end in _"):
        load_config(home=_config(tmp_path / "home", f"[mcp_servers.{name}]\n"
                                                    'command = "x"\n'))


@pytest.mark.parametrize("name", ["docs", "my_docs", "my-docs", "_x"])
def test_a_plain_server_name_is_taken(tmp_path, name):
    config = load_config(home=_config(tmp_path / "home", f"[mcp_servers.{name}]\n"
                                                         'command = "x"\n'))
    assert name in config.mcp_servers
