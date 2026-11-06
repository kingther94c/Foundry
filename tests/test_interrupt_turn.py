"""Ctrl+C stops the turn, not the session (D-062).

At the interactive prompt, a Ctrl+C while the model answers or a tool runs --
or (a)bort at an approval -- ends that task as cancelled and comes back to the
prompt with the conversation as it was, as codex's does. Each call the task
left gets a result, so the next request is one every provider takes, and the
model is told with the next message that it was stopped.
"""

from __future__ import annotations

import pytest

from foundry.cli import app
from foundry.cli.repl import STOPPED, STOPPED_NOTE
from foundry.core.context import ContextManager
from foundry.core.conversation import Message, Role, ToolResultBlock, ToolUseBlock
from foundry.core.events import ApprovalChoice, TerminalStatus, ToolEnd
from foundry.core.resume import read_journal
from foundry.core.runtime import STOPPED_BEFORE_RUNNING, STOPPED_WHILE_RUNNING
from foundry.core.session import EventType, SessionStore

from test_mode_model import _records, _session, _user_text
from test_undo import _Typist
from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)


class _CtrlC:
    """A model turn the user stops while it streams; asked again, it answers."""

    def __init__(self, then):
        self.then, self.fired = then, False

    def __call__(self):
        if not self.fired:
            self.fired = True
            raise KeyboardInterrupt
        return self.then


def _stop_on(monkeypatch, wiring, tool: str, target: str = ""):
    """Ctrl+C while ``tool`` runs (on ``target``, when given)."""
    cls = type(wiring.runtime.registry.get(tool))
    original = cls.execute

    def execute(self, op, ctx):
        if target in op.target:
            raise KeyboardInterrupt
        return original(self, op, ctx)

    monkeypatch.setattr(cls, "execute", execute)


def _results(request):
    return [b for m in request.messages if m.role is Role.TOOL for b in m.blocks]


def _ordinal_of(wiring, kind, call_id):
    return next(r.ordinal for r in SessionStore.read_records(wiring.session.path)
                if r.type == kind and r.payload.get("call_id") == call_id)


# --- while the model answers -------------------------------------------------------------


def test_ctrl_c_while_the_model_answers_stops_the_turn_only(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [_CtrlC(turn(text="here it is"))])
    typist = _session(wiring, "explain a.py", "go on")
    assert STOPPED in typist.printed
    seen = wiring.runtime.backend.seen
    assert len(seen) == 1                                    # the stopped request, asked again
    users = [m.text_content for m in seen[0].messages if m.role is Role.USER]
    assert users == ["explain a.py", f"{STOPPED_NOTE}\n\ngo on"]
    endings = _records(wiring, EventType.TERMINATION)
    assert [(e["status"], e["reason"]) for e in endings] == [
        ("cancelled", "interrupted by user"), ("cancelled", "session ended")]


# --- while a tool runs -------------------------------------------------------------------


def test_ctrl_c_while_a_tool_runs_answers_every_call_it_left(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "read_file", {"path": "a.py"}), call("c2", "read_file", {"path": "b.py"}),
             call("c3", "read_file", {"path": "a.py"})),
        turn(text="ok"),
    ])
    _stop_on(monkeypatch, wiring, "read_file", "b.py")
    events: list = []
    wiring.runtime.events.subscribe(events.append)
    _session(wiring, "read them", "and now?")

    seen = wiring.runtime.backend.seen
    results = _results(seen[1])
    assert [(b.call_id, b.is_error) for b in results] == [
        ("c1", False), ("c2", True), ("c3", True)]
    assert results[1].content == STOPPED_WHILE_RUNNING
    assert results[2].content == STOPPED_BEFORE_RUNNING
    assert _user_text(seen[1]) == f"{STOPPED_NOTE}\n\nand now?"
    ends = [(e.call_id, e.ok, e.summary) for e in events if isinstance(e, ToolEnd)]
    assert ends[-1] == ("c2", False, "interrupted") and len(ends) == 2
    assert [r["call_id"] for r in _records(wiring, EventType.TOOL_REPLY)] == ["c1", "c2", "c3"]
    # A resume rebuilds this conversation, with nothing to repair.
    state = read_journal(wiring.session.path)
    assert not state.repairs
    assert list(state.history) == wiring.runtime.context.history


def test_a_tool_stopped_mid_change_leaves_no_command_vouching(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
    ])
    _stop_on(monkeypatch, wiring, "apply_patch")
    _session(wiring, "change A")
    runtime = wiring.runtime
    assert runtime._last_mutation == _ordinal_of(wiring, EventType.TOOL_REPLY, "p1")
    assert not runtime.tool_ctx.read_tracker.has_read("a.py")


