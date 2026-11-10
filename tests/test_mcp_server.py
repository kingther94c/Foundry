"""`foundry mcp-server` (D-103): Foundry offered to another agent as an MCP
server over stdio, as `codex mcp-server` offers codex.

The client gets `foundry`, which runs a task in a repository and answers with
how it ended, and `foundry-reply`, which gives a session another task. What it
may not do: approve anything on the user's behalf -- a task runs as exec runs
one, fail-closed -- answer a request the client cancelled, let two tasks work
in one repository at once, or print anything but the protocol on stdout.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from rich.console import Console

from foundry.cli import mcp_server
from foundry.cli.unattended import NO_FINISH, unattended
from foundry.cli.wiring import build
from foundry.cli.mcp_server import Server
from foundry.core.config_mcp import McpServerConfig
from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock, Usage
from foundry.core.backends.base import Capabilities
from foundry.core.backends.replay import _emit
from foundry.core.events import MessageDelta, TerminalStatus
from foundry.core.mcp import StdioClient
from foundry.core.policy.engine import Mode
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.git import run_git

from test_cli_e2e import text_turn, tool_turn
from test_resume import repo  # noqa: F401  (fixture)

SRC = str(Path(__file__).resolve().parents[1] / "src")
ADD_C = ("*** Begin Patch\n*** Add File: c.py\n+C = 3\n*** End Patch\n")
SET_A = ("*** Begin Patch\n*** Update File: a.py\n"
         "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n*** End Patch\n")


def _finish(summary: str, status: str = "partial") -> dict:
    return tool_turn("finish", {"status": status, "summary": summary, "claims": []}, "f1")


class _Model(BaseHTTPRequestHandler):
    """A Chat Completions server; with a gate, each answer waits for it."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with server.cond:
            server.requests.append(body)
            server.cond.notify_all()
        if server.gate is not None:
            server.gate.wait(30)
        with server.cond:
            turn = server.turns[min(server.index, len(server.turns) - 1)]
            server.index += 1
        payload = json.dumps({"model": "fake", "choices": [turn],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 4}}).encode()
        if server.status != 200:
            payload = json.dumps({"error": {"message": "no such key"}}).encode()
        self.send_response(server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def model():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    httpd.daemon_threads = True
    httpd.requests, httpd.turns, httpd.index, httpd.gate, httpd.status = [], [], 0, None, 200
    httpd.cond = threading.Condition()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    if httpd.gate is not None:
        httpd.gate.set()
    httpd.shutdown()


def _asked(model, count: int) -> None:
    """Wait until the model has been asked ``count`` times."""
    with model.cond:
        assert model.cond.wait_for(lambda: len(model.requests) >= count, 30)


@pytest.fixture()
def home(tmp_path, model, monkeypatch):
    host, port = model.server_address
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    return home


def _repository(place: Path) -> Path:
    place.mkdir()
    (place / "o.py").write_text("O = 1\n", encoding="utf-8")
    run_git(["init"], place)
    run_git(["config", "user.email", "t@example.com"], place)
    run_git(["config", "user.name", "T"], place)
    run_git(["add", "."], place)
    run_git(["commit", "-m", "initial"], place)
    return place


@pytest.fixture()
def other(tmp_path):
    """A second repository."""
    return _repository(tmp_path / "other")


class _Client:
    """The client's side: what it sends, and each line the server writes."""

    def __init__(self, home, cwd, defaults=None):
        self.got: list[dict] = []
        self.cond = threading.Condition()
        # What the sessions display, as stderr would show it.
        self.out = io.StringIO()
        self.server = Server(self._line, cwd=cwd, home=home, defaults=defaults,
                             console=lambda: Console(file=self.out, width=1000))

    def _line(self, data: bytes) -> None:
        assert data.endswith(b"\n") and data.count(b"\n") == 1
        with self.cond:
            self.got.append(json.loads(data))
            self.cond.notify_all()

    def send(self, message) -> None:
        self.server.receive(json.dumps(message).encode("utf-8"))

    def answers(self, rid) -> list[dict]:
        with self.cond:
            return [m for m in self.got if m.get("id") == rid and "method" not in m]

    def answer(self, rid) -> dict:
        with self.cond:
            assert self.cond.wait_for(lambda: self.answers(rid), 30), f"no answer to {rid}"
        return self.answers(rid)[0]

    def ask(self, rid, method, params=None) -> dict:
        self.send({"jsonrpc": "2.0", "id": rid, "method": method,
                   **({"params": params} if params is not None else {})})
        return self.answer(rid)

    def start(self, rid, name, meta=None, **arguments) -> None:
        params = {"name": name, "arguments": arguments, **({"_meta": meta} if meta else {})}
        self.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params})

    def call(self, rid, name, meta=None, **arguments) -> dict:
        self.start(rid, name, meta, **arguments)
        return self.answer(rid)["result"]

    def settle(self) -> None:
        for worker in list(self.server._workers):
            worker.join(30)


