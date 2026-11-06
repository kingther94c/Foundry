"""The model's reasoning, shown where the provider gives it (D-044).

Each adapter turns what its provider sends -- an Anthropic thinking summary, a
Responses reasoning summary, the raw reasoning some Chat Completions servers
stream -- into one whole ``ReasoningComplete`` before the answer it led to. The
runtime shows it as a ``Reasoning`` event and journals it; it never becomes part
of the conversation, it is scrubbed like everything else, and a compaction's
reasoning is not shown.
"""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.render import REASONING_LINES, Renderer
from foundry.core.backends.anthropic import AnthropicBackend, build_body as anthropic_body
from foundry.core.backends.base import (
    ReasoningComplete,
    TextDelta,
    ToolCallComplete,
    TurnFinished,
    UsageUpdate,
)
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.responses import ResponsesBackend, build_body as responses_body
from foundry.core.config import load_config, parse_settings
from foundry.core.conversation import (
    Capabilities,
    Message,
    ModelTurn,
    Role,
    StopReason,
    TurnRequest,
    Usage,
)
from foundry.core.errors import ConfigError
from foundry.core.events import MessageDelta, Reasoning
from foundry.core.redaction import Redactor
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore
from test_resume import _core, repo  # noqa: F401  (fixture)

REQUEST = TurnRequest(messages=(Message.text(Role.SYSTEM, "terse"),
                                Message.text(Role.USER, "what does run() return?")),
                      tools=(), model="m")


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Client:
    """Stands in for HttpClient: the events a stream carries, or one body."""

    def __init__(self, events=(), payload=None):
        self.events = list(events)
        self.payload = payload
        self.bodies = []

    def stream_sse(self, url, body, headers, *, expect_done_sentinel=True):
        self.bodies.append(body)
        return iter(self.events)

    def post_json(self, url, body, headers):
        self.bodies.append(body)
        return _Response(self.payload)


def _kinds(events):
    return [type(e).__name__ for e in events]


def _reasoning(events):
    return [e.text for e in events if isinstance(e, ReasoningComplete)]


def _turn(events) -> ModelTurn:
    return [e for e in events if isinstance(e, TurnFinished)][0].turn


# --- Anthropic ---------------------------------------------------------------------


def _anthropic_stream(thinking=("Plan: read ", "a.py first."), text="It returns 0."):
    events = [{"type": "message_start", "message": {"model": "claude-opus-5-5",
                                                    "usage": {"input_tokens": 9}}},
              {"type": "content_block_start", "index": 0,
               "content_block": {"type": "thinking", "thinking": "", "signature": ""}}]
    events += [{"type": "content_block_delta", "index": 0,
                "delta": {"type": "thinking_delta", "thinking": piece}} for piece in thinking]
    events += [{"type": "content_block_delta", "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig"}},
               {"type": "content_block_stop", "index": 0},
               {"type": "content_block_start", "index": 1,
                "content_block": {"type": "text", "text": ""}},
               {"type": "content_block_delta", "index": 1,
                "delta": {"type": "text_delta", "text": text}},
               {"type": "content_block_stop", "index": 1},
               {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 4}},
               {"type": "message_stop"}]
    return events


def test_anthropic_asks_for_the_summary_and_can_be_told_not_to():
    assert anthropic_body(REQUEST, stream=True)["thinking"]["display"] == "summarized"
    assert "display" not in anthropic_body(REQUEST, stream=True,
                                           thinking_display=None)["thinking"]
    client = _Client(_anthropic_stream())
    list(AnthropicBackend(model="m", client=client, thinking_display=None)
         .stream_turn(REQUEST))
    assert "display" not in client.bodies[0]["thinking"]


def test_anthropic_shows_a_thinking_block_when_it_ends_before_the_answer():
    events = list(AnthropicBackend(model="m", client=_Client(_anthropic_stream()))
                  .stream_turn(REQUEST))
    assert _kinds(events) == ["ReasoningComplete", "TextDelta", "UsageUpdate", "TurnFinished"]
    assert _reasoning(events) == ["Plan: read a.py first."]
    turn = _turn(events)
    assert turn.text == "It returns 0."
    # The block replayed next round is the one the API sent, signature and all.
    blocks = json.loads(turn.opaque.data)
    assert blocks[0] == {"type": "thinking", "thinking": "Plan: read a.py first.",
                         "signature": "sig"}


