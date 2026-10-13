"""Anthropic Messages adapter against recorded fixtures on a local HTTP server.

docs/research/anthropic-messages.md, sections Request, Response and
Capabilities. The fixtures in tests/fixtures/anthropic/ are the exact bytes the
live API returned for a two-round tool loop and the same first round without
streaming; they are served byte for byte so the SSE parsing, the padding inside
data lines and the absence of [DONE] are exercised for real.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from foundry.core.backends.anthropic import AnthropicBackend, build_body
from foundry.core.backends.base import (TextDelta, ToolCallComplete, TurnFinished,
                                        UsageUpdate, collect_turn)
from foundry.core.conversation import (
    Message,
    ModelTurn,
    OpaqueBlock,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    TurnRequest,
)
from foundry.core.errors import FatalError, TransientError

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"
ROUND_ONE = (FIXTURES / "stream_tool_use.sse").read_bytes()
ROUND_TWO = (FIXTURES / "stream_after_tool_result.sse").read_bytes()
NONSTREAM = (FIXTURES / "nonstream_tool_use.json").read_bytes()

THINKING_BETA = "thinking-binding-controls-2026-08-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class _Handler(BaseHTTPRequestHandler):
    # Each POST takes the next scripted reply: (content_type, body_bytes).
    replies: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.bodies.append(json.loads(self.rfile.read(length) or b"{}"))
        self.server.headers_seen.append(dict(self.headers))
        content_type, payload = self.replies.pop(0)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.bodies = []
    httpd.headers_seen = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def serve(*replies) -> None:
    _Handler.replies = list(replies)


def sse(payload: bytes) -> tuple[str, bytes]:
    return ("text/event-stream", payload)


def as_json(payload: bytes) -> tuple[str, bytes]:
    return ("application/json", payload)


def synthetic_sse(*events: dict) -> bytes:
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode("utf-8") for e in events)


def backend_for(server, **kwargs) -> AnthropicBackend:
    host, port = server.server_address
    kwargs.setdefault("max_retries", 1)
    return AnthropicBackend(base_url=f"http://{host}:{port}/v1",
                            model="claude-opus-5-5", **kwargs)


TOOLS = (ToolSchema(name="read_file", description="Read a file.",
                    parameters={"type": "object",
                                "properties": {"path": {"type": "string"}}}),)


def first_request(**kwargs) -> TurnRequest:
    return TurnRequest(
        messages=(Message.text(Role.SYSTEM, "You are terse."),
                  Message.text(Role.SYSTEM, "Read one file only."),
                  Message.text(Role.USER, "What does run() return?")),
        tools=TOOLS, model="claude-opus-5-5", **kwargs)


def finished(events) -> ModelTurn:
    return [e for e in events if isinstance(e, TurnFinished)][0].turn


def fixture_blocks(payload: bytes) -> list[dict]:
    """The content the fixture's stream assembles to, rebuilt independently."""
    blocks: dict[int, dict] = {}
    partial: dict[int, str] = {}
    for line in payload.decode("utf-8").splitlines():
        if not line.startswith("data:"):
            continue
        event = json.loads(line[5:])
        kind = event["type"]
        if kind == "content_block_start":
            blocks[event["index"]] = dict(event["content_block"])
        elif kind == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            if delta["type"] == "text_delta":
                block["text"] += delta["text"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] += delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] += delta["signature"]
            elif delta["type"] == "input_json_delta":
                partial[event["index"]] = partial.get(event["index"], "") + delta["partial_json"]
    for index, raw in partial.items():
        blocks[index]["input"] = json.loads(raw) if raw else {}
    return [blocks[i] for i in sorted(blocks)]


# --- request: headers ---------------------------------------------------------