@pytest.fixture()
def client(home, repo):
    made = _Client(home, repo)
    yield made
    made.server.close()


def _journal(home, session_id: str) -> Path:
    return home / "sessions" / session_id / "events.jsonl"


def _types(home, session_id: str) -> list[str]:
    return [r.type for r in SessionStore.read_records(_journal(home, session_id))]


def _only_session(home) -> str:
    (found,) = [p.name for p in (home / "sessions").iterdir() if p.is_dir()]
    return found


# --- the protocol --------------------------------------------------------------------------


def test_it_says_what_it_offers(client):
    hello = client.ask(1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "1"}})["result"]
    assert hello["protocolVersion"] == "2025-03-26"
    assert hello["capabilities"] == {"tools": {}}
    assert hello["serverInfo"]["name"] == "foundry" and "foundry-reply" in hello["instructions"]
    assert client.ask(2, "initialize", {"protocolVersion": "1999-01-01"})["result"][
        "protocolVersion"] == "2025-06-18"
    assert client.ask(3, "ping")["result"] == {}
    tools = client.ask(4, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["foundry", "foundry-reply"]
    assert tools[0]["inputSchema"]["properties"]["mode"]["enum"] == [
        "dont_ask", "plan", "accept_edits"]
    assert tools[1]["inputSchema"]["required"] == ["sessionId", "prompt"]


@pytest.mark.parametrize("line, code", [
    (b"not json", -32700),
    (b'[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]', -32600),
    (b'{"jsonrpc": "2.0", "id": true, "method": "ping"}', -32600),
    (b'{"jsonrpc": "2.0", "id": null, "method": "ping"}', -32600),
], ids=["not-json", "a-batch", "a-boolean-id", "a-null-id"])
def test_what_is_not_a_request_is_answered_with_no_id(client, line, code):
    client.server.receive(line)
    assert client.got == [{"jsonrpc": "2.0", "id": None,
                           "error": {"code": code, "message": client.got[0]["error"]["message"]}}]


def test_what_it_does_not_offer_is_an_error(client):
    assert client.ask(1, "resources/list")["error"]["code"] == -32601
    unknown = client.ask(2, "tools/call", {"name": "codex", "arguments": {"prompt": "x"}})
    assert unknown["error"] == {"code": -32602, "message": "no tool 'codex': this server "
                                                           "offers foundry and foundry-reply"}
    assert client.ask(3, "ping", [1])["error"]["code"] == -32602
    client.send({"jsonrpc": "2.0", "id": 4, "method": "ping", "params": None})
    assert client.answer(4)["result"] == {}


def test_notifications_and_replies_are_not_answered(client):
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client.send({"jsonrpc": "2.0", "method": "notifications/whatever", "params": {}})
    client.send({"jsonrpc": "2.0", "id": 9, "result": {}})
    client.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                 "params": {"requestId": 12345}})
    assert client.got == []


def test_a_message_too_large_is_refused_and_the_next_read(home, repo, monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_MESSAGE_BYTES", 64)
    made = _Client(home, repo)
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "x" * 200})
    mcp_server.serve(io.BytesIO(f'{big}\n\n{{"jsonrpc": "2.0", "id": 2, "method": "ping"}}\n'
                                .encode()), made.server)
    assert [m.get("id") for m in made.got] == [None, 2]
    assert made.got[0]["error"]["message"] == "a message over 64 bytes"


# --- a task --------------------------------------------------------------------------------


def test_a_task_answers_with_its_summary_and_session(client, model, home):
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   _finish("A is 1", "completed")]
    result = client.call(1, "foundry", prompt="what is A?")
    said = result["structuredContent"]
    assert (result["isError"], said["status"], said["text"]) == (False, "completed", "A is 1")
    assert result["content"][0] == {"type": "text", "text": "A is 1"}
    assert result["content"][1]["text"] == (
        f"Foundry session {said['sessionId']} ended completed. Give it another task with "
        "foundry-reply and this sessionId.")
    assert model.requests[0]["messages"][-1]["content"] == "what is A?"
    assert SessionStore.terminal_status(_journal(home, said["sessionId"])) is (
        TerminalStatus.COMPLETED)
    # The session's display, where stderr would show it.
    assert f"session {said['sessionId']}" in client.out.getvalue()


