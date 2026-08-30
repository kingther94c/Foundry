"""Integration behaviour: what the user and the caller actually see.

These came from driving the assembled program rather than its parts. Each one
made the system quietly less useful or less honest than it looked: a refusal
nobody could see, a transient error that killed a task, a truncated answer
presented as complete.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from foundry.core.backends.base import collect_turn
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.replay import ScriptedBackend
from foundry.core.context import ContextManager
from foundry.core.conversation import (
    Message,
    ModelTurn,
    Role,
    StopReason,
    ToolUseBlock,
    TurnRequest,
    Usage,
)
from foundry.core.errors import TransientError
from foundry.core.events import EventSink, ToolRejected
from foundry.core.policy.engine import PolicyEngine, Rule, Verdict
from foundry.core.runtime import AgentRuntime
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import Workspace


def call(name: str, args: dict, call_id: str = "c1") -> ToolUseBlock:
    return ToolUseBlock(call_id=call_id, name=name, arguments=json.dumps(args))


def turn(text: str = "", *calls: ToolUseBlock) -> ModelTurn:
    return ModelTurn(text=text, tool_calls=tuple(calls), usage=Usage(1, 1),
                     stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)


@pytest.fixture()
def harness(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    def build(turns, *, policy=None, approval=None):
        events = []
        sink = EventSink()
        sink.subscribe(events.append)
        runtime = AgentRuntime(
            backend=ScriptedBackend(turns), registry=default_registry(),
            policy=policy or PolicyEngine(), context=ContextManager(),
            tool_ctx=ToolContext(workspace=Workspace(repo),
                                 artifacts=ArtifactStore(tmp_path / "a"),
                                 read_tracker=ReadTracker()),
            events=sink, approval=approval,
        )
        return runtime, events

    return build


# --- a refusal must be visible -------------------------------------------


def test_a_breaker_denial_is_reported_to_the_user(harness):
    """Refusing a destructive command is the most safety-relevant thing this
    does, and it used to produce no output at all -- only a journal line."""
    runtime, events = harness([
        turn("", call("run_command", {"command": "git reset --hard HEAD~1"})),
        turn("understood"),
    ])
    runtime.run_turn("undo my work")

    rejected = [e for e in events if isinstance(e, ToolRejected)]
    assert rejected, "the denial produced no event"
    assert "git reset --hard" in rejected[0].display
    assert rejected[0].step == 0
    assert "never permitted" in rejected[0].reason


def test_an_unknown_tool_is_reported(harness):
    runtime, events = harness([
        turn("", call("frobnicate", {})),
        turn("ok"),
    ])
    runtime.run_turn("go")
    rejected = [e for e in events if isinstance(e, ToolRejected)]
    assert rejected
    assert "frobnicate" in rejected[0].display


def test_a_policy_denial_carries_its_rule(harness):
    policy = PolicyEngine()
    policy.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.DENY,
                         rule_id="user.no_commands", reason="commands are off here"))
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -m pytest"})),
        turn("ok"),
    ], policy=policy)
    runtime.run_turn("run the tests")

    rejected = [e for e in events if isinstance(e, ToolRejected)]
    assert rejected[0].rule_id == "user.no_commands"
    assert rejected[0].reason == "commands are off here"


def test_a_headless_run_can_be_told_apart_from_a_successful_one(harness):
    """A CI job could not distinguish 'the agent did the work' from
    'everything it tried was blocked'."""
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -m pytest"})),
        turn("", call("finish", {"status": "completed", "summary": "done",
                                 "claims": []}, "c2")),
    ], approval=None)
    runtime.run_turn("run the tests")
    assert any(isinstance(e, ToolRejected) for e in events)


# --- a transient provider error must not kill the task -------------------


class _FlakyHandler(BaseHTTPRequestHandler):
    """Fails the first request, then behaves."""

    fail_status: int = 429
    seen: int = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        type(self).seen += 1
        if type(self).seen == 1:
            self.send_response(type(self).fail_status)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b'{"error": "slow down"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in ({"choices": [{"delta": {"content": "recovered"}}]},
                      {"choices": [{"delta": {}, "finish_reason": "stop"}]}):
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture()
def flaky_server():
    httpd = HTTPServer(("127.0.0.1", 0), _FlakyHandler)
    _FlakyHandler.seen = 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def a_request() -> TurnRequest:
    return TurnRequest(messages=(Message.text(Role.USER, "hi"),), tools=(), model="m")


@pytest.mark.parametrize("status", [429, 503])
def test_the_streaming_path_retries_a_transient_error(flaky_server, status):
    """retry_with_backoff was applied only to the non-streaming path, so the
    configured retries were dead by default and one 429 killed the session."""
    _FlakyHandler.fail_status = status
    _FlakyHandler.seen = 0
    host, port = flaky_server.server_address
    backend = OpenAICompatBackend(base_url=f"http://{host}:{port}/v1", model="m",
                                  stream=True, max_retries=3)
    turn_result = collect_turn(iter(backend.stream_turn(a_request())))
    assert turn_result.text == "recovered"
    assert _FlakyHandler.seen == 2, "the first attempt should have been retried"


# --- a truncated stream is not a finished turn ---------------------------


class _TruncatingHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"delta": {"content": "The fix is to change line 12 to "}}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        # No [DONE], no finish_reason: the connection simply ends.


@pytest.fixture()
def truncating_server():
    httpd = HTTPServer(("127.0.0.1", 0), _TruncatingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def test_a_truncated_stream_is_not_presented_as_the_answer(truncating_server):
    """A chunked body ending without its terminator reads as a clean EOF, so a
    cut connection was indistinguishable from a model that finished speaking --
    and the user saw a half-sentence as the complete answer."""
    host, port = truncating_server.server_address
    backend = OpenAICompatBackend(base_url=f"http://{host}:{port}/v1", model="m",
                                  stream=True, max_retries=1)
    with pytest.raises(TransientError, match="cut mid-turn"):
        collect_turn(iter(backend.stream_turn(a_request())))


# --- cancellation reaches a running child --------------------------------


def test_a_cancel_flag_stops_a_running_command(tmp_path):
    """process.wait() released the GIL for the command's whole duration, so
    Ctrl+C could not be delivered -- up to ten minutes with the max timeout."""
    import time

    from foundry.core.tools.command import run_process

    flag = {"stop": False}

    def stop_soon():
        time.sleep(1.0)
        flag["stop"] = True

    threading.Thread(target=stop_soon, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_process('python -c "import time; time.sleep(30)"',
                    cwd=str(tmp_path), timeout_s=600,
                    cancelled=lambda: flag["stop"])
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"cancel took {elapsed:.1f}s to land"
