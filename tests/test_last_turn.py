"""A task whose rounds or calls run out gets one last turn to wind up (D-197).

It ended at once, with a summary that said only which limit was reached and
nothing of where the work stood. Now, as opencode's max-steps prompt does, the
model is told the budget is spent and asked once more, offered only finish;
what it says there is the task's summary. A token ceiling is a limit on what
is spent, and still ends the task at once.
"""

from __future__ import annotations

from foundry.core.events import TerminalStatus
from foundry.core.resume import read_journal
from foundry.core.runtime import LAST_TURN_TYPED, Budget
from foundry.core.session import EventType, SessionStore

from test_headless import _unattended
from test_headless import repo  # noqa: F401 (fixture)
from test_resume import _core, call, finish, turn


def _read(cid, path="x.txt"):
    return call(cid, "read_file", {"path": path})


def _results(request):
    return {b.call_id: b for m in request.messages for b in m.blocks
            if getattr(b, "call_id", None) and hasattr(b, "is_error")}


def _texts(request):
    return [b.text for m in request.messages if m.role.value == "user"
            for b in m.blocks if hasattr(b, "text") and not hasattr(b, "call_id")]


def _records(session, kind):
    return [r.payload for r in SessionStore.read_records(session.path) if r.type == kind]


def _core_with(tmp_path, turns, budget):
    runtime, session, repo = _core(tmp_path, turns, budget=budget)
    (repo / "x.txt").write_text("data\n", encoding="utf-8")
    return runtime, session, repo


def test_rounds_run_out_and_the_last_request_offers_only_finish(tmp_path):
    runtime, session, _ = _core_with(tmp_path, [
        turn(_read("r1")), turn(_read("r2")),
        finish("f1", status="partial")], Budget(max_tool_rounds=2))
    outcome = runtime.run_turn("read it")
    assert (outcome.status, outcome.summary) == (TerminalStatus.PARTIAL, "done")
    seen = runtime.backend.seen
    assert len(seen) == 3
    assert [t.name for t in seen[2].tools] == ["finish"]
    assert "read_file" in [t.name for t in seen[1].tools]
    told = _texts(seen[2])[-1]
    assert told.startswith("This task has used its budget (tool-call rounds exceeded (2)), "
                           "so no more tools will run. Call finish now")
    session.close()
    inputs = _records(session, EventType.USER_INPUT)
    assert inputs[-1] == {"text": told, "typed": LAST_TURN_TYPED}


def test_the_model_s_own_status_stands(tmp_path):
    # Done and checked before the limit, it says so.
    runtime, _, _ = _core_with(tmp_path, [turn(_read("r1")), finish("f1")],
                               Budget(max_tool_rounds=1))
    assert runtime.run_turn("read it").status is TerminalStatus.COMPLETED


def test_calls_past_the_limit_are_answered_and_not_run(tmp_path):
    patch = ("*** Begin Patch\n*** Add File: new.txt\n+hi\n*** End Patch\n")
    runtime, _, repo = _core_with(tmp_path, [
        turn(_read("r1"), call("p1", "apply_patch", {"patch": patch}), _read("r2")),
        finish("f1", status="partial")], Budget(max_tool_calls=1))
    outcome = runtime.run_turn("go")
    assert outcome.status is TerminalStatus.PARTIAL
    answers = _results(runtime.backend.seen[1])
    assert not answers["r1"].is_error
    for cid in ("p1", "r2"):
        assert answers[cid].is_error
        assert answers[cid].content == (
            "Not run: the task's budget is spent (tool calls exceeded (1)), so only finish "
            "is accepted now. Nothing was changed by this call.")
    assert not (repo / "new.txt").exists()
    assert runtime.budget.calls == 2


def test_a_last_turn_that_asks_for_more_is_refused_and_ends_the_task(tmp_path):
    patch = ("*** Begin Patch\n*** Add File: new.txt\n+hi\n*** End Patch\n")
    runtime, session, repo = _core_with(tmp_path, [
        turn(_read("r1")), turn(call("p1", "apply_patch", {"patch": patch}))],
        Budget(max_tool_rounds=1))
    outcome = runtime.run_turn("go")
    assert (outcome.status, outcome.text) == (TerminalStatus.PARTIAL,
                                              "tool-call rounds exceeded (1)")
    assert not (repo / "new.txt").exists()
    session.close()
    state = read_journal(session.path)
    assert not state.repairs                      # every call has its answer
    replies = _records(session, EventType.TOOL_REPLY)
    assert replies[-1]["call_id"] == "p1" and replies[-1]["content"].startswith("Not run")