def test_a_reply_in_prose_is_partial_and_says_why(client, model, home):
    model.turns = [text_turn("A is 1")]
    result = client.call(1, "foundry", prompt="what is A?")
    said = result["structuredContent"]
    assert (said["status"], said["text"], said["reason"]) == ("partial", "A is 1", NO_FINISH)
    assert f"ended partial ({NO_FINISH})." in result["content"][1]["text"]
    assert result["isError"] is False
    assert f"partial {NO_FINISH}" in client.out.getvalue()
    assert SessionStore.terminal_status(_journal(home, said["sessionId"])) is (
        TerminalStatus.PARTIAL)


def test_nothing_is_approved_on_the_user_s_behalf(client, model, repo):
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("apply_patch", {"patch": SET_A}, "c2"), _finish("tried")]
    assert client.call(1, "foundry", prompt="set A to 2")["structuredContent"]["status"] == (
        "partial")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    refused = [m for m in model.requests[2]["messages"] if m.get("tool_call_id") == "c2"]
    assert refused[0]["content"] == ("blocked by policy: no matching allow rule and approval "
                                     "is unavailable")


def test_what_the_mode_would_ask_about_is_refused_with_nobody_asked(home, model, repo):
    """accept_edits lets a patch through; a command it would ask about is
    refused, and nobody -- not the terminal, not the client -- is asked."""
    made = _Client(home, repo, {"overrides": {"mode": Mode.ACCEPT_EDITS}})
    model.turns = [tool_turn("run_command", {"command": "python -c \"print(1)\""}, "c1"),
                   _finish("tried")]
    made.call(1, "foundry", prompt="run it")
    made.server.close()
    refused = [m for m in model.requests[1]["messages"] if m.get("tool_call_id") == "c1"]
    assert refused[0]["content"] == ("blocked by policy: no matching allow rule and approval "
                                     "is unavailable")


@pytest.mark.parametrize("served, asked, applied", [
    (None, "accept_edits", True),
    (Mode.ACCEPT_EDITS, None, True),
    (Mode.ACCEPT_EDITS, "plan", False),
    (Mode.PLAN, "dont_ask", False),
], ids=["asked", "the-server-s", "asked-wins", "dont-ask-asked"])
def test_the_mode_is_the_call_s_else_the_server_s(home, model, repo, served, asked, applied):
    made = _Client(home, repo, {"overrides": {"mode": served}} if served else None)
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("apply_patch", {"patch": SET_A}, "c2"), _finish("done")]
    made.call(1, "foundry", prompt="set A to 2", **({"mode": asked} if asked else {}))
    made.server.close()
    assert ((repo / "a.py").read_text(encoding="utf-8") == "A = 2\n") is applied


def test_cwd_is_relative_to_the_server_s_own_directory(home, model, repo, tmp_path):
    made = _Client(home, tmp_path)
    model.turns = [_finish("here")]
    said = made.call(1, "foundry", prompt="where?", cwd="project")["structuredContent"]
    made.server.close()
    header = next(SessionStore.read_records(_journal(home, said["sessionId"])))
    assert Path(header.payload["workspace"]) == repo.resolve()


def test_a_place_foundry_cannot_work_is_the_tool_s_error(client, model, tmp_path):
    (tmp_path / "plain").mkdir()
    result = client.call(1, "foundry", prompt="x", cwd=str(tmp_path / "plain"))
    assert result["isError"] is True and "is not inside a Git repository" in (
        result["content"][0]["text"])
    missing = client.call(2, "foundry", prompt="x", cwd=str(tmp_path / "nowhere"))
    assert missing["isError"] is True
    assert model.requests == []