def test_headers_carry_key_version_and_betas_never_authorization(server):
    serve(sse(ROUND_ONE))
    backend = backend_for(server, api_key="[redacted]")
    list(backend.stream_turn(first_request()))

    headers = {k.lower(): v for k, v in server.headers_seen[0].items()}
    assert headers["x-api-key"] == "[redacted]"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["anthropic-beta"] == f"{THINKING_BETA},{FALLBACK_BETA}"
    assert "authorization" not in headers


def test_fallback_beta_dropped_when_fallbacks_off(server):
    serve(sse(ROUND_ONE))
    list(backend_for(server, fallbacks=None).stream_turn(first_request()))
    headers = {k.lower(): v for k, v in server.headers_seen[0].items()}
    assert headers["anthropic-beta"] == THINKING_BETA
    assert "fallbacks" not in server.bodies[0]


def test_endpoint_and_default_base_url():
    backend = AnthropicBackend(model="claude-opus-5-5")
    assert backend.endpoint == "https://api.anthropic.com/v1/messages"


def test_capabilities():
    caps = AnthropicBackend(model="m").capabilities()
    assert caps.parallel_tool_calls is True
    assert caps.prompt_caching is True
    assert caps.max_context_tokens == 200_000


# --- request: body ------------------------------------------------------------


def test_body_shape_on_the_wire(server):
    serve(sse(ROUND_ONE))
    list(backend_for(server).stream_turn(first_request()))
    body = server.bodies[0]

    assert body["model"] == "claude-opus-5-5"
    assert body["stream"] is True
    assert body["max_tokens"] == 64000
    assert body["system"] == [{"type": "text",
                               "text": "You are terse.\n\nRead one file only."}]
    assert body["tools"] == [{"name": "read_file", "description": "Read a file.",
                              "input_schema": TOOLS[0].parameters}]
    assert body["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "What does run() return?"}]}]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["thinking"] == {"type": "adaptive", "block_binding":
                                {"prefix_mismatch_behavior": "drop_block"}}
    assert body["fallbacks"] == "default"
    for absent in ("output_config", "tool_choice", "temperature"):
        assert absent not in body


def test_effort_only_when_set():
    assert "output_config" not in build_body(first_request(), stream=True)
    body = build_body(first_request(), stream=True, effort="high")
    assert body["output_config"] == {"effort": "high"}


def test_temperature_only_when_the_request_sets_it():
    assert build_body(first_request(temperature=0.2), stream=True)["temperature"] == 0.2


def test_max_tokens_defaults_and_override():
    assert build_body(first_request(), stream=True)["max_tokens"] == 64000
    assert build_body(first_request(), stream=False)["max_tokens"] == 16000
    assert build_body(first_request(max_output_tokens=123), stream=True)["max_tokens"] == 123


def test_body_is_byte_stable_across_rounds():
    one = json.dumps(build_body(first_request(), stream=True))
    two = json.dumps(build_body(first_request(), stream=True))
    assert one == two


def test_tool_results_merge_before_text_in_one_user_message():
    call = ToolUseBlock(call_id="t1", name="read_file", arguments='{"path": "a"}')
    call2 = ToolUseBlock(call_id="t2", name="read_file", arguments='{"path": "b"}')
    request = TurnRequest(messages=(
        Message.text(Role.USER, "go"),
        Message(role=Role.ASSISTANT, blocks=(TextBlock("reading"), call, call2)),
        Message(role=Role.TOOL, blocks=(ToolResultBlock(call_id="t1", content="A"),)),
        Message(role=Role.TOOL, blocks=(ToolResultBlock(call_id="t2", content="no",
                                                        is_error=True),)),
        Message.text(Role.USER, "and then?"),
    ), tools=(), model="m")
    messages = build_body(request, stream=True)["messages"]

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == [
        {"type": "text", "text": "reading"},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}},
        {"type": "tool_use", "id": "t2", "name": "read_file", "input": {"path": "b"}},
    ]
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "A"},
        {"type": "tool_result", "tool_use_id": "t2", "content": "no", "is_error": True},
        {"type": "text", "text": "and then?"},
    ]


