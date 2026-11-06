"""Approving a plan (D-038): `/approve` ends plan mode, as the user chooses.

Three properties carry it. Only the user can approve, by typing it; what the
model is told afterwards is what the policy now does, because the permissions
paragraph is built again; and the change is journaled, decision by decision,
without carrying over a resume.
"""

from __future__ import annotations

import dataclasses

from foundry.cli import app
from foundry.core.events import ApprovalChoice, Notice, PlanStep, StepStatus, TerminalStatus
from foundry.core.policy.engine import Mode, PolicyEngine
from foundry.core.prompts import (
    base_system_prompt,
    mode_line,
    permissions_paragraph,
    system_prompt,
)
from foundry.core.resume import read_journal, resume_note
from foundry.core.session import EventType, SessionStore

from test_plan import plan
from test_resume import PATCH_A, _core, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from test_undo import _Typist


class _Keys(_Typist):
    """None in the script stands for the end of input."""

    def input(self, prompt=""):
        line = self.lines.pop(0)
        if line is None:
            raise EOFError
        return line


def _planning(repo, home, after, *, mode=Mode.PLAN):
    """A plan-mode session whose first task reads, plans, and tries an edit."""
    wiring = _wire(repo, home, [
        turn(call("r1", "read_file", {"path": "a.py"}),
             call("u1", "update_plan", plan(("Change A to 2", "pending"))),
             call("p0", "apply_patch", {"patch": PATCH_A})),
        turn(text="The plan: change A to 2."),
        *after,
    ], overrides={"mode": mode})
    asked: list[str] = []
    wiring.runtime.approval = lambda request: (asked.append(request.tool)
                                               or ApprovalChoice.ONCE)
    return wiring, asked


def _records(wiring, kind):
    return [r for r in SessionStore.read_records(wiring.session.path) if r.type == kind]


def _system(request) -> str:
    return request.messages[0].text_content


def test_approving_ends_plan_mode_and_starts_the_work(repo, tmp_path):
    wiring, asked = _planning(repo, tmp_path / "home", [
        turn(call("p1", "apply_patch", {"patch": PATCH_A})), turn(text="A is 2")])
    keys = _Keys(["plan how to make A 2", "/approve", "y", "/exit"])
    app._close(wiring, app._repl(wiring, keys), "session ended")

    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert asked == ["apply_patch"], "default mode still asks before each edit"
    assert wiring.runtime.policy.mode is Mode.DEFAULT
    # The plan being approved is shown before the question.
    assert any("Change A to 2" in line for line in keys.printed)

    seen = wiring.runtime.backend.seen
    assert mode_line(Mode.PLAN) in _system(seen[0])
    after = _system(seen[2])
    assert mode_line(Mode.DEFAULT) in after and "Plan mode" not in after
    told = seen[2].messages[-1].blocks[0].text
    assert told.startswith("The user approved your plan, so plan mode is over.")
    assert mode_line(Mode.DEFAULT) in told and "update_plan" in told

    (change,) = _records(wiring, EventType.MODE_CHANGE)
    assert change.payload == {"from": "plan", "to": "default", "by": "repl",
                              "reason": "the user approved the plan"}
    decisions = {r.payload["call_id"]: r.payload for r in _records(wiring,
                                                                   EventType.POLICY_DECISION)}
    assert decisions["p0"]["mode"] == "plan" and decisions["p0"]["verdict"] == "deny"
    assert decisions["p1"]["mode"] == "default"
    assert decisions["r1"]["mode"] == "plan"


def test_approving_with_edits_lets_patches_apply_without_asking(repo, tmp_path):
    wiring, asked = _planning(repo, tmp_path / "home", [
        turn(call("p1", "apply_patch", {"patch": PATCH_A})), turn(text="A is 2")])
    app._close(wiring, app._repl(wiring, _Keys(["plan it", "/approve", "e", "/exit"])),
               "session ended")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert asked == []
    assert wiring.runtime.policy.mode is Mode.ACCEPT_EDITS
    assert mode_line(Mode.ACCEPT_EDITS) in _system(wiring.runtime.backend.seen[2])
    (change,) = _records(wiring, EventType.MODE_CHANGE)
    assert change.payload["to"] == "accept_edits"