@pytest.mark.parametrize("name, arguments, message", [
    ("foundry", {}, "foundry needs the prompt: the task"),
    ("foundry", {"prompt": "  "}, "foundry needs the prompt: the task"),
    ("foundry", {"prompt": "x", "sandbox": "read-only"},
     "foundry takes no 'sandbox'; it takes config, cwd, mode, model, profile, prompt"),
    ("foundry", {"prompt": "x", "mode": "default"},
     "mode is one of dont_ask, plan, accept_edits: nobody is there to approve"),
    ("foundry", {"prompt": "x", "model": 5}, "model is a string, and not an empty one"),
    ("foundry", {"prompt": "x", "cwd": ""}, "cwd is a string, and not an empty one"),
    ("foundry", {"prompt": "x", "config": {"api_key": "k"}}, "config: -c may not set api_key"),
    ("foundry", {"prompt": "x", "config": {"runtime.modle": 1}}, "config: "),
    ("foundry", {"prompt": "x", "config": "runtime.max_tool_rounds=5"},
     "config is an object of settings, as -c takes them"),
    ("foundry", [1], "foundry's arguments are an object"),
    ("foundry", {"prompt": "x" * 11}, "the prompt is over 10 characters"),
    ("foundry-reply", {"prompt": "x"}, "foundry-reply needs the sessionId a foundry call"),
    ("foundry-reply", {"sessionId": "s", "prompt": "x", "cwd": "."},
     "foundry-reply takes no 'cwd'; it takes prompt, sessionId"),
    ("foundry-reply", {"sessionId": "nope", "prompt": "x"}, "no session nope"),
    ("foundry-reply", {"sessionId": "../home", "prompt": "x"}, "no session ../home"),
])
def test_a_call_that_cannot_run_says_why(client, model, monkeypatch, name, arguments, message):
    monkeypatch.setattr(mcp_server, "MAX_PROMPT_CHARS", 10)
    result = client.ask(1, "tools/call", {"name": name, "arguments": arguments})["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"].startswith(message)
    assert model.requests == []


def test_settings_from_config_reach_the_session(client, model, home):
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("read_file", {"path": "b.py"}, "c2"), _finish("never")]
    said = client.call(1, "foundry", prompt="read both",
                       config={"runtime.max_tool_calls": 1})["structuredContent"]
    assert (said["status"], said["text"]) == ("partial", "tool calls exceeded (1)")
    assert len(model.requests) == 2


# --- foundry-reply -------------------------------------------------------------------------


def test_a_reply_continues_the_session_held_open(client, model, home):
    model.turns = [_finish("first"), _finish("second")]
    first = client.call(1, "foundry", prompt="the first task")["structuredContent"]
    second = client.call(2, "foundry-reply", sessionId=first["sessionId"],
                         prompt="the second task")["structuredContent"]
    assert (second["sessionId"], second["text"]) == (first["sessionId"], "second")
    sent = [m.get("content") for m in model.requests[1]["messages"] if m["role"] == "user"]
    assert sent[-2:] == ["the first task", "the second task"]
    assert EventType.RESUMED not in _types(home, first["sessionId"])


def _unanswered(messages: list[dict]) -> list[str]:
    """The tool calls in a Chat Completions conversation with no result,
    which every provider refuses."""
    called = [call["id"] for m in messages if m["role"] == "assistant"
              for call in m.get("tool_calls") or ()]
    answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    return [call for call in called if call not in answered]


def test_the_first_task_s_finish_is_answered_before_a_reply(client, model, home):
    """Found in P9 (D-167): the finish that ended the first task went to the
    model again with no result, which every provider refuses."""
    model.turns = [_finish("first"), _finish("second")]
    first = client.call(1, "foundry", prompt="the first task")["structuredContent"]
    client.call(2, "foundry-reply", sessionId=first["sessionId"], prompt="the second task")
    messages = model.requests[1]["messages"]
    assert _unanswered(messages) == []
    said = [m["content"] for m in messages if m["role"] == "tool"]
    assert said == ["finish was accepted: the task ended with status partial."]
    # Journaled, so a resume finds nothing to repair.
    assert _types(home, first["sessionId"]).count(EventType.TOOL_REPLY) == 2


def test_a_reply_to_a_session_no_longer_open_resumes_it(home, model, repo):
    model.turns = [_finish("first"), _finish("second")]
    before = _Client(home, repo)
    first = before.call(1, "foundry", prompt="the first task")["structuredContent"]
    before.server.close()
    after = _Client(home, repo)
    second = after.call(1, "foundry-reply", sessionId=first["sessionId"],
                        prompt="the second task")["structuredContent"]
    after.server.close()
    assert (second["sessionId"], second["text"]) == (first["sessionId"], "second")
    assert EventType.RESUMED in _types(home, first["sessionId"])
    assert any("the first task" in str(m.get("content")) for m in model.requests[1]["messages"])
    assert _unanswered(model.requests[1]["messages"]) == []
    # The task carries the resume's note, as exec --resume's does.
    task = model.requests[1]["messages"][-1]["content"]
    assert task.endswith("\n\nthe second task") and task != "the second task"