def test_text_before_tool_result_is_still_ordered_after_it():
    request = TurnRequest(messages=(
        Message.text(Role.USER, "note first"),
        Message(role=Role.TOOL, blocks=(ToolResultBlock(call_id="t1", content="A"),)),
    ), tools=(), model="m")
    messages = build_body(request, stream=True)["messages"]
    assert messages == [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "A"},
        {"type": "text", "text": "note first"},
    ]}]


def test_empty_assistant_message_is_left_out():
    request = TurnRequest(messages=(
        Message.text(Role.USER, "a"),
        Message(role=Role.ASSISTANT, blocks=()),
        Message.text(Role.USER, "b"),
    ), tools=(), model="m")
    messages = build_body(request, stream=True)["messages"]
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "a"},
                                                     {"type": "text", "text": "b"}]}]


def test_other_providers_opaque_blocks_are_ignored():
    request = TurnRequest(messages=(
        Message.text(Role.USER, "a"),
        Message(role=Role.ASSISTANT, blocks=(TextBlock("hi"),
                                             OpaqueBlock(provider="other", data="[1]"))),
    ), tools=(), model="m")
    messages = build_body(request, stream=True)["messages"]
    assert messages[1]["content"] == [{"type": "text", "text": "hi"}]


# --- response: the recorded tool-use round ------------------------------------


def test_round_one_streams_text_and_assembles_the_turn(server):
    serve(sse(ROUND_ONE))
    events = list(backend_for(server).stream_turn(first_request()))
    turn = finished(events)

    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    assert len(deltas) == 14
    assert "".join(deltas) == turn.text
    assert turn.text.startswith("My reasoning: `src/app.py` defines")
    assert turn.text.endswith("I'll read only that file.")

    assert turn.stop_reason is StopReason.TOOL_USE
    assert turn.model == "claude-opus-5-5"
    assert turn.tool_calls == (ToolUseBlock(call_id="toolu_01EGHAAbpCP6cLpQriKd7Pih",
                                            name="read_file",
                                            arguments='{"path": "src/util.py"}'),)
    assert [e.call for e in events if isinstance(e, ToolCallComplete)] == list(turn.tool_calls)
    assert turn.notices == ()
    assert isinstance(events[-1], TurnFinished)
    assert isinstance(events[-2], UsageUpdate)


def test_round_one_usage_counts_cache_writes(server):
    serve(sse(ROUND_ONE))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    assert turn.usage.input_tokens == 4 + 4202
    assert turn.usage.cached_input_tokens == 0
    assert turn.usage.output_tokens == 495  # from the last usage, not message_start's 2


def test_round_one_opaque_content_is_every_block_with_caller(server):
    serve(sse(ROUND_ONE))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    content = json.loads(turn.opaque.data)
    assert turn.opaque.provider == "anthropic"
    assert [b["type"] for b in content] == ["thinking", "text", "tool_use"]
    assert content == fixture_blocks(ROUND_ONE)
    assert content[2]["caller"] == {"type": "direct"}
    assert content[2]["input"] == {"path": "src/util.py"}
    assert content[0]["signature"].startswith("CAQShg4K")


def test_verbatim_replay_into_the_follow_up_round(server):
    serve(sse(ROUND_ONE), sse(ROUND_TWO))
    backend = backend_for(server)
    request = first_request()
    turn = collect_turn(backend.stream_turn(request))

    call = turn.tool_calls[0]
    history = request.messages + (
        turn.as_message(),
        Message(role=Role.TOOL, blocks=(ToolResultBlock(call_id=call.call_id,
                                                        content="def run(): return 1"),)),
    )
    second = collect_turn(backend.stream_turn(request.with_messages(history)))

    body = server.bodies[1]
    assert body["system"] == server.bodies[0]["system"]
    assert body["tools"] == server.bodies[0]["tools"]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    # Exactly the blocks the API returned: nothing added (no IR-rebuilt text
    # or tool_use), nothing removed (caller and signature intact).
    assert body["messages"][1]["content"] == fixture_blocks(ROUND_ONE)
    assert body["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_01EGHAAbpCP6cLpQriKd7Pih",
         "content": "def run(): return 1"}]

    assert second.stop_reason is StopReason.END_TURN
    assert second.tool_calls == ()
    assert second.text.startswith("I guessed wrong")
    assert [b["type"] for b in json.loads(second.opaque.data)] == ["thinking", "text"]
    assert second.usage.input_tokens == 2 + 511 + 4202
    assert second.usage.cached_input_tokens == 4202
    assert second.usage.output_tokens == 286
    assert second.notices == ()


