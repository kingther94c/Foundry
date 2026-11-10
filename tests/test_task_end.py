"""A task's ending is not the session's (D-063).

At the interactive prompt, a task that ends -- the model calls finish, a
budget stops it, the context fills -- says how it ended and the prompt comes
back, as codex's does: only the user ends the session. The calls the task
left, finish among them, are answered before anything is sent again, so the
conversation stays one every provider takes. An unattended run ends with its
task, as before, and a resume answers what it left.
"""

from __future__ import annotations

import builtins

from foundry.cli import app
from foundry.cli.repl import TASK_ENDED
from foundry.cli.wiring import _close
from foundry.core.conversation import Message, Role, ToolUseBlock
from foundry.core.events import TerminalStatus
from foundry.core.resume import read_journal
from foundry.core.runtime import Budget
from foundry.core.session import EventType

from test_interrupt_turn import _results
from test_mode_model import _records, _session, _user_text
from test_resume import _wire, call, fake_model, repo, turn  # noqa: F401  (fixtures)


def _finish(cid="f1", status="completed"):
    return call(cid, "finish", {"status": status, "summary": "done", "claims": []})


def test_finish_ends_the_task_and_the_session_goes_on(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(_finish()), turn(text="you're welcome")])
    typist = _session(wiring, "do it", "thanks")
    assert TASK_ENDED in typist.printed
    seen = wiring.runtime.backend.seen
    assert len(seen) == 2 and _user_text(seen[1]) == "thanks"
    [answer] = _results(seen[1])
    assert answer.call_id == "f1" and not answer.is_error
    assert answer.content == "finish was accepted: the task ended with status completed."
    endings = [(e["status"], e["reason"]) for e in _records(wiring, EventType.TERMINATION)]
    assert endings == [("completed", "reported by the agent"), ("cancelled", "session ended")]


def test_calls_after_finish_are_answered_as_not_run(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(_finish(), call("r1", "read_file", {"path": "a.py"})), turn(text="ok")])
    _session(wiring, "do it", "and?")
    answers = _results(wiring.runtime.backend.seen[1])
    assert [(a.call_id, a.is_error) for a in answers] == [("f1", False), ("r1", True)]
    assert answers[1].content == (
        "Not run: the task ended (completed) before this call was executed. Nothing was "
        "changed by it; call it again if it is still needed.")


def test_a_budget_ends_the_task_and_says_why(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "read_file", {"path": "a.py"}), call("c2", "read_file", {"path": "b.py"})),
        turn(text="again, then"),
    ])
    wiring.runtime.budget = Budget(max_tool_calls=1)
    typist = _session(wiring, "read both", "go on")
    assert TASK_ENDED in typist.printed
    answers = _results(wiring.runtime.backend.seen[1])
    assert [(a.call_id, a.is_error) for a in answers] == [("c1", False), ("c2", True)]
    assert "the task ended (partial: tool calls exceeded (1))" in answers[1].content
    endings = [e["status"] for e in _records(wiring, EventType.TERMINATION)]
    assert endings == ["partial", "cancelled"]


def test_compact_after_a_finished_task_sends_finish_answered(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(_finish()), turn(text="the summary")])
    typist = _session(wiring, "do it", "/compact")
    summary_request = wiring.runtime.backend.seen[1]
    assert [a.call_id for a in _results(summary_request)] == ["f1"]
    assert _records(wiring, EventType.COMPACTED)
    assert not any("compaction failed" in line for line in typist.printed)


def test_a_resume_rebuilds_it_with_nothing_to_repair(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(_finish(), call("r1", "read_file", {"path": "a.py"})), turn(text="ok")])
    _session(wiring, "do it", "and?")
    state = read_journal(wiring.session.path)
    assert not state.repairs
    assert list(state.history) == wiring.runtime.context.history


def test_nothing_left_answers_nothing(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi")])
    _session(wiring, "hello")
    assert not _records(wiring, EventType.TOOL_REPLY)


def test_calls_left_before_any_ending_say_it_was_interrupted(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.context.append(Message(role=Role.ASSISTANT, blocks=(
        ToolUseBlock(call_id="x1", name="read_file", arguments="{}"),)))
    wiring.runtime.answer_left_calls()
    [reply] = _records(wiring, EventType.TOOL_REPLY)
    assert reply["content"].startswith("Not run: the task ended (interrupted) before")
    _close(wiring, TerminalStatus.CANCELLED, "session ended")


def test_an_unattended_run_still_ends_with_its_task(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(_finish(), call("r1", "read_file",
                                                                  {"path": "a.py"}))])
    assert wiring.runtime.run_turn("do it").status is TerminalStatus.COMPLETED
    _close(wiring, TerminalStatus.CANCELLED, "session ended")
    # Nothing answered in the journal: a resume repairs them, as it did.
    assert not _records(wiring, EventType.TOOL_REPLY)
    assert [r.outcome for r in read_journal(wiring.session.path).repairs] == [
        "finish_accepted", "not_run"]


def test_through_main_the_next_request_carries_finish_answered(repo, tmp_path, monkeypatch,
                                                                fake_model):
    from test_cli_e2e import text_turn, tool_turn

    httpd, handler = fake_model
    host, port = httpd.server_address
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    handler.turns, handler.index = [
        tool_turn("finish", {"status": "completed", "summary": "done", "claims": []}, "f1"),
        text_turn("you're welcome")], 0
    lines = iter(["do it", "thanks", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(lines))
    assert app.main(["run", "--workspace", str(repo)]) == 13
    second = httpd.requests[1]["messages"]
    assert [m["role"] for m in second[-3:]] == ["assistant", "tool", "user"]
    assert second[-2]["tool_call_id"] == "f1"