def test_past_the_most_held_open_the_least_recently_used_is_closed(home, model, repo, other,
                                                                   monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_OPEN_SESSIONS", 1)
    made = _Client(home, repo)
    model.turns = [_finish("done")]
    first = made.call(1, "foundry", prompt="one")["structuredContent"]["sessionId"]
    made.call(2, "foundry", prompt="two", cwd=str(other))
    assert list(made.server._open) != [first]
    # Closed, so its journal is no longer held: a reply resumes it.
    assert made.call(3, "foundry-reply", sessionId=first, prompt="three")[
        "structuredContent"]["status"] == "partial"
    made.server.close()
    assert EventType.RESUMED in _types(home, first)


def test_when_every_session_held_open_is_at_work_a_new_one_waits(home, model, repo, other,
                                                                  monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_OPEN_SESSIONS", 1)
    made = _Client(home, repo)
    model.turns, model.gate = [_finish("done")], threading.Event()
    made.start(1, "foundry", prompt="one")
    _asked(model, 1)
    result = made.call(2, "foundry", prompt="two", cwd=str(other))
    assert result["isError"] is True and result["content"][0]["text"] == (
        "every session this server holds open (1) is at work; wait for one to answer")
    model.gate.set()
    assert made.answer(1)["result"]["structuredContent"]["status"] == "partial"
    made.server.close()


# --- one at a time -------------------------------------------------------------------------


def test_one_task_at_a_time_in_a_repository(client, model, home, repo, other):
    (repo / "sub").mkdir()
    model.turns, model.gate = [_finish("done")], threading.Event()
    client.start(1, "foundry", prompt="one")
    _asked(model, 1)
    # A folder in it is the same repository.
    busy = client.call(2, "foundry", prompt="two", cwd=str(repo / "sub"))
    assert busy["isError"] is True and busy["content"][0]["text"].startswith(
        "another Foundry task is at work in ")
    reply = client.call(3, "foundry-reply", sessionId=_only_session(home), prompt="more")
    assert reply["content"][0]["text"] == (f"session {_only_session(home)} is at work on "
                                           "another task; wait for its answer")
    # Another repository is another matter.
    client.start(4, "foundry", prompt="four", cwd=str(other))
    _asked(model, 2)
    model.gate.set()
    assert client.answer(1)["result"]["isError"] is False
    assert client.answer(4)["result"]["isError"] is False


def test_a_request_id_is_not_used_twice_at_once(client, model):
    model.turns, model.gate = [_finish("done")], threading.Event()
    client.start(1, "foundry", prompt="one")
    _asked(model, 1)
    client.start(1, "foundry", prompt="again")
    with client.cond:
        assert client.cond.wait_for(lambda: client.answers(1), 30)
    assert client.answers(1)[0]["error"]["message"] == (
        "request 1 is still at work; an id is not used twice")
    model.gate.set()
    client.settle()
    assert client.answers(1)[1]["result"]["isError"] is False


# --- cancelled -----------------------------------------------------------------------------


def test_a_cancelled_task_stops_and_is_not_answered(home, model, repo):
    made = _Client(home, repo, {"overrides": {"mode": Mode.ACCEPT_EDITS}})
    model.turns, model.gate = [tool_turn("apply_patch", {"patch": ADD_C}, "c1")], threading.Event()
    made.start(7, "foundry", prompt="add c")
    _asked(model, 1)
    made.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
               "params": {"requestId": 7, "reason": "changed my mind"}})
    # Answered while the task is at work.
    assert made.ask(8, "ping")["result"] == {}
    model.gate.set()
    made.settle()
    assert made.answers(7) == []
    assert not (repo / "c.py").exists()
    session = _only_session(home)
    assert SessionStore.terminal_status(_journal(home, session)) is TerminalStatus.CANCELLED
    # The task, not the session: a reply goes on with it.
    model.turns = [_finish("went on")]
    assert made.call(9, "foundry-reply", sessionId=session, prompt="go on")[
        "structuredContent"]["text"] == "went on"
    made.server.close()


def test_a_task_cancelled_before_it_began_never_begins(client, model, home, monkeypatch):
    began = threading.Event()
    real = Server._work

    def late(self, *args):
        began.wait(30)
        return real(self, *args)

    monkeypatch.setattr(Server, "_work", late)
    client.start(1, "foundry", prompt="one")
    client.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                 "params": {"requestId": 1}})
    began.set()
    client.settle()
    assert client.answers(1) == [] and model.requests == []
    assert not (home / "sessions").exists() or not any((home / "sessions").iterdir())
    assert client.server._requests == {} and client.server._cancelled == set()