def test_non_ascii_text_survives(server):
    serve(sse(ROUND_TWO))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    # The fixture's text contains an em dash; written as an escape (ASCII rule).
    assert "the value \u2014 and" in turn.text


# --- response: non-streaming --------------------------------------------------


def test_non_streaming_fixture(server):
    serve(as_json(NONSTREAM))
    backend = backend_for(server, stream=False)
    events = list(backend.stream_turn(first_request()))
    turn = finished(events)

    assert server.bodies[0]["stream"] is False
    assert server.bodies[0]["max_tokens"] == 16000
    assert turn.stop_reason is StopReason.TOOL_USE
    assert turn.tool_calls == (ToolUseBlock(call_id="toolu_01SYHoo7WcyoVK2AoSMaRtsM",
                                            name="read_file",
                                            arguments='{"path": "src/app.py"}'),)
    assert turn.text.startswith("I'm starting with `src/app.py`.")
    assert json.loads(turn.opaque.data) == json.loads(NONSTREAM)["content"]
    assert turn.usage.input_tokens == 4 + 0 + 4202
    assert turn.usage.cached_input_tokens == 4202
    assert turn.usage.output_tokens == 484
    assert [type(e) for e in events] == [TextDelta, ToolCallComplete, UsageUpdate,
                                         TurnFinished]


def test_json_answer_to_a_stream_request_degrades(server):
    serve(as_json(NONSTREAM), as_json(NONSTREAM), as_json(NONSTREAM))
    backend = backend_for(server)
    turn = collect_turn(backend.stream_turn(first_request()))

    assert turn.tool_calls[0].call_id == "toolu_01SYHoo7WcyoVK2AoSMaRtsM"
    assert backend.stream is False
    assert [b["stream"] for b in server.bodies] == [True, False]
    assert server.bodies[1]["max_tokens"] == 16000
    # And it stays degraded for the rest of the session.
    collect_turn(backend.stream_turn(first_request()))
    assert server.bodies[2]["stream"] is False


def test_recorder_gets_the_body_without_the_key(server):
    class Recorder:
        def __init__(self):
            self.requests = []

        def record(self, record):
            self.requests.append(record)

    recorder = Recorder()
    serve(sse(ROUND_ONE))
    backend = backend_for(server, api_key="sk-ant-canary", recorder=recorder)
    list(backend.stream_turn(first_request()))
    record = recorder.requests[0]
    assert record.body == server.bodies[0]
    assert record.endpoint.endswith("/v1/messages")
    assert "sk-ant-canary" not in json.dumps(record.body)


# --- response: synthetic streams ----------------------------------------------


def start(**message) -> dict:
    base = {"model": "claude-opus-5-5", "usage": {"input_tokens": 10,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "output_tokens": 1}, "input_transformations": []}
    base.update(message)
    return {"type": "message_start", "message": base}


def text_block(index: int, text: str) -> list[dict]:
    return [
        {"type": "content_block_start", "index": index,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": index,
         "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": index},
    ]


