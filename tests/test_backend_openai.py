"""Adapter contract tests against a fake HTTP server on localhost.

The protocol layer needs coverage that a scripted backend cannot give: streaming
fragment reassembly, tool-call accumulation, usage parsing, and the mapping from
HTTP status to the error taxonomy. A local server gives all of that with no
credentials and no internet.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from foundry.core.backends.base import TurnFinished, collect_turn
from foundry.core.backends.openai_compat import OpenAICompatBackend, build_body
from foundry.core.conversation import (
    Message,
    Role,
    StopReason,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    TurnRequest,
)
from foundry.core.errors import AuthError, FatalError, TransientError
from foundry.core.httpc import HttpClient, retry_with_backoff


class _Handler(BaseHTTPRequestHandler):
    script: dict = {}

    def log_message(self, *args):  # silence the test output
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.last_body = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_headers = dict(self.headers)

        status = self.script.get("status", 200)
        if status >= 400:
            self.send_response(status)
            for key, value in self.script.get("headers", {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "boom"}).encode())
            return

        if self.script.get("sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in self.script["sse"]:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return

        payload = json.dumps(self.script.get("json", {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.last_body = None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()


def base_url(server) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/v1"


def simple_request() -> TurnRequest:
    return TurnRequest(
        messages=(Message.text(Role.USER, "hi"),),
        tools=(ToolSchema(name="read_file", description="read", parameters={"type": "object"}),),
        model="test-model",
    )


# --- body construction ---------------------------------------------------


def test_body_includes_tools_and_messages():
    body = build_body(simple_request(), stream=False)
    assert body["model"] == "test-model"
    assert body["messages"][0]["role"] == "user"
    assert body["tools"][0]["function"]["name"] == "read_file"


def test_tool_results_become_tool_role_messages():
    request = TurnRequest(
        messages=(Message(role=Role.TOOL,
                          blocks=(ToolResultBlock(call_id="c1", content="output"),)),),
        tools=(), model="m",
    )
    body = build_body(request, stream=False)
    assert body["messages"][0] == {"role": "tool", "tool_call_id": "c1", "content": "output"}


def test_assistant_tool_calls_round_trip():
    request = TurnRequest(
        messages=(Message(role=Role.ASSISTANT,
                          blocks=(ToolUseBlock(call_id="c1", name="read_file",
                                               arguments='{"path":"a.py"}'),)),),
        tools=(), model="m",
    )
    body = build_body(request, stream=False)
    call = body["messages"][0]["tool_calls"][0]
    assert call["id"] == "c1"
    assert call["function"]["name"] == "read_file"


# --- non-streaming -------------------------------------------------------


def test_non_streaming_text_response(server):
    _Handler.script = {"json": {
        "model": "test-model",
        "choices": [{"message": {"content": "hello there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
    }}
    backend = OpenAICompatBackend(base_url=base_url(server), model="test-model", stream=False)
    turn = collect_turn(iter(backend.stream_turn(simple_request())))
    assert turn.text == "hello there"
    assert turn.usage.input_tokens == 11
    assert turn.stop_reason is StopReason.END_TURN


def test_non_streaming_tool_call(server):
    _Handler.script = {"json": {
        "choices": [{
            "message": {"content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}},
            ]},
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
    }}
    backend = OpenAICompatBackend(base_url=base_url(server), model="m", stream=False)
    turn = collect_turn(iter(backend.stream_turn(simple_request())))
    assert turn.stop_reason is StopReason.TOOL_USE
    assert turn.tool_calls[0].name == "read_file"
    assert json.loads(turn.tool_calls[0].arguments)["path"] == "a.py"


# --- streaming -----------------------------------------------------------


def test_streaming_reassembles_text_and_tool_calls(server):
    _Handler.script = {"sse": [
        {"model": "m", "choices": [{"delta": {"content": "Let me "}}]},
        {"choices": [{"delta": {"content": "look."}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "read_file", "arguments": '{"pa'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'th":"a.py"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 20, "completion_tokens": 9}},
    ]}
    backend = OpenAICompatBackend(base_url=base_url(server), model="m")
    events = list(backend.stream_turn(simple_request()))
    turn = [e for e in events if isinstance(e, TurnFinished)][0].turn

    assert turn.text == "Let me look."
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].call_id == "call_a"
    assert json.loads(turn.tool_calls[0].arguments) == {"path": "a.py"}
    assert turn.usage.output_tokens == 9


def test_streaming_handles_parallel_tool_calls(server):
    """The wire protocol must accept N calls in one turn even though the
    executor runs them serially."""
    _Handler.script = {"sse": [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "a", "function": {"name": "read_file", "arguments": "{}"}},
            {"index": 1, "id": "b", "function": {"name": "list_files", "arguments": "{}"}},
        ]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]}
    backend = OpenAICompatBackend(base_url=base_url(server), model="m")
    turn = collect_turn(iter(backend.stream_turn(simple_request())))
    assert [c.name for c in turn.tool_calls] == ["read_file", "list_files"]


# --- error taxonomy ------------------------------------------------------


@pytest.mark.parametrize("status,expected", [
    (401, AuthError),
    (403, AuthError),
    (407, AuthError),
    (429, TransientError),
    (500, TransientError),
    (503, TransientError),
    (400, FatalError),
])
def test_status_maps_to_taxonomy(server, status, expected):
    _Handler.script = {"status": status}
    backend = OpenAICompatBackend(base_url=base_url(server), model="m", stream=False,
                                  max_retries=1)
    with pytest.raises(expected):
        collect_turn(iter(backend.stream_turn(simple_request())))


def test_407_names_the_ntlm_limitation(server):
    _Handler.script = {"status": 407}
    client = HttpClient()
    with pytest.raises(AuthError, match="NTLM or Kerberos"):
        client.post_json(f"{base_url(server)}/chat/completions", {}, {})


def test_rate_limit_honours_retry_after(server):
    _Handler.script = {"status": 429, "headers": {"Retry-After": "0.01"}}
    client = HttpClient()
    slept: list[float] = []
    with pytest.raises(TransientError):
        retry_with_backoff(
            lambda: client.post_json(f"{base_url(server)}/chat/completions", {}, {}),
            attempts=3, sleep=slept.append,
        )
    assert slept == [0.01, 0.01]


def test_retry_stops_on_fatal(server):
    _Handler.script = {"status": 400}
    client = HttpClient()
    calls: list[int] = []

    def attempt():
        calls.append(1)
        return client.post_json(f"{base_url(server)}/chat/completions", {}, {})

    with pytest.raises(FatalError):
        retry_with_backoff(attempt, attempts=4, sleep=lambda _: None)
    assert len(calls) == 1


# --- credentials ---------------------------------------------------------


def test_api_key_is_sent_but_never_in_the_request_record(server):
    _Handler.script = {"json": {"choices": [{"message": {"content": "ok"},
                                             "finish_reason": "stop"}]}}

    class Recorder:
        def __init__(self):
            self.requests = []

        def record(self, record):
            self.requests.append(record)

    recorder = Recorder()
    backend = OpenAICompatBackend(base_url=base_url(server), model="m", stream=False,
                                  api_key="sk-canary-value-123456", recorder=recorder)
    collect_turn(iter(backend.stream_turn(simple_request())))

    assert server.last_headers["Authorization"] == "Bearer sk-canary-value-123456"
    serialized = json.dumps(recorder.requests[0].body)
    assert "sk-canary" not in serialized
    assert "Authorization" not in serialized