def test_a_cancel_while_the_session_is_built_stops_the_task(client, model, home, monkeypatch):
    real = Server._build

    def build_then_cancelled(self, where, task, *, resume):
        wiring = real(self, where, task, resume=resume)
        self._cancel(1)
        return wiring

    monkeypatch.setattr(Server, "_build", build_then_cancelled)
    model.turns = [_finish("never")]
    client.start(1, "foundry", prompt="one")
    client.settle()
    assert client.answers(1) == [] and model.requests == []
    assert SessionStore.terminal_status(_journal(home, _only_session(home))) is (
        TerminalStatus.CANCELLED)


def test_a_cancel_after_the_answer_is_spent_with_it(client, model, home):
    model.turns = [_finish("first"), _finish("second")]
    first = client.call(1, "foundry", prompt="one")["structuredContent"]
    held = client.server._open[first["sessionId"]]
    held.wiring.runtime.cancel()  # as a cancel that came as the task ended would
    client.server._settle(99, held)
    assert client.call(2, "foundry-reply", sessionId=first["sessionId"], prompt="two")[
        "structuredContent"]["text"] == "second"


# --- progress ------------------------------------------------------------------------------


def test_each_tool_call_is_progress_when_it_is_asked_for(client, model):
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"), _finish("read")]
    client.call(1, "foundry", {"progressToken": "p1"}, prompt="read a")
    progress = [m["params"] for m in client.got if m.get("method") == "notifications/progress"]
    assert progress == [{"progressToken": "p1", "progress": 1, "message": "read a.py"},
                        {"progressToken": "p1", "progress": 2, "message": "finish: partial"}]
    # A call that did not ask is told nothing, in the same session.
    model.turns, model.index = [tool_turn("read_file", {"path": "b.py"}, "c3"),
                                _finish("read")], 0
    client.call(2, "foundry-reply", sessionId=client.answer(1)["result"]["structuredContent"][
        "sessionId"], prompt="read b")
    assert len([m for m in client.got if m.get("method") == "notifications/progress"]) == 2