def test_anthropic_shows_nothing_for_omitted_or_redacted_thinking():
    omitted = _anthropic_stream(thinking=())
    assert _reasoning(AnthropicBackend(model="m", client=_Client(omitted))
                      .stream_turn(REQUEST)) == []
    redacted = [e for e in _anthropic_stream(thinking=()) if e.get("index") != 0]
    redacted.insert(1, {"type": "content_block_start", "index": 0,
                        "content_block": {"type": "redacted_thinking", "data": "xyz"}})
    redacted.insert(2, {"type": "content_block_stop", "index": 0})
    assert _reasoning(AnthropicBackend(model="m", client=_Client(redacted))
                      .stream_turn(REQUEST)) == []


def test_anthropic_without_streaming_shows_the_thinking_first():
    payload = {"model": "m", "stop_reason": "end_turn", "usage": {},
               "content": [{"type": "thinking", "thinking": "  Check a.py.  ",
                            "signature": "s"},
                           {"type": "text", "text": "0"}]}
    events = list(AnthropicBackend(model="m", client=_Client(payload=payload), stream=False)
                  .stream_turn(REQUEST))
    assert _kinds(events)[:2] == ["ReasoningComplete", "TextDelta"]
    assert _reasoning(events) == ["Check a.py."]


# --- Chat Completions ------------------------------------------------------------


def _chunk(**delta):
    return {"choices": [{"delta": delta}]}


def _compat(chunks, **kwargs):
    stop = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    backend = OpenAICompatBackend(base_url="http://x/v1", model="m",
                                  client=_Client([*chunks, stop]), **kwargs)
    return list(backend.stream_turn(REQUEST))


@pytest.mark.parametrize("key", ["reasoning_content", "reasoning"])
def test_compat_reasoning_is_shown_whole_once_the_answer_starts(key):
    events = _compat([_chunk(**{key: "Think "}), _chunk(**{key: "hard."}),
                      _chunk(content="It "), _chunk(content="returns 0.")])
    assert _kinds(events) == ["ReasoningComplete", "TextDelta", "TextDelta",
                              "UsageUpdate", "TurnFinished"]
    assert _reasoning(events) == ["Think hard."]
    assert _turn(events).text == "It returns 0."


def test_compat_reasoning_before_a_tool_call_comes_before_it():
    call = {"index": 0, "id": "c1", "function": {"name": "read_file",
                                                 "arguments": '{"path": "a"}'}}
    events = _compat([_chunk(reasoning_content="Need the file."), _chunk(tool_calls=[call])])
    assert _kinds(events) == ["ReasoningComplete", "ToolCallComplete", "UsageUpdate",
                              "TurnFinished"]


def test_compat_reasoning_alone_is_shown_at_the_end_and_odd_shapes_are_ignored():
    events = _compat([_chunk(reasoning_content="Only thought."),
                      _chunk(reasoning={"tokens": 12}), _chunk(reasoning_content="")])
    assert _reasoning(events) == ["Only thought."]
    assert _turn(events).text == ""
    assert _reasoning(_compat([_chunk(reasoning_content="   "), _chunk(content="x")])) == []


def test_compat_without_streaming_and_reasoning_never_sent_back():
    payload = {"model": "m", "choices": [{"finish_reason": "stop", "message": {
        "content": "0", "reasoning_content": "Look at run()."}}]}
    client = _Client(payload=payload)
    events = list(OpenAICompatBackend(base_url="http://x/v1", model="m", client=client,
                                      stream=False).stream_turn(REQUEST))
    assert _kinds(events)[:2] == ["ReasoningComplete", "TextDelta"]
    assert "reasoning" not in json.dumps(client.bodies[0])


# --- Responses ------------------------------------------------------------------


REASONING_ITEM = {"type": "reasoning", "id": "rs_1",
                  "summary": [{"type": "summary_text", "text": "Look first."},
                              {"type": "summary_text", "text": " Then answer. "}]}
MESSAGE_ITEM = {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "OK"}]}


def _responses(events, **kwargs):
    client = _Client(events)
    backend = ResponsesBackend(base_url="http://x/v1", model="m", client=client, **kwargs)
    return list(backend.stream_turn(REQUEST)), client


def test_responses_asks_for_a_summary_only_when_configured():
    assert "reasoning" not in responses_body(REQUEST, stream=True)
    assert responses_body(REQUEST, stream=True, reasoning_summary="auto")["reasoning"] == {
        "summary": "auto"}
    _events, client = _responses([{"type": "response.completed", "response": {}}],
                                 reasoning_summary="detailed")
    assert client.bodies[0]["reasoning"] == {"summary": "detailed"}