def tool_block(index: int, call_id: str, partial: str) -> list[dict]:
    return [
        {"type": "content_block_start", "index": index, "content_block": {
            "type": "tool_use", "id": call_id, "name": "read_file", "input": {},
            "caller": {"type": "direct"}}},
        {"type": "content_block_delta", "index": index,
         "delta": {"type": "input_json_delta", "partial_json": partial}},
        {"type": "content_block_stop", "index": index},
    ]


def end(stop_reason: str, stop_details=None, output_tokens: int = 5) -> list[dict]:
    return [
        {"type": "message_delta", "delta": {"stop_reason": stop_reason,
                                            "stop_details": stop_details},
         "usage": {"output_tokens": output_tokens}},
        {"type": "message_stop"},
    ]


@pytest.mark.parametrize("wire,expected", [
    ("end_turn", StopReason.END_TURN),
    ("stop_sequence", StopReason.END_TURN),
    ("pause_turn", StopReason.END_TURN),
    ("tool_use", StopReason.TOOL_USE),
    ("max_tokens", StopReason.MAX_TOKENS),
    ("model_context_window_exceeded", StopReason.MAX_TOKENS),
    ("refusal", StopReason.REFUSAL),
])
def test_stop_reason_mapping(server, wire, expected):
    serve(sse(synthetic_sse(start(), *text_block(0, "x"), *end(wire))))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    assert turn.stop_reason is expected


def test_usage_fields_override_individually(server):
    # message_delta only carries output_tokens; input and cache counts stay.
    serve(sse(synthetic_sse(start(usage={"input_tokens": 3,
                                         "cache_creation_input_tokens": 7,
                                         "cache_read_input_tokens": 90,
                                         "output_tokens": 1}),
                            *text_block(0, "x"), *end("end_turn", output_tokens=42))))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    assert turn.usage.input_tokens == 100
    assert turn.usage.cached_input_tokens == 90
    assert turn.usage.output_tokens == 42


@pytest.mark.parametrize("details,category", [
    (None, None),
    ({"type": "refusal", "category": "cyber"}, "cyber"),
])
def test_refusal_has_no_tool_calls_and_a_notice(server, details, category):
    serve(sse(synthetic_sse(start(), *text_block(0, "I can't help."),
                            *tool_block(1, "toolu_x", '{"path": "a"}'),
                            *end("refusal", details))))
    events = list(backend_for(server).stream_turn(first_request()))
    turn = finished(events)
    assert turn.stop_reason is StopReason.REFUSAL
    assert turn.tool_calls == ()
    assert not [e for e in events if isinstance(e, ToolCallComplete)]
    assert len(turn.notices) == 1
    assert "declined" in turn.notices[0]
    if category:
        assert category in turn.notices[0]
    else:
        assert "category" not in turn.notices[0]


def test_thinking_dropped_becomes_notices(server):
    transformations = [
        {"type": "thinking_dropped", "path": "messages[1].content[0]",
         "reason": "prefix_mismatch"},
        {"type": "something_else", "path": "messages[3]"},
    ]
    serve(sse(synthetic_sse(start(input_transformations=transformations),
                            *text_block(0, "ok"), *end("end_turn"))))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    assert len(turn.notices) == 1
    assert "messages[1].content[0]" in turn.notices[0]
    assert "prefix_mismatch" in turn.notices[0]


def test_thinking_dropped_reported_on_the_final_message_delta(server):
    """After a mid-stream fallback the API reports input_transformations again
    on the final message_delta; reading only message_start missed them."""
    dropped = {"type": "thinking_dropped", "path": "messages.3.content.0",
               "reason": "model_binding_mismatch"}
    final = end("end_turn")
    final[0]["input_transformations"] = [dropped]
    serve(sse(synthetic_sse(start(), *text_block(0, "ok"), *final)))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    assert len(turn.notices) == 1
    assert "messages.3.content.0" in turn.notices[0]
    assert "model_binding_mismatch" in turn.notices[0]


