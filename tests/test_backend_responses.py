"""Responses adapter contract tests.

The gateway's real behaviour is unverified until fixtures are captured from it
(OQ-6), so these tests pin the shapes the adapter claims to handle. When real
fixtures arrive they replace these payloads without touching the adapter's
interface.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from foundry.core.backends.base import collect_turn
from foundry.core.backends.responses import ResponsesBackend, build_body
from foundry.core.conversation import (
    Message,
    Role,
    StopReason,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    TurnRequest,
)
from foundry.core.errors import ProtocolError, TransientError


class _Handler(BaseHTTPRequestHandler):
    script: dict = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.last_body = json.loads(self.rfile.read(length) or b"{}")
        if self.script.get("sse") is not None:
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
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def base_url(server) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/v1"


def request_with_history() -> TurnRequest:
    return TurnRequest(
        messages=(
            Message.text(Role.SYSTEM, "you are foundry"),
            Message.text(Role.USER, "read the file"),
            Message(role=Role.ASSISTANT,
                    blocks=(ToolUseBlock(call_id="call_1", name="read_file",
                                         arguments='{"path":"a.py"}'),)),
            Message(role=Role.TOOL,
                    blocks=(ToolResultBlock(call_id="call_1", content="file body"),)),
        ),
        tools=(ToolSchema(name="read_file", description="read", parameters={"type": "object"}),),
        model="gpt-5",
    )


# --- body construction ---------------------------------------------------


def test_system_prompt_moves_to_instructions():
    body = build_body(request_with_history(), stream=False)
    assert body["instructions"] == "you are foundry"
    assert all(item.get("role") != "system" for item in body["input"])


def test_tool_call_and_output_are_sibling_items():
    body = build_body(request_with_history(), stream=False)
    kinds = [item["type"] for item in body["input"]]
    assert "function_call" in kinds
    assert "function_call_output" in kinds
    call = next(i for i in body["input"] if i["type"] == "function_call")
    output = next(i for i in body["input"] if i["type"] == "function_call_output")
    assert call["call_id"] == output["call_id"] == "call_1"


def test_requests_are_stateless():
    """store:false keeps a gateway that does not persist state equivalent."""
    body = build_body(request_with_history(), stream=False)
    assert body["store"] is False


def test_tools_use_the_flat_shape():
    body = build_body(request_with_history(), stream=False)
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["name"] == "read_file"


# --- non-streaming -------------------------------------------------------


def test_non_streaming_text(server):
    _Handler.script = {"json": {
        "model": "gpt-5",
        "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}]}],
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5", stream=False)
    turn = collect_turn(iter(backend.stream_turn(request_with_history())))
    assert turn.text == "hello"
    assert turn.usage.input_tokens == 12


def test_non_streaming_tool_call(server):
    _Handler.script = {"json": {
        "output": [{"type": "function_call", "call_id": "call_9",
                    "name": "read_file", "arguments": '{"path":"b.py"}'}],
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5", stream=False)
    turn = collect_turn(iter(backend.stream_turn(request_with_history())))
    assert turn.stop_reason is StopReason.TOOL_USE
    assert turn.tool_calls[0].call_id == "call_9"


def test_reasoning_items_are_ignored_not_fatal(server):
    _Handler.script = {"json": {
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "done"}]},
        ],
    }}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5", stream=False)
    assert collect_turn(iter(backend.stream_turn(request_with_history()))).text == "done"


# --- streaming -----------------------------------------------------------


def test_streaming_text_and_tool_call(server):
    _Handler.script = {"sse": [
        {"type": "response.output_text.delta", "delta": "Look"},
        {"type": "response.output_text.delta", "delta": "ing."},
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "function_call", "call_id": "call_2", "name": "read_file"}},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"pa'},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": 'th":"a.py"}'},
        {"type": "response.output_item.done", "output_index": 1,
         "item": {"type": "function_call", "call_id": "call_2", "name": "read_file",
                  "arguments": '{"path":"a.py"}'}},
        {"type": "response.completed",
         "response": {"model": "gpt-5", "usage": {"input_tokens": 30, "output_tokens": 8}}},
    ]}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5")
    turn = collect_turn(iter(backend.stream_turn(request_with_history())))
    assert turn.text == "Looking."
    assert json.loads(turn.tool_calls[0].arguments) == {"path": "a.py"}
    assert turn.usage.output_tokens == 8


def test_streaming_recovers_a_call_without_a_done_event(server):
    """Some gateways drop the terminal item event; the fragments still count."""
    _Handler.script = {"sse": [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "call_id": "call_3", "name": "list_files"}},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": "{}"},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 1}}},
    ]}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5")
    turn = collect_turn(iter(backend.stream_turn(request_with_history())))
    assert turn.tool_calls[0].name == "list_files"


def test_streaming_falls_back_to_terminal_output(server):
    _Handler.script = {"sse": [
        {"type": "response.completed", "response": {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }},
    ]}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5")
    assert collect_turn(iter(backend.stream_turn(request_with_history()))).text == "ok"


def test_provider_failure_becomes_a_protocol_error(server):
    _Handler.script = {"sse": [
        {"type": "response.failed", "response": {"error": {"message": "model overloaded"}}},
    ]}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5")
    with pytest.raises(ProtocolError, match="model overloaded"):
        collect_turn(iter(backend.stream_turn(request_with_history())))


def test_a_stream_without_a_completion_event_is_transient(server):
    """A cut connection is retryable, not fatal -- and accepting the partial
    content would present a truncated answer as the model's complete one."""
    _Handler.script = {"sse": []}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5", max_retries=1)
    with pytest.raises(TransientError, match="cut mid-turn"):
        collect_turn(iter(backend.stream_turn(request_with_history())))


def test_a_truncated_stream_does_not_become_a_finished_turn(server):
    """Partial text arrived, then the connection dropped: the half-sentence
    must not be handed to the runtime as the model's answer."""
    _Handler.script = {"sse": [
        {"type": "response.output_text.delta", "delta": "The fix is to change line 12 to "},
    ]}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5", max_retries=1)
    with pytest.raises(TransientError, match="cut mid-turn"):
        collect_turn(iter(backend.stream_turn(request_with_history())))