def test_responses_shows_a_reasoning_item_when_it_is_done():
    events, _ = _responses([
        {"type": "response.output_item.added", "output_index": 0, "item": REASONING_ITEM},
        {"type": "response.output_item.done", "output_index": 0, "item": REASONING_ITEM},
        {"type": "response.output_text.delta", "delta": "OK"},
        {"type": "response.completed", "response": {"usage": {}}},
    ])
    assert _kinds(events) == ["ReasoningComplete", "TextDelta", "UsageUpdate", "TurnFinished"]
    assert _reasoning(events) == ["Look first.\n\nThen answer."]


def test_responses_falls_back_to_reasoning_text_and_shows_nothing_for_an_empty_item():
    raw = {"type": "reasoning", "summary": [],
           "content": [{"type": "reasoning_text", "text": "raw thought"}]}
    empty = {"type": "reasoning", "summary": [], "encrypted_content": "zzz"}
    events, _ = _responses([
        {"type": "response.output_item.done", "item": raw},
        {"type": "response.output_item.done", "item": empty},
        {"type": "response.output_text.delta", "delta": "OK"},
        {"type": "response.completed", "response": {}},
    ])
    assert _reasoning(events) == ["raw thought"]


def test_responses_terminal_only_output_shows_its_reasoning_once():
    terminal = {"type": "response.completed",
                "response": {"output": [REASONING_ITEM, MESSAGE_ITEM]}}
    events, _ = _responses([terminal])
    assert _kinds(events) == ["ReasoningComplete", "TextDelta", "UsageUpdate", "TurnFinished"]
    assert _turn(events).text == "OK"
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["OK"]
    # Already shown from its done event: not again from the terminal output.
    events, _ = _responses([{"type": "response.output_item.done", "item": REASONING_ITEM},
                            terminal])
    assert len(_reasoning(events)) == 1


def test_responses_without_streaming_shows_the_reasoning_first():
    payload = {"model": "m", "output": [REASONING_ITEM, MESSAGE_ITEM]}
    backend = ResponsesBackend(base_url="http://x/v1", model="m",
                               client=_Client(payload=payload), stream=False)
    events = list(backend.stream_turn(REQUEST))
    assert _kinds(events)[:2] == ["ReasoningComplete", "TextDelta"]


# --- the runtime --------------------------------------------------------------------


class _Thinker:
    """A backend whose every turn reasons, then answers."""

    def __init__(self, reasoning="Read run() before answering.", text="It returns 0."):
        self.reasoning = reasoning
        self.text = text

    def capabilities(self):
        return Capabilities()

    def stream_turn(self, request):
        turn = ModelTurn(text=self.text, usage=Usage(5, 2), stop_reason=StopReason.END_TURN)
        return iter([ReasoningComplete(self.reasoning), TextDelta(self.text),
                     UsageUpdate(turn.usage), TurnFinished(turn)])


def _thinking_runtime(tmp_path, *, redactor=None, **backend):
    runtime, session, _repo = _core(tmp_path, [], redactor=redactor)
    runtime.backend = _Thinker(**backend)
    runtime.events.redactor = redactor
    events = []
    runtime.events.subscribe(events.append)
    return runtime, session, events


def _responses_logged(session):
    return [r.payload for r in SessionStore.read_records(session.path)
            if r.type == EventType.MODEL_RESPONSE]


def test_the_runtime_shows_reasoning_before_the_answer_and_journals_it(tmp_path):
    runtime, session, events = _thinking_runtime(tmp_path)
    runtime.run_turn("what does run() return?")
    shown = [e for e in events if isinstance(e, (Reasoning, MessageDelta))]
    assert isinstance(shown[0], Reasoning) and shown[0].text == "Read run() before answering."
    assert isinstance(shown[1], MessageDelta)
    assert _responses_logged(session)[0]["reasoning"] == ["Read run() before answering."]
    # Never part of the conversation the model is sent again.
    assert "Read run()" not in json.dumps([m.text_content for m in runtime.context.project()])


def test_show_reasoning_off_hides_it_but_the_journal_keeps_it(tmp_path):
    runtime, session, events = _thinking_runtime(tmp_path)
    runtime.show_reasoning = False
    runtime.run_turn("go")
    assert not [e for e in events if isinstance(e, Reasoning)]
    assert _responses_logged(session)[0]["reasoning"] == ["Read run() before answering."]


def test_a_compactions_reasoning_is_journaled_but_not_shown(tmp_path):
    runtime, session, events = _thinking_runtime(tmp_path)
    runtime._sample(purpose="compact")
    assert not [e for e in events if isinstance(e, (Reasoning, MessageDelta))]
    assert _responses_logged(session)[0]["reasoning"] == ["Read run() before answering."]


def test_an_empty_reasoning_is_neither_shown_nor_journaled(tmp_path):
    runtime, session, events = _thinking_runtime(tmp_path, reasoning="")
    runtime.run_turn("go")
    assert not [e for e in events if isinstance(e, Reasoning)]
    assert "reasoning" not in _responses_logged(session)[0]