def test_thinking_dropped_in_non_streaming_json(server):
    payload = json.loads(NONSTREAM)
    payload["input_transformations"] = [{"type": "thinking_dropped",
                                         "path": "messages[1].content[0]",
                                         "reason": "prefix_mismatch"}]
    serve(as_json(json.dumps(payload).encode("utf-8")))
    turn = collect_turn(backend_for(server, stream=False).stream_turn(first_request()))
    assert len(turn.notices) == 1
    assert "prefix_mismatch" in turn.notices[0]


def test_fallback_keeps_only_declined_text_and_names_both_models(server):
    events = [
        start(),
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "sigA"}},
        {"type": "content_block_stop", "index": 0},
        *text_block(1, "Starting. "),
        *tool_block(2, "toolu_declined", '{"path": "x"}'),
        {"type": "content_block_start", "index": 3, "content_block": {
            "type": "fallback", "from": {"model": "claude-opus-5-5"},
            "to": {"model": "claude-sonnet-5"}}},
        {"type": "content_block_stop", "index": 3},
        {"type": "content_block_start", "index": 4,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 4,
         "delta": {"type": "signature_delta", "signature": "sigB"}},
        {"type": "content_block_stop", "index": 4},
        *text_block(5, "Reading it."),
        *tool_block(6, "toolu_kept", '{"path": "y"}'),
        *end("tool_use"),
    ]
    serve(sse(synthetic_sse(*events)))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))

    content = json.loads(turn.opaque.data)
    assert [b["type"] for b in content] == ["text", "thinking", "text", "tool_use"]
    assert content[1]["signature"] == "sigB"
    assert content[3]["id"] == "toolu_kept"
    assert turn.text == "Starting. Reading it."
    assert [c.call_id for c in turn.tool_calls] == ["toolu_kept"]
    assert len(turn.notices) == 1
    assert "claude-opus-5-5" in turn.notices[0]
    assert "claude-sonnet-5" in turn.notices[0]


def test_tool_use_with_no_input_gets_empty_object(server):
    serve(sse(synthetic_sse(start(), *tool_block(0, "toolu_e", ""), *end("tool_use"))))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    assert turn.tool_calls[0].arguments == "{}"
    assert json.loads(turn.opaque.data)[0]["input"] == {}


def test_unknown_blocks_and_deltas_are_kept_and_ignored(server):
    serve(sse(synthetic_sse(
        start(),
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "mystery", "payload": 1}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "mystery_delta", "x": 2}},
        {"type": "content_block_stop", "index": 0},
        *text_block(1, "ok"),
        *end("end_turn"))))
    turn = collect_turn(backend_for(server).stream_turn(first_request()))
    assert json.loads(turn.opaque.data)[0] == {"type": "mystery", "payload": 1}
    assert turn.text == "ok"


@pytest.mark.parametrize("error_type,expected", [
    ("overloaded_error", TransientError),
    ("api_error", TransientError),
    ("invalid_request_error", FatalError),
])
def test_error_event(server, error_type, expected):
    serve(sse(synthetic_sse(start(), *text_block(0, "par"),
                            {"type": "error", "error": {"type": error_type,
                                                        "message": "Overloaded now"}})))
    with pytest.raises(expected, match="Overloaded now"):
        collect_turn(backend_for(server).stream_turn(first_request()))


def test_stream_cut_before_message_stop(server):
    cut = ROUND_ONE[:ROUND_ONE.index(b"event: message_delta")]
    serve(sse(cut))
    with pytest.raises(TransientError, match="message_stop"):
        collect_turn(backend_for(server).stream_turn(first_request()))


def test_stream_with_no_events_is_transient(server):
    serve(sse(b""))
    with pytest.raises(TransientError):
        collect_turn(backend_for(server).stream_turn(first_request()))


def test_stream_without_message_stop_after_message_delta(server):
    serve(sse(synthetic_sse(start(), *text_block(0, "x"), end("end_turn")[0])))
    with pytest.raises(TransientError):
        collect_turn(backend_for(server).stream_turn(first_request()))