def test_a_long_display_is_cut_in_progress(client, model, monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_PROGRESS_CHARS", 8)
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"), _finish("read")]
    client.call(1, "foundry", {"progressToken": 5}, prompt="read a")
    sent = [m["params"] for m in client.got if m.get("method") == "notifications/progress"]
    assert sent[0] == {"progressToken": 5, "progress": 1, "message": "read ..."}
    assert sent[1]["message"] == "finish: partial"[:5] + "..."


def test_a_token_that_is_no_token_asks_for_nothing(client, model):
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"), _finish("read")]
    client.call(1, "foundry", {"progressToken": {"not": "a token"}}, prompt="read a")
    assert not [m for m in client.got if m.get("method") == "notifications/progress"]


# --- the end -------------------------------------------------------------------------------


def test_when_the_client_goes_every_session_is_closed(home, model, repo, other, monkeypatch):
    monkeypatch.setattr(mcp_server, "SHUTDOWN_WAIT_S", 0.1)
    made = _Client(home, repo)
    model.turns = [_finish("done")]
    idle = made.call(1, "foundry", prompt="one")["structuredContent"]["sessionId"]
    model.gate = threading.Event()
    made.start(2, "foundry", prompt="two", cwd=str(other))
    _asked(model, 2)
    made.server.close()
    model.gate.set()
    made.settle()
    assert made.answers(2) == [] and made.server._open == {}
    busy = next(p.name for p in (home / "sessions").iterdir() if p.is_dir() and p.name != idle)
    assert SessionStore.terminal_status(_journal(home, busy)) is TerminalStatus.CANCELLED
    # Neither journal is held any longer.
    model.gate, model.turns = None, [_finish("again")]
    after = _Client(home, repo)
    for rid, session in enumerate((idle, busy)):
        assert after.call(rid, "foundry-reply", sessionId=session, prompt="again")[
            "structuredContent"]["text"] == "again"
    after.server.close()


# --- the runtime, cancelled from another thread ---------------------------------------------


FIRST = "a" * 24  # one streamed piece; "second" is the next


class _Cancelling:
    """An answer that asks for a patch, the runtime cancelled part-way through
    it, once the whole of it has come, or never."""

    name = "cancelling"

    def __init__(self, at: str):
        self.at = at
        self.runtime = None
        self.asked = 0

    def capabilities(self):
        return Capabilities()

    def stream_turn(self, request):
        self.asked += 1
        usage = Usage(input_tokens=10, output_tokens=5)
        if self.asked > 1:
            done = ToolUseBlock(call_id="f1", name="finish", arguments=json.dumps(
                {"status": "partial", "summary": "added", "claims": []}))
            yield from _emit(ModelTurn(text="", tool_calls=(done,), usage=usage,
                                       stop_reason=StopReason.TOOL_USE))
            return
        call = ToolUseBlock(call_id="c1", name="apply_patch",
                            arguments=json.dumps({"patch": ADD_C}))
        turn = ModelTurn(text=FIRST + "second", tool_calls=(call,), usage=usage,
                         stop_reason=StopReason.TOOL_USE)
        for index, event in enumerate(_emit(turn)):
            if self.at == "mid" and index == 1:
                self.runtime.cancel()
            yield event
        if self.at == "end":
            self.runtime.cancel()


@pytest.mark.parametrize("at", ["mid", "end", "never"])
def test_a_cancel_from_another_thread_stops_the_turn_before_its_calls(repo, tmp_path, at):
    wiring = build(repo, home=tmp_path / "home", interactive=False,
                   overrides={"mode": Mode.ACCEPT_EDITS},
                   console=Console(file=io.StringIO(), width=100))
    unattended(wiring)
    wiring.runtime.backend = backend = _Cancelling(at)
    backend.runtime = wiring.runtime
    said = []
    wiring.runtime.events.subscribe(
        lambda event: said.append(event.text) if isinstance(event, MessageDelta) else None)
    outcome = wiring.runtime.run_turn("add c")
    wiring.session.close()
    history = wiring.runtime.context.history
    if at == "never":
        assert outcome.status is TerminalStatus.PARTIAL and (repo / "c.py").exists()
        return
    assert outcome.status is TerminalStatus.CANCELLED
    assert not (repo / "c.py").exists()
    if at == "mid":
        # Stopped where it was: the rest of the answer reached nobody.
        assert "".join(said) == FIRST
        assert history[-1].role.value == "user"
    else:
        assert "".join(said) == FIRST + "second"
        assert [m.role.value for m in history[-2:]] == ["assistant", "tool"]


def test_a_running_command_hears_the_cancel(repo, tmp_path):
    wiring = build(repo, home=tmp_path / "home", interactive=False,
                   console=Console(file=io.StringIO(), width=100))
    try:
        assert wiring.runtime.tool_ctx.cancelled() is False
        wiring.runtime.cancel()
        assert wiring.runtime.tool_ctx.cancelled() is True
    finally:
        wiring.session.close()


# --- over stdio ----------------------------------------------------------------------------


def _environment(home) -> dict[str, str]:
    return {**os.environ, "FOUNDRY_HOME": str(home), "PYTHONPATH": SRC}


def test_over_stdio_stdout_is_the_protocol_alone(home, model, repo):
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   _finish("A is 1", "completed")]
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "foundry", "arguments": {"prompt": "what is A?"}}},
    ]
    process = subprocess.Popen([sys.executable, "-m", "foundry", "mcp-server"], cwd=repo,
                               env=_environment(home), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for message in messages:
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
    process.stdin.flush()
    # The answer comes before the input ends: the task is not run at the end.
    got = [json.loads(process.stdout.readline()) for _ in range(2)]
    process.stdin.close()
    rest, err = process.communicate(timeout=60)
    assert process.returncode == 0
    assert rest == b""
    assert [m["id"] for m in got] == [1, 2]
    assert got[1]["result"]["structuredContent"]["text"] == "A is 1"
    # The session's display went to stderr.
    assert b"read a.py" in err


def test_foundry_s_own_client_can_use_it(home, model, repo):
    model.turns = [_finish("hello from the server", "completed")]
    entry = McpServerConfig(name="foundry", command=sys.executable,
                            args=("-m", "foundry", "mcp-server", "--mode", "plan"),
                            env=(("FOUNDRY_HOME", str(home)), ("PYTHONPATH", SRC)))
    client = StdioClient(entry, repo)
    client.start()
    try:
        assert [tool["name"] for tool in client.list_tools()] == ["foundry", "foundry-reply"]
        result = client.call_tool("foundry", {"prompt": "say hello"})
    finally:
        client.close()
    assert result["structuredContent"]["text"] == "hello from the server"
    assert SessionStore.terminal_status(_journal(
        home, result["structuredContent"]["sessionId"])) is TerminalStatus.COMPLETED


def test_a_setting_that_cannot_work_is_said_at_start(home, repo):
    done = subprocess.run([sys.executable, "-m", "foundry", "mcp-server", "-c", "modle=x"],
                          cwd=repo, env=_environment(home), capture_output=True, timeout=60,
                          input=b"")
    assert done.returncode == 2 and done.stdout == b""
    assert b"modle" in done.stderr
    profile = subprocess.run([sys.executable, "-m", "foundry", "mcp-server", "-p", "nope"],
                             cwd=repo, env=_environment(home), capture_output=True, timeout=60,
                             input=b"")
    assert profile.returncode == 2 and b"nope" in profile.stderr


# --- what a session is given ---------------------------------------------------------------


def test_a_configured_mode_does_not_decide(home, model, repo):
    """As exec: what an unattended task may do is said by whoever starts it."""
    with (home / "config.toml").open("a", encoding="utf-8") as config:
        config.write('[runtime]\nmode = "accept_edits"\n')
    made = _Client(home, repo)
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("apply_patch", {"patch": SET_A}, "c2"), _finish("tried")]
    made.call(1, "foundry", prompt="set A to 2")
    made.server.close()
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_the_model_and_profile_asked_for_are_used(home, model, client, other):
    with (home / "config.toml").open("a", encoding="utf-8") as config:
        config.write('[profiles.alt.backend]\nmodel = "alt-model"\n')
    model.turns = [_finish("done")]
    client.call(1, "foundry", prompt="one", model="other-model")
    client.call(2, "foundry", prompt="two", profile="alt", cwd=str(other))
    assert [sent["model"] for sent in model.requests] == ["other-model", "alt-model"]


