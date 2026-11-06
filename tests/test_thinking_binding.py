"""Thinking kept valid (D-048): Anthropic binds each thinking block to the
conversation sent before it.

Foundry changes that prefix itself -- masking an old result, rebuilding the
permissions paragraph, a resume's new system prompt. The blocks made against
the old prefix are then left out of every later request, instead of being
sent for the API to drop (and report) again each round; blocks before the
change, and blocks made after it, are sent as they are.
"""

from __future__ import annotations

import json

import pytest

from foundry.core.backends.anthropic import PROVIDER, build_body
from foundry.core.context import BOUND_PROVIDER, ContextManager, MASK_NOTICE, unbind
from foundry.core.conversation import (
    Message,
    OpaqueBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    TurnRequest,
)
from foundry.cli import app
from foundry.core.events import TerminalStatus
from foundry.core.resume import _as_received, read_journal
from foundry.core.session import EventType, SessionStore

from test_resume import _core, _restored, _wire, call, repo, turn  # noqa: F401 (fixture)


def thinking(word: str, *, text: str = "", calls=(), redacted=False) -> OpaqueBlock:
    content = [{"type": "redacted_thinking", "data": f"enc-{word}"} if redacted
               else {"type": "thinking", "thinking": word, "signature": f"sig-{word}"}]
    if text:
        content.append({"type": "text", "text": text})
    for call_id in calls:
        content.append({"type": "tool_use", "id": call_id, "name": "read_file",
                        "input": {"path": "a.py"}, "caller": {"type": "direct"}})
    return OpaqueBlock(provider="anthropic", data=json.dumps(content))


def reply(word: str, *, text: str = "", calls=()) -> Message:
    blocks = [TextBlock(text)] if text else []
    blocks += [ToolUseBlock(call_id=c, name="read_file", arguments='{"path": "a.py"}')
               for c in calls]
    return Message(Role.ASSISTANT, (*blocks, thinking(word, text=text, calls=calls)))


def words(messages) -> list[str]:
    """The thinking each assistant message still carries, in order."""
    found = []
    for message in messages:
        for block in message.blocks:
            if isinstance(block, OpaqueBlock):
                found += [c["thinking"] for c in json.loads(block.data)
                          if c.get("type") == "thinking"]
    return found


# --- unbind ------------------------------------------------------------------------------


def test_the_provider_is_the_anthropic_adapters():
    assert BOUND_PROVIDER == PROVIDER


def test_unbind_takes_out_thinking_and_keeps_the_rest_verbatim():
    message = reply("why", text="Reading it.", calls=["c1"])
    stripped = unbind(message)
    (opaque,) = [b for b in stripped.blocks if isinstance(b, OpaqueBlock)]
    assert json.loads(opaque.data) == [
        {"type": "text", "text": "Reading it."},
        {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.py"},
         "caller": {"type": "direct"}}]
    assert stripped.blocks[:2] == message.blocks[:2]


def test_unbind_takes_out_redacted_thinking_too():
    message = Message(Role.ASSISTANT, (TextBlock("x"), thinking("w", text="x", redacted=True)))
    assert "redacted" not in unbind(message).blocks[-1].data


def test_an_opaque_block_of_only_thinking_goes():
    message = Message(Role.ASSISTANT, (TextBlock("ok"), thinking("hm")))
    assert unbind(message).blocks == (TextBlock("ok"),)


@pytest.mark.parametrize("message", [
    Message(Role.ASSISTANT, (TextBlock("plain"),)),
    Message(Role.ASSISTANT, (TextBlock("x"), OpaqueBlock(provider="other", data=json.dumps(
        [{"type": "thinking", "thinking": "w", "signature": "s"}])))),
    Message(Role.ASSISTANT, (OpaqueBlock(provider="anthropic", data="not json"),)),
    Message(Role.ASSISTANT, (OpaqueBlock(provider="anthropic", data='{"type": "text"}'),)),
    Message(Role.ASSISTANT, (thinking("w", text="x").__class__(
        provider="anthropic", data=json.dumps([{"type": "text", "text": "x"}])),)),
    Message(Role.USER, (TextBlock("a user's words"),)),
    Message(Role.USER, (thinking("not a reply"),)),
])
def test_unbind_leaves_alone_what_has_no_bound_thinking(message):
    assert unbind(message) is message


# --- the context -------------------------------------------------------------------------


def _context(**kw) -> ContextManager:
    return ContextManager(system_prompt="base", **kw)


def _round(context: ContextManager, word: str, call_id: str, output: str = "ok"):
    """One tool round: the request is sent, the reply and its result arrive."""
    context.start_turn()
    context.mark_sent()
    context.append(reply(word, calls=[call_id]))
    context.append_tool_result(call_id, output)


def test_a_conversation_that_only_grows_sends_every_block():
    context = _context()
    context.append_user("go")
    for n in range(4):
        _round(context, f"w{n}", f"c{n}")
    assert words(context.project()) == ["w0", "w1", "w2", "w3"]


