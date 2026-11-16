"""Both OpenAI-family adapters read what a real gateway sent (D-200).

Six replies recorded from the local gateway on 2026-09-08, the first it gave
with tool calls in them, are replayed here byte for byte through the real
HTTP client. Around them are the shapes a review of the adapters against that
traffic found they could not read: a call's arguments as null or as an object,
a call listed only in the terminal event, a finished item that leaves out
what was streamed, an answer cut at the output ceiling or refused, a
completion event with nothing in it, and a tool call's index as a string or
not there at all.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from foundry.core.backends.base import (
    ReasoningComplete,
    TextDelta,
    collect_turn,
    wire_arguments,
)
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.responses import ResponsesBackend, _parse_output
from foundry.core.conversation import Message, Role, StopReason, TurnRequest
from foundry.core.errors import StreamDropped

FIXTURES = Path(__file__).parent / "fixtures" / "live_gateway"
READ_CALC = {"path": "src/calc.py"}


class _Endpoint(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        raw = self.server.raw
        self.send_response(200)
        if raw.lstrip().startswith(b"{"):
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
        else:
            self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def endpoint():
    """serve(what) -> the base URL of an endpoint that answers every request
    with ``what``: recorded bytes as they are, a dict as a JSON reply, a list
    of events as a stream that ends with [DONE]."""
    httpd = HTTPServer(("127.0.0.1", 0), _Endpoint)
    httpd.raw = b""
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def serve(what) -> str:
        if isinstance(what, bytes):
            httpd.raw = what
        elif isinstance(what, dict):
            httpd.raw = json.dumps(what).encode("utf-8")
        else:
            httpd.raw = b"".join(f"data: {json.dumps(event)}\n\n".encode("utf-8")
                                 for event in what) + b"data: [DONE]\n\n"
        return f"http://127.0.0.1:{httpd.server_address[1]}/v1"

    yield serve
    httpd.shutdown()
    httpd.server_close()


def _recorded(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _events(backend) -> list:
    request = TurnRequest(messages=(Message.text(Role.USER, "go"),), tools=(), model="m")
    return list(backend.stream_turn(request))


def _responses(url: str, *, stream: bool = True):
    return collect_turn(iter(_events(ResponsesBackend(base_url=url, model="m", stream=stream))))


def _chat(url: str, *, stream: bool = True):
    return collect_turn(iter(_events(OpenAICompatBackend(base_url=url, model="m", stream=stream))))


def _calls(turn) -> list[tuple]:
    return [(call.call_id, call.name, call.parse_arguments()) for call in turn.tool_calls]


def _call(name: str, call_id: str, arguments, **more) -> dict:
    return {"type": "function_call", "call_id": call_id, "name": name,
            "arguments": arguments, **more}


def _completed(*output, **more) -> dict:
    return {"type": "response.completed",
            "response": {"status": "completed", "output": list(output),
                         "usage": {"input_tokens": 10, "output_tokens": 5}, **more}}


# -- what the gateway sent, byte for byte -------------------------------------

def test_the_recorded_streams_are_as_the_gateway_framed_them():
    """Their worth is that they are byte-exact (.gitattributes keeps git from
    rewriting their line endings): SSE frames end with a blank line."""
    for name in ("foundry_chat_toolcall_stream.raw", "foundry_chat_followup_stream.raw",
                 "foundry_responses_toolcall_stream.raw",
                 "foundry_responses_followup_stream.raw"):
        raw = _recorded(name)
        assert b"\r" not in raw and raw.endswith(b"data: [DONE]\n\n"), name
    for name in ("foundry_chat_toolcall_nonstream.raw",
                 "foundry_responses_toolcall_nonstream.raw"):
        assert json.loads(_recorded(name)), name


def test_the_gateways_streamed_chat_tool_call_is_read(endpoint):
    turn = _chat(endpoint(_recorded("foundry_chat_toolcall_stream.raw")))

    assert _calls(turn) == [("6bdee61ab", "read_file", READ_CALC)]
    assert turn.stop_reason is StopReason.TOOL_USE and turn.text == ""
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (1816, 189)
    assert turn.model == "openclaw/foundry"


def test_the_gateways_chat_tool_call_in_one_reply_is_read(endpoint):
    turn = _chat(endpoint(_recorded("foundry_chat_toolcall_nonstream.raw")), stream=False)

    assert _calls(turn) == [("674d6ab8a", "read_file", READ_CALC)]
    assert turn.stop_reason is StopReason.TOOL_USE
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (1820, 88)


def test_the_gateways_chat_answer_after_a_tool_result_is_read(endpoint):
    turn = _chat(endpoint(_recorded("foundry_chat_followup_stream.raw")))

    assert turn.text == "Line 3 of `src/calc.py` is:\n\n```python\nreturn a - b\n```"
    assert turn.tool_calls == () and turn.stop_reason is StopReason.END_TURN
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (1888, 332)


@pytest.mark.parametrize("name, call_id, tokens", [
    ("foundry_responses_toolcall_stream.raw", "541a5c033", (1814, 247)),
    ("foundry_responses_followup_stream.raw", None, (1862, 326)),
])
def test_the_gateways_streamed_responses_tool_call_is_one_call(endpoint, name, call_id, tokens):
    """The call arrives whole in output_item.added, again in output_item.done
    and again in the terminal event's output, whose status is "incomplete"
    though nothing was cut short."""
    raw = _recorded(name)
    assert raw.count(b'"type":"function_call"') == 3 and b'"status":"incomplete"' in raw

    turn = _responses(endpoint(raw))

    assert [(c.name, c.parse_arguments()) for c in turn.tool_calls] == [("read_file", READ_CALC)]
    assert call_id is None or turn.tool_calls[0].call_id == call_id
    assert turn.stop_reason is StopReason.TOOL_USE and turn.text == ""
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == tokens


def test_the_gateways_responses_tool_call_in_one_reply_is_read(endpoint):
    raw = _recorded("foundry_responses_toolcall_nonstream.raw")
    assert json.loads(raw)["status"] == "incomplete"

    turn = _responses(endpoint(raw), stream=False)

    assert _calls(turn) == [("80bb071ce", "read_file", READ_CALC)]
    assert turn.stop_reason is StopReason.TOOL_USE
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (1818, 210)


# -- a call's arguments --------------------------------------------------------

@pytest.mark.parametrize("sent, text", [
    (None, "{}"), ("", "{}"), ('{"path": "a.py"}', '{"path": "a.py"}'),
    ({"path": "a.py"}, '{"path": "a.py"}'), ({}, "{}"), ([1], "[1]"), (7, "7"),
])
def test_arguments_reach_the_tool_layer_as_json_text(sent, text):
    assert wire_arguments(sent) == text


def test_null_arguments_are_a_call_with_none_on_responses(endpoint):
    """They were None in the turn, and the next thing to measure the
    conversation raised a TypeError: a traceback, not a backend error."""
    item = _call("list_files", "c1", None)

    streamed = _responses(endpoint([
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        _completed(model=None)]))
    single = _responses(endpoint({"status": "completed", "output": [item], "model": None}),
                        stream=False)

    assert _calls(streamed) == _calls(single) == [("c1", "list_files", {})]
    assert streamed.model == single.model == "m", "a null model is the one asked for"
    assert _parse_output([item, None, {"type": "message", "content": [
        {"type": "output_text", "text": None}, "stray"]}]) == ("", streamed.tool_calls)


def test_arguments_sent_as_an_object_are_read_on_both_adapters(endpoint):
    responses = _responses(endpoint({"status": "completed", "output": [
        _call("read_file", "c1", {"path": "a.py"})]}), stream=False)
    chat = _chat(endpoint({"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": {"path": "a.py"}}},
            "stray"]}}]}), stream=False)
    streamed = _chat(endpoint([{"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "read_file",
                                             "arguments": {"path": "a.py"}}},
        "stray"]}}]}]))

    assert _calls(responses) == _calls(chat) == _calls(streamed) == [
        ("c1", "read_file", {"path": "a.py"})]


# -- what the terminal event lists --------------------------------------------

def test_a_call_listed_only_in_the_terminal_event_survives_streamed_text(endpoint):
    """The list was read only when nothing at all had been streamed, so a
    preamble dropped the call: the turn ended on "Let me look", no tool ran,
    and nothing said so."""
    turn = _responses(endpoint([
        {"type": "response.output_text.delta", "delta": "Let me look at that file."},
        _completed({"type": "message", "content": [
            {"type": "output_text", "text": "Let me look at that file."}]},
            _call("read_file", "c9", '{"path": "a.py"}'))]))

    assert turn.text == "Let me look at that file."
    assert _calls(turn) == [("c9", "read_file", {"path": "a.py"})]
    assert turn.stop_reason is StopReason.TOOL_USE


def test_text_only_in_the_terminal_event_is_shown_once(endpoint):
    events = _events(ResponsesBackend(base_url=endpoint([
        _completed({"type": "message", "content": [{"type": "output_text", "text": "OK"}]})]),
        model="m"))

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["OK"]
    assert collect_turn(iter(events)).text == "OK"


def test_a_call_that_was_streamed_is_not_taken_again_from_the_list(endpoint):
    done = _call("read_file", "c1", '{"path": "a.py"}')

    turn = _responses(endpoint([
        {"type": "response.output_item.done", "output_index": 0, "item": done},
        _completed(done)]))

    assert _calls(turn) == [("c1", "read_file", {"path": "a.py"})]


def test_a_call_the_stream_did_not_carry_is_added_to_those_it_did(endpoint):
    first = _call("read_file", "c1", '{"path": "a.py"}')
    second = _call("read_file", "c2", '{"path": "b.py"}')

    turn = _responses(endpoint([
        {"type": "response.output_item.done", "output_index": 0, "item": first},
        _completed(first, second)]))

    assert [c.call_id for c in turn.tool_calls] == ["c1", "c2"]
    assert turn.tool_calls[1].parse_arguments() == {"path": "b.py"}


def test_a_call_begun_in_the_stream_and_finished_only_in_the_list_is_one_call(endpoint):
    """No output_item.done for it: what the list says is the whole of it, and
    where the list has no arguments the pieces that were streamed stand."""
    begun = {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "function_call", "call_id": "c1", "name": "run_command"}}
    piece = {"type": "response.function_call_arguments.delta", "output_index": 0,
             "delta": '{"command": "py'}

    whole = _responses(endpoint([begun, piece, _completed(
        _call("run_command", "c1", '{"command": "pytest -q"}'))]))
    bare = _responses(endpoint([
        begun, {**piece, "delta": '{"command": "pytest"}'},
        _completed(_call("", "c1", None))]))

    assert _calls(whole) == [("c1", "run_command", {"command": "pytest -q"})]
    assert _calls(bare) == [("c1", "run_command", {"command": "pytest"})]


def test_calls_with_no_id_are_taken_from_the_list_only_when_none_were_streamed(endpoint):
    nameless = _call("read_file", "", '{"path": "a.py"}')

    only_listed = _responses(endpoint([_completed(nameless, nameless)]))
    also_streamed = _responses(endpoint([
        {"type": "response.output_item.done", "output_index": 0, "item": nameless},
        _completed(nameless)]))
    # Nothing tells it from the call that was streamed under an id.
    beside_one_with_an_id = _responses(endpoint([
        {"type": "response.output_item.done", "output_index": 0,
         "item": _call("read_file", "c1", '{"path": "a.py"}')},
        _completed(_call("read_file", "c1", '{"path": "a.py"}'), nameless)]))

    assert len(only_listed.tool_calls) == 2 and len(also_streamed.tool_calls) == 1
    assert [c.call_id for c in beside_one_with_an_id.tool_calls] == ["c1"]


# -- a finished item that leaves out what was streamed ------------------------

def test_a_done_item_with_no_arguments_keeps_what_was_streamed(endpoint):
    """The pieces were dropped and the absent key read as {}: a command
    arrived with no command."""
    turn = _responses(endpoint([
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "call_id": "c1", "name": "run_command"}},
        {"type": "response.function_call_arguments.delta", "output_index": 0,
         "delta": '{"command":'},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": None},
        {"type": "response.function_call_arguments.delta", "output_index": 0,
         "delta": ' "pytest -q"}'},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "function_call"}},
        _completed()]))

    assert _calls(turn) == [("c1", "run_command", {"command": "pytest -q"})]


def test_the_whole_of_the_arguments_replaces_their_pieces(endpoint):
    turn = _responses(endpoint([
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "call_id": "c1", "name": "run_command"}},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"comm'},
        {"type": "response.function_call_arguments.done", "output_index": 0,
         "arguments": '{"command": "pytest"}'},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "function_call", "call_id": "c1", "name": "run_command"}},
        _completed()]))

    assert _calls(turn) == [("c1", "run_command", {"command": "pytest"})]


def test_arguments_given_when_the_item_is_added_are_kept(endpoint):
    """The local gateway sends them there, whole, and no delta. With a done
    event that leaves them out, or none, they are all there is."""
    added = {"type": "response.output_item.added", "output_index": 0,
             "item": _call("read_file", "c1", '{"path": "a.py"}')}

    bare_done = _responses(endpoint([
        added, {"type": "response.output_item.done", "output_index": 0,
                "item": {"type": "function_call", "call_id": "c1"}},
        _completed()]))
    no_done = _responses(endpoint([added, _completed()]))

    assert _calls(bare_done) == _calls(no_done) == [("c1", "read_file", {"path": "a.py"})]


# -- why a turn ended -----------------------------------------------------------

@pytest.mark.parametrize("kind, response, calls, expected", [
    ("response.incomplete", {"status": "incomplete",
                             "incomplete_details": {"reason": "max_output_tokens"}},
     False, StopReason.MAX_TOKENS),
    ("response.incomplete", {"status": "incomplete"}, False, StopReason.MAX_TOKENS),
    ("response.incomplete", {"status": "incomplete",
                             "incomplete_details": {"reason": "content_filter"}},
     False, StopReason.REFUSAL),
    ("response.completed", {"status": "incomplete",
                            "incomplete_details": {"reason": "content_filter"}},
     True, StopReason.REFUSAL),
    # Cut while it was writing a call: the call is there, and so is the reason.
    ("response.incomplete", {"status": "incomplete",
                             "incomplete_details": {"reason": "max_output_tokens"}},
     True, StopReason.MAX_TOKENS),
    ("response.incomplete", {"status": "incomplete"}, True, StopReason.MAX_TOKENS),
    ("response.completed", {"status": "incomplete",
                            "incomplete_details": {"reason": "max_output_tokens"}},
     True, StopReason.MAX_TOKENS),
    # The local gateway's tool-call turn: "incomplete", and no reason given.
    ("response.completed", {"status": "incomplete"}, True, StopReason.TOOL_USE),
    ("response.completed", {"status": "incomplete", "incomplete_details": None},
     True, StopReason.TOOL_USE),
    ("response.completed", {"status": "incomplete"}, False, StopReason.MAX_TOKENS),
    ("response.completed", {"status": "completed"}, True, StopReason.TOOL_USE),
    ("response.completed", {"status": "completed"}, False, StopReason.END_TURN),
    ("response.completed", {}, False, StopReason.END_TURN),
])
def test_a_responses_turn_says_why_it_ended(endpoint, kind, response, calls, expected):
    """A reply cut at the output ceiling, or refused, used to be a finished
    turn like any other: "the fix is to change line 12 to" was the answer."""
    output = [{"type": "message", "content": [
        {"type": "output_text", "text": "the fix is to change line 12 to"}]}]
    if calls:
        output.append(_call("read_file", "c1", '{"path": "a.py"}'))
    payload = {**response, "output": output, "usage": {"input_tokens": 9, "output_tokens": 4}}

    streamed = _responses(endpoint([{"type": kind, "response": payload}]))

    assert streamed.stop_reason is expected
    assert streamed.text == "the fix is to change line 12 to"
    if kind == "response.completed":
        assert _responses(endpoint(payload), stream=False).stop_reason is expected


# -- a completion event with nothing in it -------------------------------------

def test_a_completion_event_with_nothing_in_it_has_the_turn_asked_again(endpoint):
    """A gateway that opens the stream, fails inside and closes it tidily: it
    was a finished turn with an empty answer and no tokens."""
    empty = {"type": "response.completed", "response": {"status": "completed", "output": []}}

    with pytest.raises(StreamDropped, match="carried nothing before its completion event"):
        _responses(endpoint([{"type": "response.created", "response": {}}, empty]))


@pytest.mark.parametrize("usage", [
    {"input_tokens": 10, "output_tokens": 0}, {"input_tokens": 0, "output_tokens": 5},
], ids=["read", "wrote"])
def test_an_empty_answer_that_cost_tokens_is_still_an_answer(endpoint, usage):
    turn = _responses(endpoint([_completed(usage=usage)]))

    assert turn.text == "" and turn.tool_calls == ()
    assert turn.stop_reason is StopReason.END_TURN
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == tuple(usage.values())


def test_an_answer_with_no_usage_reported_is_still_an_answer(endpoint):
    turn = _responses(endpoint([
        {"type": "response.output_text.delta", "delta": "OK"},
        {"type": "response.completed", "response": {"status": "completed", "output": []}}]))

    assert turn.text == "OK" and turn.stop_reason is StopReason.END_TURN


@pytest.mark.parametrize("item", [
    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Thinking it over."}]},
    {"type": "reasoning", "summary": [], "encrypted_content": "opaque"},
], ids=["shown", "kept"])
def test_a_turn_that_only_reasoned_is_not_asked_again(endpoint, item):
    turn = _responses(endpoint([
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": {"status": "completed", "output": []}}]))

    assert turn.text == "" and turn.stop_reason is StopReason.END_TURN


def test_reasoning_listed_only_in_the_terminal_event_is_shown_and_is_an_answer(endpoint):
    item = {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Thinking it over."}]}

    events = _events(ResponsesBackend(base_url=endpoint([
        {"type": "response.completed", "response": {"status": "completed", "output": [item]}}]),
        model="m"))

    assert [e.text for e in events if isinstance(e, ReasoningComplete)] == ["Thinking it over."]
    assert collect_turn(iter(events)).stop_reason is StopReason.END_TURN


def test_an_answer_cut_before_its_first_word_is_not_asked_again(endpoint):
    turn = _responses(endpoint([{"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "output": []}}]))

    assert turn.stop_reason is StopReason.MAX_TOKENS and turn.text == ""


# -- a tool call's index, over Chat Completions ---------------------------------

def _fragment(index, call_id=None, name=None, arguments=None) -> dict:
    function = {key: value for key, value in (("name", name), ("arguments", arguments))
                if value is not None}
    fragment = {"function": function}
    if index is not ...:
        fragment["index"] = index
    if call_id is not None:
        fragment["id"] = call_id
    return {"choices": [{"delta": {"tool_calls": [fragment]}}]}


def test_an_index_quoted_on_some_fragments_does_not_end_the_session(endpoint):
    """Sorting 0 with "1" raised a TypeError: a traceback, not a backend
    error."""
    turn = _chat(endpoint([_fragment(0, "a", "alpha", "{}"), _fragment("1", "b", "beta", "{}")]))

    assert [c.name for c in turn.tool_calls] == ["alpha", "beta"]


def test_one_call_whose_index_is_quoted_part_way_stays_one_call(endpoint):
    """0 and "0" were two slots: one call became a name with no arguments and
    arguments with no name."""
    turn = _chat(endpoint([_fragment(0, "a", "run_command", '{"comm'),
                           _fragment("0", arguments='and": "pytest"}')]))

    assert _calls(turn) == [("a", "run_command", {"command": "pytest"})]


def test_twelve_calls_stay_in_the_order_of_their_numbers(endpoint):
    """As text, "10" would come before "2"."""
    turn = _chat(endpoint([_fragment(str(i) if i % 2 else i, f"c{i}", f"t{i}", "{}")
                           for i in range(12)]))

    assert [c.name for c in turn.tool_calls] == [f"t{i}" for i in range(12)]


def test_an_index_that_is_no_number_is_a_call_of_its_own(endpoint):
    """It is kept apart, and after the numbered ones, whatever order it came in."""
    turn = _chat(endpoint([_fragment("x", "b", "beta", "{}"), _fragment(0, "a", "alpha", "{}"),
                           _fragment("x", arguments=" "), _fragment(1.5, "c", "gamma", "{}")]))

    assert [(c.name, c.arguments) for c in turn.tool_calls] == [
        ("alpha", "{}"), ("gamma", "{}"), ("beta", "{} ")]


def test_calls_sent_with_no_index_are_told_apart_by_their_ids(endpoint):
    """Each landed in the first call: one name, and every call's arguments
    run together."""
    turn = _chat(endpoint([
        _fragment(..., "a", "read_file", '{"path": "a.py"}'),
        _fragment(..., "b", "read_file", '{"path": '),
        _fragment(..., arguments='"b.py"}'),          # more of the latest
        _fragment(..., "a"),                          # an id seen before: that call
    ]))

    assert _calls(turn) == [("a", "read_file", {"path": "a.py"}),
                            ("b", "read_file", {"path": "b.py"})]


def test_one_call_sent_with_no_index_and_no_id_is_still_one_call(endpoint):
    turn = _chat(endpoint([_fragment(..., None, "read_file", '{"path": '),
                           _fragment(..., arguments='"a.py"}')]))

    assert _calls(turn) == [("call_0", "read_file", {"path": "a.py"})]