@pytest.mark.parametrize("served, given", [
    ({"runtime": {"max_tool_calls": 1}}, {}),
    ({}, {"runtime.max_tool_calls": 1}),
    ({"runtime": {"max_tool_calls": 5}}, {"max_tool_calls": 1}),
], ids=["the-server-s", "the-call-s", "the-call-s-wins"])
def test_settings_are_the_server_s_then_the_call_s(home, model, repo, served, given):
    made = _Client(home, repo, {"settings": served})
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("read_file", {"path": "b.py"}, "c2"), _finish("never")]
    said = made.call(1, "foundry", prompt="read both", config=given)["structuredContent"]
    made.server.close()
    assert said["text"] == "tool calls exceeded (1)"


def test_a_task_that_could_not_be_worked_on_is_an_error(client, model):
    model.status = 401
    result = client.call(1, "foundry", prompt="one")
    assert result["structuredContent"]["status"] == "blocked"
    assert result["isError"] is True


def test_a_fault_of_foundry_s_own_ends_the_session(client, model, home, monkeypatch):
    def broken(self, entry, task):
        raise RuntimeError("boom")

    monkeypatch.setattr(Server, "_run", broken)
    result = client.call(1, "foundry", prompt="one")
    assert result == {"content": [{"type": "text", "text": "Foundry failed: RuntimeError: boom"}],
                      "isError": True}
    session = _only_session(home)
    assert client.server._open == {}
    assert SessionStore.terminal_status(_journal(home, session)) is TerminalStatus.FAILED
    # Its repository is free again.
    monkeypatch.undo()
    model.turns = [_finish("done")]
    assert client.call(2, "foundry", prompt="two")["isError"] is False


def test_the_least_recently_used_is_the_one_closed(home, model, repo, other, tmp_path,
                                                   monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_OPEN_SESSIONS", 2)
    third = _repository(tmp_path / "third")
    made = _Client(home, repo)
    model.turns = [_finish("done")]
    first = made.call(1, "foundry", prompt="one")["structuredContent"]["sessionId"]
    second = made.call(2, "foundry", prompt="two", cwd=str(other))["structuredContent"][
        "sessionId"]
    made.call(3, "foundry-reply", sessionId=first, prompt="again")
    last = made.call(4, "foundry", prompt="three", cwd=str(third))["structuredContent"][
        "sessionId"]
    assert set(made.server._open) == {first, last} and second not in made.server._open
    made.server.close()


STRAY = (
    "import os\n"
    "import foundry.cli.mcp_server as served\n"
    "real = served.Server.receive\n"
    "def receive(self, line):\n"
    "    print('a stray print')\n"
    "    os.write(1, b'a stray write\\n')\n"
    "    return real(self, line)\n"
    "served.Server.receive = receive\n"
    "from foundry.cli.app import main\n"
    "raise SystemExit(main(['mcp-server']))\n")


def test_what_else_writes_to_stdout_reaches_stderr(home, repo):
    process = subprocess.Popen([sys.executable, "-c", STRAY], cwd=repo, env=_environment(home),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    out, err = process.communicate(b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n',
                                   timeout=60)
    assert process.returncode == 0
    assert out.decode("utf-8").splitlines() == ['{"jsonrpc": "2.0", "id": 1, "result": {}}']
    assert b"a stray print" in err and b"a stray write" in err
