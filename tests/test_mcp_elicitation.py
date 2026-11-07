"""`foundry mcp-server` asks its client (D-104): what the permission mode would
ask about is put to a client that offers elicitation, as codex's mcp-server
asks for its approvals, and its user answers.

What it may not do: ask a client that offered no elicitation, take anything
but a clear yes as one, offer "always" (a rule written into the user's own
config), show the client what the terminal would not -- control sequences,
registered secrets -- or leave a task waiting on a question once it has been
stopped or the client has gone.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from foundry.cli import mcp_server
from foundry.cli.mcp_server import ANSWER_SCHEMA, _decision, _question
from foundry.core.events import ApprovalChoice, ApprovalKind, ApprovalRequest, TerminalStatus
from foundry.core.prompts import UNATTENDED_LINE
from foundry.core.session import EventType, SessionStore

from test_cli_e2e import tool_turn
from test_mcp_server import (  # noqa: F401  (fixtures)
    SET_A, _Client, _finish, _journal, _only_session, home, model,
)
from test_resume import repo  # noqa: F401  (fixture)

PRINT_ONE = "python -c \"print(1)\""
HELLO = {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}},
         "clientInfo": {"name": "t", "version": "1"}}


def _asking(home, repo, defaults=None) -> _Client:
    """A client that says it can be asked."""
    made = _Client(home, repo, defaults)
    made.ask("hello", "initialize", HELLO)
    return made


def _questions(client, count: int = 1) -> list[dict]:
    """The questions put to the client, once there are ``count``."""
    def asked():
        return [m for m in client.got if m.get("method") == "elicitation/create"]
    with client.cond:
        assert client.cond.wait_for(lambda: len(asked()) >= count, 30)
    return asked()


def _say(client, question: dict, result: dict | None = None, error: dict | None = None) -> None:
    client.send({"jsonrpc": "2.0", "id": question["id"],
                 **({"error": error} if error else {"result": result})})


def _accept(decision: str) -> dict:
    return {"action": "accept", "content": {"decision": decision}}


def _patching(model) -> None:
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("apply_patch", {"patch": SET_A}, "c2"), _finish("done")]


def _reply_to(model, call_id: str) -> str:
    for request in model.requests:
        for message in request["messages"]:
            if message.get("tool_call_id") == call_id:
                return message["content"]
    raise AssertionError(f"no reply to {call_id}")


# --- who is asked --------------------------------------------------------------------------


def test_a_client_that_can_be_asked_may_choose_default(home, repo):
    asking = _asking(home, repo)
    modes = asking.ask(1, "tools/list")["result"]["tools"][0]["inputSchema"]["properties"][
        "mode"]["enum"]
    assert modes == ["dont_ask", "plan", "accept_edits", "default"]
    asking.server.close()
    silent = _Client(home, repo)
    silent.ask(0, "initialize", {**HELLO, "capabilities": {"elicitation": True}})
    assert "default" not in silent.ask(1, "tools/list")["result"]["tools"][0]["inputSchema"][
        "properties"]["mode"]["enum"]
    refused = silent.call(2, "foundry", prompt="x", mode="default")
    assert refused["content"][0]["text"].endswith("(a client that offers elicitation is asked)")
    silent.server.close()


def test_an_unknown_mode_is_refused_for_a_client_that_can_be_asked(home, repo, model):
    asking = _asking(home, repo)
    result = asking.call(1, "foundry", prompt="x", mode="yolo")
    asking.server.close()
    assert result["content"][0]["text"] == (
        "mode is one of dont_ask, plan, accept_edits, default")
    assert model.requests == []


def test_what_the_mode_asks_about_is_asked_and_a_yes_lets_it_run(home, repo, model):
    asking = _asking(home, repo)
    _patching(model)
    asking.start(1, "foundry", prompt="set A to 2", mode="default")
    (question,) = _questions(asking)
    assert question["params"]["requestedSchema"] == ANSWER_SCHEMA
    assert ANSWER_SCHEMA["properties"]["decision"]["enum"] == ["once", "session", "deny", "abort"]
    message = question["params"]["message"]
    assert message.startswith(f"Foundry session {_only_session(home)} in ")
    assert "asks to change files: " in message and "=======\nA = 2\n" in message
    _say(asking, question, _accept("once"))
    assert asking.answer(1)["result"]["isError"] is False
    asking.server.close()
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    approvals = [r.payload["choice"] for r in SessionStore.read_records(
        _journal(home, _only_session(home))) if r.type == EventType.APPROVAL]
    assert approvals == ["once"]


@pytest.mark.parametrize("answer, applied", [
    ({"result": _accept("session")}, True),
    ({"result": _accept("deny")}, False),
    ({"result": {"action": "decline"}}, False),
    ({"result": {"action": "cancel"}}, False),
    ({"result": _accept("always")}, False),
    ({"result": {"action": "accept"}}, False),
    ({"result": "yes"}, False),
    ({"error": {"code": -32601, "message": "not offered"}}, False),
], ids=["session", "deny", "decline", "cancel", "always-not-offered", "no-content",
        "not-an-object", "an-error"])
def test_only_a_clear_yes_is_a_yes(home, repo, model, answer, applied):
    asking = _asking(home, repo)
    _patching(model)
    asking.start(1, "foundry", prompt="set A to 2", mode="default")
    (question,) = _questions(asking)
    _say(asking, question, answer.get("result"), answer.get("error"))
    assert asking.answer(1)["result"]["structuredContent"]["status"] == "partial"
    asking.server.close()
    assert ((repo / "a.py").read_text(encoding="utf-8") == "A = 2\n") is applied
    if not applied:
        assert _reply_to(model, "c2") == "the user declined this operation"


def test_no_and_stop_stops_the_task(home, repo, model):
    asking = _asking(home, repo)
    _patching(model)
    asking.start(1, "foundry", prompt="set A to 2", mode="default")
    (question,) = _questions(asking)
    _say(asking, question, _accept("abort"))
    # Stopped by its user's answer, not by a cancel: it is answered.
    assert asking.answer(1)["result"]["structuredContent"]["status"] == "cancelled"
    asking.server.close()
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_yes_for_the_session_is_not_asked_again(home, repo, model):
    asking = _asking(home, repo)
    model.turns = [tool_turn("run_command", {"command": PRINT_ONE}, "c1"),
                   tool_turn("run_command", {"command": PRINT_ONE}, "c2"), _finish("ran")]
    asking.start(1, "foundry", prompt="print twice", mode="accept_edits")
    (question,) = _questions(asking)
    assert "asks to run a command: " + PRINT_ONE in question["params"]["message"]
    _say(asking, question, _accept("session"))
    asking.answer(1)
    asking.server.close()
    assert len([m for m in asking.got if m.get("method") == "elicitation/create"]) == 1
    assert "exit code 0" in _reply_to(model, "c2")


def test_a_session_that_never_asks_puts_nothing_to_the_client(home, repo, model):
    asking = _asking(home, repo)
    _patching(model)
    asking.call(1, "foundry", prompt="set A to 2")
    asking.server.close()
    assert not [m for m in asking.got if m.get("method") == "elicitation/create"]
    assert _reply_to(model, "c2") == ("blocked by policy: no matching allow rule and approval "
                                      "is unavailable")


def test_the_system_prompt_says_whether_anyone_can_be_asked(home, repo, model):
    model.turns = [_finish("done")]
    asking = _asking(home, repo)
    asking.call(1, "foundry", prompt="one", mode="accept_edits")
    asking.server.close()
    silent = _Client(home, repo)
    silent.call(1, "foundry", prompt="two", mode="accept_edits")
    silent.server.close()
    first, second = (request["messages"][0]["content"] for request in model.requests)
    assert UNATTENDED_LINE not in first and UNATTENDED_LINE in second


# --- a question no longer asked ------------------------------------------------------------


def test_a_cancel_while_asked_withdraws_the_question(home, repo, model):
    asking = _asking(home, repo)
    _patching(model)
    asking.start(1, "foundry", prompt="set A to 2", mode="default")
    (question,) = _questions(asking)
    asking.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                 "params": {"requestId": 1}})
    asking.settle()
    assert asking.answers(1) == []
    withdrawn = [m["params"] for m in asking.got if m.get("method") == "notifications/cancelled"]
    assert withdrawn == [{"requestId": question["id"], "reason": "the task was stopped"}]
    # Its answer, coming late, is nobody's.
    _say(asking, question, _accept("once"))
    asking.server.close()
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert SessionStore.terminal_status(_journal(home, _only_session(home))) is (
        TerminalStatus.CANCELLED)
    approvals = [r.payload["choice"] for r in SessionStore.read_records(
        _journal(home, _only_session(home))) if r.type == EventType.APPROVAL]
    assert approvals == ["abort"]


def test_the_client_going_while_asked_stops_the_task(home, repo, model):
    asking = _asking(home, repo)
    _patching(model)
    asking.start(1, "foundry", prompt="set A to 2", mode="default")
    _questions(asking)
    asking.server.close()
    asking.settle()
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert SessionStore.terminal_status(_journal(home, _only_session(home))) is (
        TerminalStatus.CANCELLED)


def test_a_question_is_not_put_once_the_client_has_gone(home, repo):
    asking = _asking(home, repo)
    asking.server._closing = True
    entry = SimpleNamespace(wiring=SimpleNamespace(runtime=SimpleNamespace(scrub=None)))
    assert asking.server._ask(entry, None) is ApprovalChoice.ABORT
    assert not [m for m in asking.got if m.get("method") == "elicitation/create"]


def test_a_reply_to_no_question_is_ignored(home, repo):
    asking = _asking(home, repo)
    for qid in ("foundry-ask-99", 5, None, [1], {"a": 1}):
        asking.send({"jsonrpc": "2.0", "id": qid, "result": _accept("once")})
    assert asking.server._questions == {}
    assert [m.get("id") for m in asking.got] == ["hello"]
    asking.server.close()


# --- what the client is shown --------------------------------------------------------------


def _request(display: str, detail: str = "", kind=ApprovalKind.COMMAND, tool="run_command"):
    return ApprovalRequest(request_id="r1", approval_kind=kind, tool=tool, display=display,
                           reason="no rule allows it", detail=detail)


_WIRING = SimpleNamespace(session=SimpleNamespace(session_id="s1"),
                          workspace=SimpleNamespace(root="C:/repo"))


def test_the_question_says_what_would_run_and_why(home):
    assert _question(_request("git push"), _WIRING, None) == (
        "Foundry session s1 in C:/repo asks to run a command: git push\n"
        "It asks because: no rule allows it")
    other = _request("docs: read doc://x", kind=ApprovalKind.OTHER, tool="read_mcp_resource")
    assert "asks to use read_mcp_resource: docs: read doc://x" in _question(other, _WIRING, None)


def test_the_question_shows_no_control_sequences_and_no_secrets():
    text = _question(_request("echo \x1b[31mred\x1b]0;title\x07 s3cret"), _WIRING,
                     lambda value: value.replace("s3cret", "[redacted]"))
    assert "\x1b" not in text and "\x07" not in text
    assert text.splitlines()[0].endswith("echo red [redacted]")


def test_a_long_patch_is_cut_to_fit(monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_QUESTION_CHARS", 60)
    text = _question(_request("patch a.py", "x" * 500, ApprovalKind.PATCH, "apply_patch"),
                     _WIRING, None)
    assert len(text) == 60 and text.endswith("...")


@pytest.mark.parametrize("reply, choice", [
    ({"result": _accept("once")}, ApprovalChoice.ONCE),
    ({"result": _accept("session")}, ApprovalChoice.SESSION),
    ({"result": _accept("abort")}, ApprovalChoice.ABORT),
    ({"result": _accept("always")}, ApprovalChoice.DENY),
    ({"result": {"action": "accept", "content": "once"}}, ApprovalChoice.DENY),
    ({"result": {"action": "decline", "content": {"decision": "once"}}}, ApprovalChoice.DENY),
    ({}, ApprovalChoice.DENY),
])
def test_the_answer_is_read_strictly(reply, choice):
    assert _decision(reply) is choice


def test_waiting_is_for_the_answer_not_a_while(home, repo, model, monkeypatch):
    """A person may take their time: the question has no deadline of its own."""
    asking = _asking(home, repo)
    _patching(model)
    waited = threading.Event()
    real = asking.server._answers.wait

    def counting(timeout=None):
        waited.set()
        return real(timeout)

    monkeypatch.setattr(asking.server._answers, "wait", counting)
    asking.start(1, "foundry", prompt="set A to 2", mode="default")
    (question,) = _questions(asking)
    assert waited.wait(30)
    _say(asking, question, _accept("once"))
    assert asking.answer(1)["result"]["isError"] is False
    asking.server.close()