def test_a_new_system_prompt_leaves_out_every_block_made_before_it():
    context = _context()
    context.append_user("go")
    _round(context, "w0", "c0")
    _round(context, "w1", "c1")
    context.system_prompt = "base, now in plan mode"
    assert words(context.project()) == []
    context.mark_sent()
    _round(context, "w2", "c2")              # made against the new prompt
    _round(context, "w3", "c3")
    assert words(context.project()) == ["w2", "w3"]
    # The text and calls of the earlier replies are still sent.
    calls = [b.call_id for m in context.project() for b in m.blocks
             if isinstance(b, ToolUseBlock)]
    assert calls == ["c0", "c1", "c2", "c3"]


def test_masking_leaves_out_the_blocks_after_the_masked_result_only():
    context = _context(max_context_tokens=4_000, mask_after_turns=2)
    context.append_user("go")
    big = "x" * 6_000
    for n in range(6):
        _round(context, f"w{n}", f"c{n}", output=big if n < 2 else "ok")
        if n == 2:
            # Round 2's request was past half the window and masked c0's
            # output. w0 comes before c0 and stays; w1 was made against the
            # unmasked c0 and goes; w2 was made by the request that masked it,
            # so it is bound to the new prefix and stays.
            projected = context.project()
            assert any(isinstance(b, ToolResultBlock) and b.content == MASK_NOTICE
                       for m in projected for b in m.blocks)
            assert words(projected) == ["w0", "w2"]
    later = words(context.project())
    assert later[:1] == ["w0"] and "w1" not in later


def test_the_message_that_differs_from_the_one_sent_loses_its_thinking_too():
    # What is sent at the difference is not what was received there, so
    # nothing from that position on is trusted with its reasoning.
    context = _context()
    context.append_user("go")
    _round(context, "w0", "c0")
    context.mark_sent()
    sent = list(context._sent)
    sent[2] = reply("w0", text="something else", calls=["c0"])
    context._sent = tuple(sent)
    assert words(context.project()) == []


def test_projecting_again_without_sending_changes_nothing():
    context = _context()
    context.append_user("go")
    _round(context, "w0", "c0")
    context.system_prompt = "changed"
    first = context.project()
    assert context.project() == first and context._unbound == set()   # not until sent
    context.mark_sent()
    assert context._unbound == {2}


def test_a_compaction_starts_a_new_prefix():
    context = _context()
    context.append_user("go")
    _round(context, "w0", "c0")
    context.system_prompt = "changed"
    context.mark_sent()
    context.replace_history([Message.text(Role.USER, "summary")])
    assert context._sent == () and context._unbound == set()
    _round(context, "w1", "c1")
    assert words(context.project()) == ["w1"]


def test_the_wire_body_carries_the_reply_without_its_thinking():
    context = _context()
    context.append_user("go")
    context.mark_sent()
    context.append(reply("w0", text="Reading.", calls=["c0"]))
    context.append_tool_result("c0", "A = 1")
    context.system_prompt = "changed"
    body = build_body(TurnRequest(messages=context.project(), tools=(), model="m"),
                      stream=False)
    assistant = [m for m in body["messages"] if m["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": [
        {"type": "text", "text": "Reading."},
        {"type": "tool_use", "id": "c0", "name": "read_file", "input": {"path": "a.py"},
         "caller": {"type": "direct"}}]}]


# --- the runtime and a resume ------------------------------------------------------------


def _session(tmp_path, rounds: int, **kw):
    script = [turn(call(f"r{n}", "read_file", {"path": "a.py"}), opaque=thinking(f"w{n}"))
              for n in range(rounds)] + [turn(text="done", opaque=thinking("end", text="done"))]
    runtime, session, repo = _core(tmp_path, script, **kw)
    (repo / "a.py").write_text("A = 1\n", encoding="utf-8")
    return runtime, session


def test_the_requests_sent_and_journaled_leave_out_invalidated_thinking(tmp_path):
    runtime, session = _session(tmp_path, 3)
    runtime.run_turn("read a.py")
    runtime.context.system_prompt = "t, and now a new rule"     # what /mode does
    runtime.backend.turns.append(turn(text="noted", opaque=thinking("after", text="noted")))
    runtime.run_turn("continue")
    seen = runtime.backend.seen
    assert words(seen[3].messages) == ["w0", "w1", "w2"]          # grew: all sent
    assert words(seen[4].messages) == []                         # after the change
    requests = [r.payload for r in SessionStore.read_records(session.path)
                if r.type == EventType.MODEL_REQUEST]
    assert "sig-w0" in json.dumps(requests[3]) and "sig-w0" not in json.dumps(requests[4])
    # The live history keeps every reply as received.
    assert words(runtime.context.history) == ["w0", "w1", "w2", "end", "after"]