def test_a_command_stopped_is_not_counted_as_a_change(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("x1", "run_command", {"command": "python -c \"print(1)\""})),
    ])
    _stop_on(monkeypatch, wiring, "run_command")
    _session(wiring, "run it")
    runtime = wiring.runtime
    assert _results(runtime.backend.seen[-1])[-1].call_id == "r1"
    assert [r["content"] for r in _records(wiring, EventType.TOOL_REPLY)
            if r["call_id"] == "x1"] == [STOPPED_WHILE_RUNNING]
    assert runtime._last_mutation == 0
    assert runtime.tool_ctx.read_tracker.has_read("a.py")


# --- an abort at an approval -------------------------------------------------------------


def test_an_abort_stops_the_task_and_the_session_goes_on(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("p1", "apply_patch", {"patch": PATCH_A}), call("r1", "read_file", {"path": "a.py"})),
        turn(text="as you wish"),
    ], approve=ApprovalChoice.ABORT)
    typist = _session(wiring, "change A", "leave it")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    seen = wiring.runtime.backend.seen
    # The next message ran: the abort stopped its own task, not every one after it.
    assert len(seen) == 2 and STOPPED in typist.printed
    assert [(b.call_id, b.content) for b in _results(seen[1])] == [
        ("p1", "the user aborted the task"), ("r1", STOPPED_BEFORE_RUNNING)]
    assert _user_text(seen[1]) == f"{STOPPED_NOTE}\n\nleave it"


def test_a_call_id_used_again_is_not_taken_for_the_one_that_ran(repo, tmp_path):
    # Some servers number calls afresh in each response: call_0 again.
    def approve(request):
        return ApprovalChoice.ABORT if request.tool == "apply_patch" else ApprovalChoice.ONCE

    wiring = _wire(repo, tmp_path / "home", [
        turn(call("call_0", "read_file", {"path": "a.py"})),
        turn(call("call_1", "apply_patch", {"patch": PATCH_A}),
             call("call_0", "read_file", {"path": "b.py"})),
    ])
    wiring.runtime.approval = approve
    _session(wiring, "change A")
    replies = [(r["call_id"], r["content"]) for r in _records(wiring, EventType.TOOL_REPLY)]
    assert replies[1:] == [("call_1", "the user aborted the task"),
                           ("call_0", STOPPED_BEFORE_RUNNING)]


def test_ctrl_c_during_compact_keeps_the_conversation(repo, tmp_path):
    # The summary request is the one stopped; the next message is answered.
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi"), _CtrlC(turn(text="again"))])
    typist = _Typist(["hello", "/compact", "still here?", "/exit"])
    app._close(wiring, app._repl(wiring, typist), "session ended")
    assert ("[yellow]compaction cancelled; the conversation is unchanged[/yellow]"
            in typist.printed)
    assert [m.text_content for m in wiring.runtime.context.history
            if m.role is Role.USER] == ["hello", "still here?"]
    assert not _records(wiring, EventType.COMPACTED)


# --- the runtime's side ------------------------------------------------------------------


def test_a_ctrl_c_as_the_task_ends_writes_no_second_ending(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime._terminate(TerminalStatus.PARTIAL, "over budget")
    outcome = wiring.runtime.interrupted()
    assert outcome.status is TerminalStatus.CANCELLED
    assert outcome.text == "interrupted by user"
    app._close(wiring, TerminalStatus.CANCELLED, "session ended")
    assert [e["status"] for e in _records(wiring, EventType.TERMINATION)] == ["partial"]


def test_one_shot_runs_still_end_on_ctrl_c(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [_CtrlC(turn(text="never"))])
    with pytest.raises(KeyboardInterrupt):
        wiring.runtime.run_turn("explain a.py")
    app._close(wiring, TerminalStatus.CANCELLED, "interrupted")


def _uses(*ids):
    return Message(role=Role.ASSISTANT, blocks=tuple(
        ToolUseBlock(call_id=i, name="read_file", arguments="{}") for i in ids))


def _result(call_id):
    return Message(role=Role.TOOL, blocks=(ToolResultBlock(call_id=call_id, content="x"),))


@pytest.mark.parametrize("history, left", [
    ([], []),
    ([Message.text(Role.USER, "hi")], []),
    ([Message.text(Role.USER, "hi"), Message.text(Role.ASSISTANT, "hello")], []),
    ([Message.text(Role.USER, "hi"), _uses("c1", "c2")], ["c1", "c2"]),
    ([Message.text(Role.USER, "hi"), _uses("c1", "c2"), _result("c1")], ["c2"]),
    ([Message.text(Role.USER, "hi"), _uses("c1"), _result("c1"),
      Message.text(Role.USER, "more")], []),
    ([_uses("old"), Message.text(Role.USER, "hi"), _uses("c1"), _result("c1")], []),
])
def test_unanswered_calls_are_the_newest_message_s(history, left):
    context = ContextManager(system_prompt="t")
    for message in history:
        context.append(message)
    assert [c.call_id for c in context.unanswered_calls()] == left
