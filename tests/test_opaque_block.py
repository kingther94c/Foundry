"""The OpaqueBlock and ModelTurn.notices IR additions.

docs/research/anthropic-messages.md, "IR additions": provider content the
runtime never interprets, carried so an adapter can replay it. Everything that
is not that adapter must ignore it or pass it through untouched -- the two
existing adapters, the context manager's masking and token estimate, and
redaction -- while the journal records it so a request can be rebuilt.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from foundry.core.backends import openai_compat, responses
from foundry.core.backends.replay import ScriptedBackend
from foundry.core.context import ContextManager, MASK_NOTICE, _estimate_tokens
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
    Usage,
)
from foundry.core.events import EventSink, Notice
from foundry.core.policy.engine import PolicyEngine
from foundry.core.redaction import Redactor
from foundry.core.runtime import AgentRuntime
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import Workspace

OPAQUE_DATA = json.dumps([
    {"type": "thinking", "thinking": "", "signature": "c2lnbmF0dXJl" * 40},
    {"type": "text", "text": "reading it"},
    {"type": "tool_use", "id": "toolu_1", "name": "read_file",
     "input": {"path": "a.py"}, "caller": {"type": "direct"}},
])
OPAQUE = OpaqueBlock(provider="anthropic", data=OPAQUE_DATA)
CALL = ToolUseBlock(call_id="toolu_1", name="read_file", arguments='{"path":"a.py"}')


# --- the IR itself ----------------------------------------------------------


def test_opaque_block_is_frozen_and_tagged():
    assert OPAQUE.kind == "opaque"
    with pytest.raises(dataclasses.FrozenInstanceError):
        OPAQUE.data = "{}"  # type: ignore[misc]


def test_model_turn_defaults_are_backward_compatible():
    turn = ModelTurn(text="hi")
    assert turn.opaque is None
    assert turn.notices == ()
    assert turn.as_message().blocks == (TextBlock("hi"),)


def test_as_message_appends_opaque_after_text_and_tool_use():
    turn = ModelTurn(text="reading it", tool_calls=(CALL,), opaque=OPAQUE)
    assert turn.as_message().blocks == (TextBlock("reading it"), CALL, OPAQUE)


def test_as_message_with_only_an_opaque_block():
    assert ModelTurn(text="", opaque=OPAQUE).as_message().blocks == (OPAQUE,)


# --- the existing adapters ignore it ----------------------------------------


def _request(*assistant_blocks) -> TurnRequest:
    return TurnRequest(
        messages=(
            Message.text(Role.SYSTEM, "you are foundry"),
            Message.text(Role.USER, "read a.py"),
            Message(role=Role.ASSISTANT, blocks=tuple(assistant_blocks)),
            Message(role=Role.TOOL, blocks=(ToolResultBlock(call_id="toolu_1",
                                                            content="VALUE = 1"),)),
        ),
        tools=(ToolSchema(name="read_file", description="read",
                          parameters={"type": "object"}),),
        model="m",
    )


ASSISTANT_SHAPES = [
    pytest.param((TextBlock("reading it"), CALL), id="text+tool_use"),
    pytest.param((CALL,), id="tool_use"),
    pytest.param((TextBlock("done"),), id="text"),
    pytest.param((), id="empty"),
]


@pytest.mark.parametrize("adapter", [openai_compat, responses],
                         ids=["openai_compat", "responses"])
@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
@pytest.mark.parametrize("blocks", ASSISTANT_SHAPES)
def test_adapter_wire_body_is_identical_with_and_without_opaque(adapter, stream, blocks):
    without = adapter.build_body(_request(*blocks), stream=stream)
    with_opaque = adapter.build_body(_request(*blocks, OPAQUE), stream=stream)
    assert json.dumps(with_opaque, sort_keys=True) == json.dumps(without, sort_keys=True)
    assert "signature" not in json.dumps(with_opaque)


# --- the context manager -----------------------------------------------------


def test_token_estimate_ignores_opaque_block():
    plain = Message(role=Role.ASSISTANT, blocks=(TextBlock("reading it"), CALL))
    carrying = Message(role=Role.ASSISTANT, blocks=(TextBlock("reading it"), CALL, OPAQUE))
    assert _estimate_tokens([carrying]) == _estimate_tokens([plain])


def test_masking_never_touches_an_opaque_block():
    # A tiny window so every eligible tool result is masked.
    context = ContextManager(system_prompt="sys", max_context_tokens=200,
                             mask_after_turns=1)
    context.append_user("go")
    for round_ in range(4):
        context.start_turn()
        opaque = OpaqueBlock(provider="anthropic", data=OPAQUE_DATA + str(round_))
        context.append(ModelTurn(text="", tool_calls=(CALL,), opaque=opaque).as_message())
        context.append_tool_result("toolu_1", "x" * 400)

    projected = context.project()
    assert any(isinstance(b, ToolResultBlock) and b.content == MASK_NOTICE
               for m in projected for b in m.blocks), "masking must have run"

    original = [m for m in context.history if m.role is Role.ASSISTANT]
    seen = [m for m in projected if m.role is Role.ASSISTANT]
    assert seen == original
    for before, after in zip(original, seen):
        assert after.blocks[-1] is before.blocks[-1]  # the very same object


def test_masked_message_keeps_an_opaque_block_unchanged():
    from foundry.core.context import _masked

    # Masking applies to tool messages; a stray opaque block in one survives.
    message = Message(role=Role.TOOL, blocks=(
        ToolResultBlock(call_id="c", content="y" * 500), OPAQUE))
    masked = _masked(message)
    assert masked.blocks[0].content == MASK_NOTICE
    assert masked.blocks[1] is OPAQUE


# --- redaction ---------------------------------------------------------------


def test_context_redaction_does_not_rewrite_opaque_data():
    redactor = Redactor()
    redactor.register("c2lnbmF0dXJl")  # appears in the signature on purpose
    context = ContextManager(redactor=redactor)
    context.start_turn()
    message = ModelTurn(text="", opaque=OPAQUE).as_message()
    context.append(message)
    # Only tool results pass the context redactor; replayed provider content
    # must reach the adapter byte for byte or a thinking signature breaks.
    assert context.project()[-1].blocks == (OPAQUE,)


# --- the runtime: journal and notices --------------------------------------


@pytest.fixture()
def wiring(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session = SessionStore(tmp_path / "sessions")

    def build(backend):
        events: list = []
        sink = EventSink()
        sink.subscribe(events.append)
        runtime = AgentRuntime(
            backend=backend, registry=default_registry(recorder=session),
            policy=PolicyEngine(), context=ContextManager(system_prompt="sys"),
            tool_ctx=ToolContext(workspace=Workspace(repo), artifacts=session.artifacts,
                                 read_tracker=ReadTracker()),
            session=session, events=sink, model="fake",
        )
        return runtime, session, events

    return build


def _records(session, event_type):
    session.close()
    return [r.payload for r in SessionStore.read_records(session.path)
            if r.type == event_type]


def test_block_payload_records_opaque_block():
    assert AgentRuntime._block_payload(OPAQUE) == {
        "kind": "opaque", "provider": "anthropic", "data": OPAQUE_DATA}


def test_journal_records_the_opaque_block_sent_back(wiring):
    read = ToolUseBlock(call_id="toolu_1", name="read_file",
                        arguments='{"path":"src/app.py"}')
    runtime, session, _ = wiring(ScriptedBackend([
        ModelTurn(text="reading it", tool_calls=(read,), opaque=OPAQUE,
                  usage=Usage(input_tokens=5, output_tokens=2),
                  stop_reason=StopReason.TOOL_USE),
        ModelTurn(text="VALUE is 1", usage=Usage(input_tokens=5, output_tokens=2)),
    ]))
    runtime.run_turn("what is VALUE?")

    requests = _records(session, EventType.MODEL_REQUEST)
    assistant = [m for m in requests[1]["messages"] if m["role"] == "assistant"][0]
    assert assistant["blocks"][-1] == {
        "kind": "opaque", "provider": "anthropic", "data": OPAQUE_DATA}
    assert [b["kind"] for b in assistant["blocks"]] == ["text", "tool_use", "opaque"]


def test_notices_reach_the_event_stream_and_the_journal(wiring):
    notices = ("thinking block dropped at messages.1.content.0: prefix mismatch",
               "fallback: claude-a declined, claude-b answered")
    runtime, session, events = wiring(ScriptedBackend([
        ModelTurn(text="done", notices=notices, usage=Usage(input_tokens=5, output_tokens=2)),
    ]))
    runtime.run_turn("hello")

    emitted = [e for e in events if isinstance(e, Notice) and e.text in notices]
    assert [e.text for e in emitted] == list(notices)
    assert all(e.level == "warning" for e in emitted)

    responses_ = _records(session, EventType.MODEL_RESPONSE)
    assert responses_[0]["notices"] == list(notices)


def test_model_response_journals_empty_notices_by_default(wiring):
    runtime, session, events = wiring(ScriptedBackend([
        ModelTurn(text="done", usage=Usage(input_tokens=5, output_tokens=2)),
    ]))
    runtime.run_turn("hello")
    assert _records(session, EventType.MODEL_RESPONSE)[0]["notices"] == []
    assert not [e for e in events if isinstance(e, Notice) and e.level == "warning"]