def test_finish_beside_a_refused_call_is_taken(tmp_path):
    runtime, _, _ = _core_with(tmp_path, [
        turn(_read("r1")),
        turn(_read("r2"), call("f1", "finish", {"status": "partial", "summary": "half",
                                                "claims": []}))],
        Budget(max_tool_rounds=1))
    outcome = runtime.run_turn("go")
    assert (outcome.status, outcome.summary) == (TerminalStatus.PARTIAL, "half")
    # r2 is answered, as not run; finish is, as ever, when the conversation goes on.
    assert [c.call_id for c in runtime.context.unanswered_calls()] == ["f1"]


def test_a_prose_last_turn_is_the_summary(tmp_path):
    runtime, _, _ = _core_with(tmp_path, [
        turn(_read("r1")), turn(text="  Read x.txt; the fix is still to do.  ")],
        Budget(max_tool_rounds=1))
    outcome = runtime.run_turn("go")
    assert (outcome.status, outcome.summary) == (TerminalStatus.PARTIAL,
                                                 "Read x.txt; the fix is still to do.")
    assert runtime._ended == (TerminalStatus.PARTIAL, "tool-call rounds exceeded (1)")


def test_a_bad_finish_in_the_last_turn_still_ends_it(tmp_path):
    runtime, _, _ = _core_with(tmp_path, [
        turn(_read("r1")), turn(call("f1", "finish", {"status": "maybe"}))],
        Budget(max_tool_rounds=1))
    outcome = runtime.run_turn("go")
    assert (outcome.status, outcome.text) == (TerminalStatus.PARTIAL,
                                              "tool-call rounds exceeded (1)")
    assert len(runtime.backend.seen) == 2


def test_a_token_ceiling_ends_the_task_at_once(tmp_path):
    runtime, _, _ = _core_with(tmp_path, [
        turn(_read("r1")), finish("f1")], Budget(max_tokens=1))
    outcome = runtime.run_turn("go")
    assert (outcome.status, outcome.text) == (TerminalStatus.PARTIAL,
                                              "token budget exceeded (1)")
    assert len(runtime.backend.seen) == 1


def test_rounds_out_and_tokens_out_together_end_at_once(tmp_path):
    runtime, _, _ = _core_with(tmp_path, [
        turn(_read("r1")), finish("f1")], Budget(max_tool_rounds=1, max_tokens=1))
    outcome = runtime.run_turn("go")
    assert outcome.text == "token budget exceeded (1)"
    assert len(runtime.backend.seen) == 1


def test_the_last_turn_has_no_ceiling_of_its_own_but_tokens(tmp_path):
    # Rounds ran out; the last turn spends past the token ceiling: it is not
    # asked again.
    runtime, _, _ = _core_with(tmp_path, [
        turn(_read("r1")), turn(text="summary")], Budget(max_tool_rounds=1, max_tokens=20))
    outcome = runtime.run_turn("go")
    assert outcome.summary == "summary"
    assert len(runtime.backend.seen) == 2


def test_with_no_finish_to_offer_the_task_ends_at_once(tmp_path):
    runtime, _, _ = _core_with(tmp_path, [turn(_read("r1")), turn(_read("r2"))],
                               Budget(max_tool_rounds=1))
    del runtime.registry.tools["finish"]
    outcome = runtime.run_turn("go")
    assert outcome.text == "tool-call rounds exceeded (1)"
    assert len(runtime.backend.seen) == 1


def test_the_next_task_starts_with_every_tool_again(tmp_path):
    runtime, _, _ = _core_with(tmp_path, [
        turn(_read("r1")), turn(text="out"), turn(_read("r2")), finish("f2")],
        Budget(max_tool_rounds=1))
    runtime.run_turn("one")
    runtime.answer_left_calls()
    outcome = runtime.run_turn("two")
    assert outcome.status is TerminalStatus.COMPLETED
    third = runtime.backend.seen[2]
    assert "read_file" in [t.name for t in third.tools]
    assert not _results(runtime.backend.seen[3])["r2"].is_error


def test_an_unattended_run_is_not_reminded_after_its_last_turn(repo, tmp_path):  # noqa: F811
    wiring = _unattended(repo, tmp_path, [
        turn(call("r1", "read_file", {"path": "a.py"})), turn(text="Read a.py; nothing else."),
        turn(text="never asked")])
    wiring.runtime.budget = Budget(max_tool_rounds=1)
    from foundry.cli.unattended import _run_unattended

    outcome = _run_unattended(wiring.runtime, "go")
    assert outcome.status is TerminalStatus.PARTIAL
    assert outcome.summary == "Read a.py; nothing else."
    assert len(wiring.runtime.backend.seen) == 2