def test_a_secret_in_the_reasoning_is_scrubbed_on_screen_and_in_the_journal(tmp_path):
    secret = "sk-test-" + "q" * 40
    redactor = Redactor()
    redactor.register(secret)
    runtime, session, events = _thinking_runtime(
        tmp_path, redactor=redactor, reasoning=f"The key is {secret}; do not echo it.")
    runtime.run_turn("go")
    shown = [e.text for e in events if isinstance(e, Reasoning)]
    assert shown and secret not in shown[0] and "[redacted]" in shown[0]
    assert secret not in session.path.read_text(encoding="utf-8")


def test_a_journal_with_reasoning_resumes_without_it(tmp_path):
    runtime, session, _events = _thinking_runtime(tmp_path)
    runtime.run_turn("go")
    state = read_journal(session.path)
    assert [m.text_content for m in state.history] == ["go", "It returns 0."]


# --- the screen ---------------------------------------------------------------------


def _screen(event, *, verbose=False) -> str:
    out = io.StringIO()
    Renderer(console=Console(file=out, width=200), verbose=verbose).handle(event)
    return out.getvalue()


def test_reasoning_is_shown_dim_and_escaped():
    shown = _screen(Reasoning("Check [bold]this[/bold]\x1b]52;c;aGk=\x07 first."))
    assert shown.splitlines()[0] == "thinking"
    assert "  Check [bold]this[/bold] first." in shown


def test_long_reasoning_is_cut_unless_verbose():
    text = "\n".join(f"step {n}" for n in range(REASONING_LINES + 4))
    short = _screen(Reasoning(text))
    assert f"step {REASONING_LINES - 1}" in short and f"step {REASONING_LINES}" not in short
    assert "... 4 more lines" in short
    full = _screen(Reasoning(text), verbose=True)
    assert f"step {REASONING_LINES + 3}" in full and "more line" not in full
    assert "... 1 more line\n" in _screen(Reasoning("\n".join(["x"] * (REASONING_LINES + 1))))
    assert _screen(Reasoning("  \n ")) == ""


def test_exec_json_carries_reasoning():
    assert app._event_payload(Reasoning("why")) == {"kind": "reasoning", "text": "why"}


# --- configuration ------------------------------------------------------------------


def _home(tmp_path, text=""):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return home


def test_the_settings_are_checked(tmp_path):
    with pytest.raises(ConfigError, match="reasoning_summary must be one of"):
        load_config(home=_home(tmp_path, '[backend]\nreasoning_summary = "loud"\n'))
    with pytest.raises(ConfigError, match="show_reasoning must be true or false"):
        load_config(home=_home(tmp_path, '[runtime]\nshow_reasoning = "no"\n'))
    config = load_config(home=_home(tmp_path, '[backend]\nreasoning_summary = "none"\n'
                                    '[runtime]\nshow_reasoning = false\n'))
    assert config.backend.reasoning_summary == "none" and config.show_reasoning is False
    assert config.explain("show_reasoning") == "show_reasoning = False (from user)"


def test_dash_c_reaches_both_settings():
    assert parse_settings(["show_reasoning=false", "reasoning_summary=concise"]) == {
        "runtime": {"show_reasoning": False}, "backend": {"reasoning_summary": "concise"}}


def _built(repo, tmp_path, *items):
    wiring = app.build(repo, home=_home(tmp_path), console=Console(file=io.StringIO()),
                       overrides={"base_url": "http://127.0.0.1:9/v1", "model": "fake"},
                       settings=parse_settings(list(items)))
    app._close(wiring, app.TerminalStatus.CANCELLED, "done")
    return wiring


def test_the_session_is_wired_from_the_settings(repo, tmp_path):
    anthropic = _built(repo, tmp_path, "protocol=anthropic_messages")
    assert anthropic.runtime.backend.thinking_display == "summarized"
    assert anthropic.runtime.show_reasoning is True
    quiet = _built(repo, tmp_path, "protocol=anthropic_messages", "reasoning_summary=none",
                   "show_reasoning=false")
    assert quiet.runtime.backend.thinking_display is None
    assert quiet.runtime.show_reasoning is False
    assert _built(repo, tmp_path, "protocol=responses").runtime.backend.reasoning_summary is None
    assert _built(repo, tmp_path, "protocol=responses",
                  "reasoning_summary=none").runtime.backend.reasoning_summary is None
    assert _built(repo, tmp_path, "protocol=responses",
                  "reasoning_summary=detailed").runtime.backend.reasoning_summary == "detailed"