def test_a_resume_rebuilds_the_replies_as_received_and_what_was_left_out(tmp_path):
    runtime, session = _session(tmp_path, 2)
    runtime.run_turn("read a.py")
    runtime.context.system_prompt = "t, changed"
    runtime.backend.turns.append(turn(call("r9", "read_file", {"path": "a.py"}),
                                      opaque=thinking("w9")))
    runtime.backend.turns.append(turn(text="fine", opaque=thinking("fin", text="fine")))
    runtime.run_turn("again")
    session.close()
    live = runtime.context

    state = read_journal(session.path)
    assert state.last_sent == runtime.backend.seen[-1].messages
    restored = _restored(state)
    restored.system_prompt = live.system_prompt
    assert restored.history == live.history
    assert restored._unbound == live._unbound
    assert restored.project() == live.project()


def test_a_resume_with_a_new_system_prompt_sends_none_of_the_old_thinking(tmp_path):
    runtime, session = _session(tmp_path, 2)
    runtime.run_turn("read a.py")
    session.close()
    restored = _restored(read_journal(session.path))
    assert words(restored.project()) == ["w0", "w1", "end"]       # the same prompt: all
    restored.system_prompt = "t, after the resume"
    assert words(restored.project()) == []


def test_a_reply_is_put_back_only_where_it_is_the_one_received():
    received = reply("w0", text="A is 1.")
    original = received.blocks[-1]
    assert _as_received(unbind(received), original) == received
    assert _as_received(received, original) is received
    other = unbind(reply("w1", text="B is 2."))
    assert _as_received(other, original) is other
    user = Message.text(Role.USER, "hi")
    assert _as_received(user, original) is user


def test_a_compaction_forgets_the_reply_received_before_it(tmp_path):
    runtime, session = _session(tmp_path, 0)
    runtime.run_turn("go")                      # its reply: "done", thinking "end"
    session.append(EventType.COMPACTED, {"turn": 2, "messages": [
        {"role": "user", "blocks": [{"kind": "text", "text": "summary"}]}]})
    # A reply after the compaction that, stripped, reads the same as "end"'s.
    stripped = unbind(Message(Role.ASSISTANT, (TextBlock("done"), thinking("end", text="done"))))
    session.append(EventType.MODEL_REQUEST, {"turn": 2, "messages": [
        {"role": "system", "blocks": [{"kind": "text", "text": "t"}]},
        {"role": "user", "blocks": [{"kind": "text", "text": "summary"}]},
        *[{"role": m.role.value, "blocks": [runtime._block_payload(b) for b in m.blocks]}
          for m in (stripped, Message.text(Role.USER, "next"))]]})
    session.close()
    state = read_journal(session.path)
    assert state.history[1] == stripped and words(state.history) == []


def test_after_a_compaction_a_resume_compares_with_nothing(tmp_path):
    runtime, session = _session(tmp_path, 1)
    runtime.run_turn("read a.py")
    session.append(EventType.COMPACTED, {"turn": 9, "messages": [
        {"role": "user", "blocks": [{"kind": "text", "text": "summary"}]}]})
    session.close()
    assert read_journal(session.path).last_sent == ()


def test_each_reply_is_stripped_once(monkeypatch):
    from foundry.core import context as context_module
    calls: list = []
    real = context_module.unbind
    monkeypatch.setattr(context_module, "unbind", lambda m: calls.append(m) or real(m))
    context = _context()
    context.append_user("go")
    for n in range(3):
        _round(context, f"w{n}", f"c{n}")
    context.mark_sent()                          # every reply was sent
    context.system_prompt = "changed"
    for _ in range(5):
        context.project()
    parsed = [m for m in calls if m.role is Role.ASSISTANT]
    assert len(parsed) == 3 and words(context.project()) == []


def test_a_resume_through_the_app_keeps_out_what_was_left_out(repo, tmp_path):
    home = tmp_path / "home"
    first = _wire(repo, home, [
        turn(call("c1", "read_file", {"path": "a.py"}), opaque=thinking("w0")),
        turn(text="A is 1", opaque=thinking("w1", text="A is 1")),
        turn(text="noted", opaque=thinking("w2", text="noted"))])
    first.runtime.run_turn("what is A?")
    first.runtime.context.system_prompt += "\nA new rule."       # what /mode does
    first.runtime.run_turn("noted?")
    assert words(first.runtime.backend.seen[-1].messages) == []
    app._close(first, TerminalStatus.CANCELLED, "session ended")

    # The resumed prompt is built again, without the rule: w2 was made
    # against the prompt with it, w0 and w1 against the one before.
    after = _wire(repo, home, [turn(text="B is 1", opaque=thinking("w3", text="B is 1"))],
                  resume="last")
    after.runtime.run_turn("and B?")
    assert words(after.runtime.backend.seen[0].messages) == []
    assert words(after.runtime.context.history) == ["w0", "w1", "w2", "w3"]
    app._close(after, TerminalStatus.CANCELLED, "session ended")