def test_declining_or_no_answer_keeps_planning_and_changes_nothing(repo, tmp_path):
    for answer in ("n", None, "maybe"):
        home = tmp_path / f"home-{answer}"
        wiring, asked = _planning(repo, home, [])
        keys = _Keys(["plan it", "/approve", answer, "/exit"])
        app._close(wiring, app._repl(wiring, keys), "session ended")
        assert wiring.runtime.policy.mode is Mode.PLAN
        assert len(wiring.runtime.backend.seen) == 2, "no turn ran"
        assert any("still in plan mode" in line for line in keys.printed)
        assert not _records(wiring, EventType.MODE_CHANGE)
        assert mode_line(Mode.PLAN) in wiring.runtime.context.system_message().text_content
        assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_outside_plan_mode_there_is_nothing_to_approve(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    keys = _Keys(["/approve", "/exit"])
    app._close(wiring, app._repl(wiring, keys), "session ended")
    assert any("nothing to approve" in line and "default mode" in line for line in keys.printed)
    assert wiring.runtime.backend.seen == []
    assert not _records(wiring, EventType.MODE_CHANGE)


def test_with_no_plan_set_it_says_so_before_asking(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="I would change A to 2.")],
                   overrides={"mode": Mode.PLAN})
    keys = _Keys(["plan it", "/approve", "n", "/exit"])
    app._close(wiring, app._repl(wiring, keys), "session ended")
    assert any("has not set a plan" in line for line in keys.printed)


def test_an_approval_does_not_carry_over_a_resume(repo, tmp_path):
    home = tmp_path / "home"
    wiring, _ = _planning(repo, home, [
        turn(call("p1", "apply_patch", {"patch": PATCH_A})), turn(text="A is 2")])
    app._close(wiring, app._repl(wiring, _Keys(["plan it", "/approve", "y", "/exit"])),
               "session ended")
    assert read_journal(wiring.session.path).mode == "default"

    resumed = _wire(repo, home, [], resume="last", overrides={"mode": Mode.PLAN})
    events: list = []
    resumed.runtime.events.subscribe(events.append)
    app._announce_resume(resumed)
    assert resumed.runtime.policy.mode is Mode.PLAN
    assert resumed.runtime.plan == (PlanStep("Change A to 2", StepStatus.PENDING),)
    assert ("the user had switched this session to default mode. That does not carry "
            "over a resume: this run is in plan mode") in resumed.note
    assert any(isinstance(e, Notice) and "/approve" in e.text and "default mode" in e.text
               for e in events)
    app._close(resumed, TerminalStatus.CANCELLED, "session ended")


def test_a_session_never_switched_says_nothing_about_modes(tmp_path):
    runtime, session, _ = _core(tmp_path, [turn(text="hi")])
    runtime.run_turn("hello")
    session.close()
    state = read_journal(session.path)
    assert state.mode == ""
    assert "switched" not in resume_note(state, frozenset(), "plan")
    # Switched, then resumed in the mode it was switched to: nothing to say.
    switched = dataclasses.replace(state, mode="default")
    assert "switched" not in resume_note(switched, frozenset(), "default")
    assert "switched this session to default mode" in resume_note(switched, frozenset(), "plan")


def test_the_model_is_told_how_a_plan_is_approved():
    assert "/approve" in mode_line(Mode.PLAN)
    policy = PolicyEngine(mode=Mode.PLAN)
    assert mode_line(Mode.PLAN) in permissions_paragraph(policy)
    assert system_prompt(policy) == base_system_prompt() + "\n\n" + permissions_paragraph(policy)
