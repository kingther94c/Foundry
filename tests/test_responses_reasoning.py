"""A reasoning model's reasoning, carried across rounds on the Responses API
(D-089), as codex carries it: nothing is stored (``store: false``), so the
reasoning is asked for encrypted (``include: ["reasoning.encrypted_content"]``)
and sent back with the turn it belongs to.

What carries it: the reasoning goes back only to the model that produced it,
before that turn's message and calls, and without the item ids a stateless
request cannot name; and it is asked for only once reasoning is configured or
the model has shown it reasons, so a model that does not reason is never sent
the field.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli.wiring import build
from foundry.core.backends.base import ReasoningComplete, collect_turn
from foundry.core.backends.responses import PROVIDER, ResponsesBackend, build_body
from foundry.core.conversation import (Message, OpaqueBlock, Role, TextBlock, ToolResultBlock,
                                       ToolUseBlock, TurnRequest)

from test_backend_responses import _Handler, base_url, request_with_history, server  # noqa: F401
from test_cli_e2e import repo  # noqa: F401  (fixture)

SUMMARY = [{"type": "summary_text", "text": "Reading the file first."}]
REASONING = {"type": "reasoning", "id": "rs_1", "summary": SUMMARY, "encrypted_content": "ENC1"}
KEPT = {"type": "reasoning", "summary": SUMMARY, "encrypted_content": "ENC1"}
CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_9", "name": "read_file",
        "arguments": '{"path": "src/calc.py"}'}


def _turn_with(opaque_model="gpt-5", items=(KEPT,), provider=PROVIDER, data=None):
    opaque = OpaqueBlock(provider=provider, data=data if data is not None else json.dumps(
        {"model": opaque_model, "items": list(items)}))
    return Message(role=Role.ASSISTANT, blocks=(
        TextBlock("Looking."), ToolUseBlock(call_id="call_9", name="read_file",
                                            arguments='{"path": "a.py"}'), opaque))


def _request(*assistant, model="gpt-5"):
    return TurnRequest(messages=(
        Message.text(Role.SYSTEM, "you are foundry"), Message.text(Role.USER, "read it"),
        *assistant,
        Message(role=Role.TOOL, blocks=(ToolResultBlock(call_id="call_9", content="body"),)),
    ), tools=(), model=model)


# --- sending it back ---------------------------------------------------------------------


def test_a_turns_reasoning_goes_back_before_its_message_and_calls():
    body = build_body(_request(_turn_with()), stream=False)
    kinds = [item.get("type") for item in body["input"]]
    assert kinds == ["message", "reasoning", "message", "function_call", "function_call_output"]
    assert body["input"][1] == KEPT
    assert all("id" not in item for item in body["input"])


@pytest.mark.parametrize("message", [
    _turn_with(opaque_model="o4-mini"),                     # another model's
    _turn_with(provider="anthropic"),                       # another provider's
    _turn_with(data="not json"),
    _turn_with(data=json.dumps(["a list"])),
    _turn_with(items=({"type": "message", "content": []},)),
    _turn_with(data=json.dumps({"model": "gpt-5", "items": "ENC1"})),
])
def test_what_is_not_this_models_reasoning_is_not_sent(message):
    body = build_body(_request(message), stream=False)
    assert [item.get("type") for item in body["input"]] == [
        "message", "message", "function_call", "function_call_output"]


def test_a_users_message_carries_no_reasoning():
    user = Message(role=Role.USER, blocks=(TextBlock("hi"), OpaqueBlock(
        provider=PROVIDER, data=json.dumps({"model": "gpt-5", "items": [KEPT]}))))
    body = build_body(TurnRequest(messages=(user,), tools=(), model="gpt-5"), stream=False)
    assert [item.get("type") for item in body["input"]] == ["message"]


def test_it_is_asked_for_only_when_told_to():
    assert "include" not in build_body(request_with_history(), stream=False)
    assert build_body(request_with_history(), stream=False, include_reasoning=True)[
        "include"] == ["reasoning.encrypted_content"]


# --- keeping it --------------------------------------------------------------------------


def _backend(server, **kwargs):
    return ResponsesBackend(base_url=base_url(server), model="gpt-5", **kwargs)


def test_a_response_keeps_its_encrypted_reasoning(server):
    _Handler.script = {"json": {"model": "gpt-5", "output": [
        REASONING, {"type": "reasoning", "id": "rs_2", "summary": []},  # nothing to send back
        CALL]}}
    backend = _backend(server, stream=False)
    turn = collect_turn(iter(backend.stream_turn(request_with_history())))
    assert turn.opaque.provider == PROVIDER
    assert json.loads(turn.opaque.data) == {"model": "gpt-5", "items": [KEPT]}
    assert backend.reasons
    assert "include" not in server.last_body       # not before it had shown it reasons
    collect_turn(iter(backend.stream_turn(request_with_history())))
    assert server.last_body["include"] == ["reasoning.encrypted_content"]


def test_a_stream_keeps_its_encrypted_reasoning(server):
    _Handler.script = {"sse": [
        {"type": "response.output_item.done", "output_index": 0, "item": REASONING},
        {"type": "response.output_item.done", "output_index": 1, "item": CALL},
        {"type": "response.completed", "response": {"model": "gpt-5", "usage": {}}},
    ]}
    backend = _backend(server)
    events = list(backend.stream_turn(request_with_history()))
    turn = collect_turn(iter(events))
    assert json.loads(turn.opaque.data) == {"model": "gpt-5", "items": [KEPT]}
    assert backend.reasons
    assert [e.text for e in events if isinstance(e, ReasoningComplete)] == [
        "Reading the file first."]


@pytest.mark.parametrize("stream", [True, False])
def test_it_is_kept_for_the_model_asked_not_the_snapshot_that_answered(server, stream):
    """A provider answers "gpt-5" with a dated snapshot's name; kept under
    that name, the reasoning would never match a request again."""
    answered = {"model": "gpt-5-2025-08-07", "usage": {}, "output": [REASONING, CALL]}
    _Handler.script = ({"sse": [{"type": "response.completed", "response": answered}]}
                       if stream else {"json": answered})
    turn = collect_turn(iter(_backend(server, stream=stream).stream_turn(
        request_with_history())))
    assert json.loads(turn.opaque.data)["model"] == "gpt-5"
    replayed = build_body(_request(turn.as_message()), stream=False)
    assert KEPT in replayed["input"]


def test_a_gateway_that_sends_only_the_end_is_read_too(server):
    _Handler.script = {"sse": [
        {"type": "response.completed", "response": {"model": "gpt-5", "usage": {},
                                                    "output": [REASONING, CALL]}},
    ]}
    backend = _backend(server)
    turn = collect_turn(iter(backend.stream_turn(request_with_history())))
    assert json.loads(turn.opaque.data)["items"] == [KEPT]
    assert backend.reasons


def test_reasoning_without_encrypted_content_still_asks_next_time(server):
    _Handler.script = {"json": {"output": [{"type": "reasoning", "id": "rs_1", "summary": []},
                                           {"type": "message", "content": [
                                               {"type": "output_text", "text": "done"}]}]}}
    backend = _backend(server, stream=False)
    turn = collect_turn(iter(backend.stream_turn(request_with_history())))
    assert turn.opaque is None and backend.reasons


def test_a_model_that_does_not_reason_is_never_asked(server):
    _Handler.script = {"json": {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": "done"}]}]}}
    backend = _backend(server, stream=False)
    for _ in range(2):
        turn = collect_turn(iter(backend.stream_turn(request_with_history())))
        assert turn.opaque is None
        assert "include" not in server.last_body
    assert not backend.reasons


@pytest.mark.parametrize("setting", [{"effort": "high"}, {"reasoning_summary": "auto"}])
def test_configured_reasoning_asks_from_the_first_request(server, setting):
    _Handler.script = {"json": {"output": []}}
    collect_turn(iter(_backend(server, stream=False, **setting).stream_turn(
        request_with_history())))
    assert server.last_body["include"] == ["reasoning.encrypted_content"]


# --- a session ---------------------------------------------------------------------------


class _Rounds(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.bodies.append(json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0)))))
        payload = json.dumps(self.server.replies.pop(0)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def rounds():
    httpd = HTTPServer(("127.0.0.1", 0), _Rounds)
    httpd.bodies, httpd.replies = [], []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


DONE = {"type": "message", "content": [{"type": "output_text", "text": "add subtracts"}]}


def _wired(repo, tmp_path, rounds, **kwargs):
    host, port = rounds.server_address
    return build(repo, home=tmp_path / "home", overrides={
        "base_url": f"http://{host}:{port}/v1", "model": "gpt-5", "stream": False,
        "protocol": "responses"}, console=Console(file=io.StringIO(), width=100), **kwargs)


def test_a_tool_loop_sends_the_reasoning_back_and_a_resume_keeps_it(repo, tmp_path, rounds):
    rounds.replies = [{"model": "gpt-5", "output": [REASONING, CALL]},
                      {"model": "gpt-5", "output": [DONE]}]
    wiring = _wired(repo, tmp_path, rounds)
    wiring.runtime.run_turn("what does add do?")
    first, second = rounds.bodies
    assert "include" not in first
    assert second["include"] == ["reasoning.encrypted_content"]
    replayed = [item for item in second["input"] if item.get("type") == "reasoning"]
    assert replayed == [KEPT]
    kinds = [item.get("type") for item in second["input"]]
    assert kinds.index("reasoning") == kinds.index("function_call") - 1
    session_id = wiring.session.session_id
    wiring.session.close()

    rounds.replies = [{"model": "gpt-5", "output": [DONE]}]
    again = _wired(repo, tmp_path, rounds, resume=session_id)
    again.runtime.run_turn("and sub?")
    assert [item for item in rounds.bodies[-1]["input"]
            if item.get("type") == "reasoning"] == [KEPT]
    again.session.close()
