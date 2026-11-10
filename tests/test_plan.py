"""update_plan (D-037): the model's plan, kept where the user can see it.

The plan is the model's own account, so two properties carry the feature. What
reaches the user is one line of plain text per step, scrubbed like any other
model output. And nothing about the plan changes what a session reports:
finish lists the steps not marked completed and decides nothing over them.
"""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.render import Renderer
from foundry.cli.repl import repl, with_note
from foundry.cli.unattended import _event_payload
from foundry.cli.wiring import _announce_resume, _close
from foundry.core.errors import InvalidToolCall
from foundry.core.events import (
    ApprovalChoice,
    ApprovalKind,
    ApprovalRequest,
    EventSink,
    Notice,
    PlanStep,
    PlanUpdated,
    StepStatus,
    TerminalStatus,
    Termination,
    ToolBegin,
    ToolEnd,
)
from foundry.core.policy.engine import Mode, PolicyEngine, Verdict, builtin_rules
from foundry.core.prompts import base_system_prompt, permissions_paragraph
from foundry.core.redaction import Redactor
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.plan import MAX_STEPS, UpdatePlan, steps_from_payload

from test_resume import (  # noqa: F401  (repo and fake_model are fixtures)
    PATCH_A, _core, _wire, call, fake_model, finish, repo, turn)
from test_undo import _Typist

SECRET = "hunter2-correct-horse-battery-staple"


def plan(*steps: tuple[str, str], explanation: str | None = None) -> dict:
    args: dict = {"plan": [{"step": text, "status": status} for text, status in steps]}
    if explanation is not None:
        args["explanation"] = explanation
    return args


THREE = plan(("Reproduce the failure", "completed"), ("Fix add()", "in_progress"),
             ("Run the tests", "pending"))
STEPS = (PlanStep("Reproduce the failure", StepStatus.COMPLETED),
         PlanStep("Fix add()", StepStatus.IN_PROGRESS),
         PlanStep("Run the tests", StepStatus.PENDING))


def _session(tmp_path, script, *, redactor=None):
    runtime, session, repo = _core(tmp_path, script, redactor=redactor)
    events: list = []
    runtime.events.subscribe(events.append)
    return runtime, session, events


# --- what a plan may hold -------------------------------------------------------


def test_each_step_becomes_one_line_of_plain_text():
    op = UpdatePlan().validate(plan(("Fix\n  the\x1b[31m bug\u202e\t", "pending"),
                                    explanation="because\r\nof it"))
    assert op.args["steps"] == (PlanStep("Fix the[31m bug", StepStatus.PENDING),)
    assert op.args["explanation"] == "because of it"
    assert op.display == "update_plan: 1 step(s), 0 completed"
    assert UpdatePlan().validate(plan()).display == "update_plan: clear the plan"


@pytest.mark.parametrize("args, says", [
    ({}, "plan must be an array"),
    ({"plan": "read, then fix"}, "plan must be an array"),
    ({"plan": [], "steps": []}, "unknown argument(s): steps"),
    ({"plan": ["read the file"]}, "step 1 must be an object"),
    ({"plan": [{"step": "a", "status": "pending", "id": 1}]}, "step 1 has unknown field(s): id"),
    ({"plan": [{"step": " \n\t", "status": "pending"}]}, "step 1 needs a non-empty step"),
    ({"plan": [{"step": 3, "status": "pending"}]}, "step 1 needs a non-empty step"),
    ({"plan": [{"step": "a"}]}, "step 1 status must be one of pending, in_progress, completed"),
    ({"plan": [{"step": "a", "status": "done"}]}, "status must be one of"),
    (plan(("a", "in_progress"), ("b", "pending"), ("c", "in_progress")),
     "steps 1, 3 are all in_progress"),
    (plan(*[(f"s{n}", "pending") for n in range(MAX_STEPS + 1)]), "keep it to 20 or fewer"),
    (plan(("x" * 201, "pending")), "at most 200"),
    ({"plan": [], "explanation": 5}, "explanation must be a string"),
    ({"plan": [], "explanation": "y" * 501}, "keep it to 500"),
])
def test_a_plan_that_is_not_one_is_refused_with_the_reason(args, says):
    with pytest.raises(InvalidToolCall) as caught:
        UpdatePlan().validate(args)
    assert says in str(caught.value)


def test_twenty_steps_and_one_in_progress_are_fine():
    steps = [(f"step {n}", "completed") for n in range(MAX_STEPS - 1)] + [("last", "in_progress")]
    assert len(UpdatePlan().validate(plan(*steps)).args["steps"]) == MAX_STEPS


# --- the loop -----------------------------------------------------------------------


def test_the_user_sees_the_plan_the_journal_keeps_it_and_the_model_hears_counts(tmp_path):
    runtime, session, events = _session(tmp_path, [
        turn(call("u1", "update_plan", {**THREE, "explanation": "the test points at add()"})),
        turn(text="working on it")])
    runtime.run_turn("fix it")
    session.close()

    assert runtime.plan == STEPS and runtime.plan_explanation == "the test points at add()"
    kinds = [e.kind for e in events if getattr(e, "call_id", None) == "u1"]
    assert kinds == ["tool_begin", "plan_updated", "tool_end"]
    (shown,) = [e for e in events if isinstance(e, PlanUpdated)]
    assert shown.steps == STEPS and shown.explanation == "the test points at add()"

    (result,) = [r for r in SessionStore.read_records(session.path)
                 if r.type == EventType.TOOL_RESULT]
    assert result.payload["plan"] == {"explanation": "the test points at add()", "steps": [
        {"step": "Reproduce the failure", "status": "completed"},
        {"step": "Fix add()", "status": "in_progress"},
        {"step": "Run the tests", "status": "pending"}]}

    backend = runtime.backend
    assert "update_plan" in [tool.name for tool in backend.seen[0].tools]
    reply = backend.seen[1].messages[-1].blocks[0]
    assert reply.content == "Plan updated: 3 step(s), 1 completed, 1 in progress, 1 pending."
    assert not reply.is_error


def test_each_call_replaces_the_plan_and_an_empty_one_clears_it(tmp_path):
    runtime, session, events = _session(tmp_path, [
        turn(call("u1", "update_plan", THREE)),
        turn(call("u2", "update_plan", plan(("Only step", "in_progress")))),
        turn(call("u3", "update_plan", plan())),
        finish(),
    ])
    outcome = runtime.run_turn("fix it")
    session.close()

    shown = [e.steps for e in events if isinstance(e, PlanUpdated)]
    assert shown == [STEPS, (PlanStep("Only step", StepStatus.IN_PROGRESS),), ()]
    assert runtime.plan == ()
    assert runtime.backend.seen[3].messages[-1].blocks[0].content == "Plan cleared."
    assert "Plan steps" not in outcome.summary


def test_a_refused_plan_leaves_the_last_one_in_place(tmp_path):
    runtime, session, events = _session(tmp_path, [
        turn(call("u1", "update_plan", THREE)),
        turn(call("u2", "update_plan", plan(("a", "in_progress"), ("b", "in_progress")))),
        turn(text="ok")])
    runtime.run_turn("fix it")
    session.close()
    assert runtime.plan == STEPS
    assert len([e for e in events if isinstance(e, PlanUpdated)]) == 1
    assert "at most one step" in runtime.backend.seen[2].messages[-1].blocks[0].content


@pytest.mark.parametrize("mode", list(Mode))
def test_no_mode_asks_before_a_plan_changes(mode):
    policy = PolicyEngine(rules=list(builtin_rules()), mode=mode, interactive=False)
    decision, _ = policy.evaluate(UpdatePlan().validate(THREE))
    assert decision.verdict is Verdict.ALLOW


def test_plan_mode_can_lay_out_a_plan_but_still_not_edit(tmp_path):
    runtime, session, events = _session(tmp_path, [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("u1", "update_plan", plan(("Change A to 2", "pending"))),
             call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="Here is the plan.")])
    (tmp_path / "repo" / "a.py").write_text("A = 1\n", encoding="utf-8", newline="\n")
    runtime.policy.mode = Mode.PLAN
    runtime.run_turn("plan how to make A 2")
    session.close()
    assert runtime.plan == (PlanStep("Change A to 2", StepStatus.PENDING),)
    assert (tmp_path / "repo" / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert "plan mode" in runtime.backend.seen[2].messages[-1].blocks[0].content


# --- finish -------------------------------------------------------------------------


def test_finish_lists_unfinished_steps_and_decides_nothing_over_them(tmp_path):
    runtime, session, events = _session(tmp_path, [
        turn(call("u1", "update_plan", THREE)), finish()])
    outcome = runtime.run_turn("fix it")
    session.close()

    # An empty claims list is an honest completed; the open steps do not
    # downgrade it -- they are pointed out.
    assert outcome.status is TerminalStatus.COMPLETED
    assert ("Plan steps not marked completed:\n  - [in progress] Fix add()\n"
            "  - [pending] Run the tests") in outcome.summary
    assert "Reproduce the failure" not in outcome.summary
    assert any(isinstance(e, Notice) and e.level == "warning"
               and "2 plan step(s) not marked completed" in e.text for e in events)
    (ended,) = [r for r in SessionStore.read_records(session.path)
                if r.type == EventType.TERMINATION]
    assert "[pending] Run the tests" in ended.payload["summary"]


def test_a_blocked_finish_lists_them_without_a_warning(tmp_path):
    runtime, session, events = _session(tmp_path, [
        turn(call("u1", "update_plan", THREE)), finish(status="blocked")])
    outcome = runtime.run_turn("fix it")
    session.close()
    assert outcome.status is TerminalStatus.BLOCKED
    assert "[in progress] Fix add()" in outcome.summary
    assert not [e for e in events if isinstance(e, Notice) and "plan step" in e.text]


def test_a_finished_plan_adds_nothing(tmp_path):
    done = plan(("Reproduce the failure", "completed"), ("Fix add()", "completed"))
    runtime, session, events = _session(tmp_path, [
        turn(call("u1", "update_plan", done)), finish()])
    outcome = runtime.run_turn("fix it")
    session.close()
    assert "Plan steps" not in outcome.summary
    assert not [e for e in events if isinstance(e, Notice) and "plan step" in e.text]


# --- what reaches the user ---------------------------------------------------------


def test_a_secret_in_the_plan_is_scrubbed_everywhere(tmp_path):
    redactor = Redactor()
    redactor.register(SECRET)
    runtime, session, events = _session(tmp_path, [
        turn(call("u1", "update_plan", plan((f"Use the key {SECRET}", "in_progress"),
                                            explanation=f"key {SECRET}"))),
        finish(status="blocked")], redactor=redactor)
    runtime.events.redactor = redactor
    runtime.run_turn("fix it")
    session.close()

    (shown,) = [e for e in events if isinstance(e, PlanUpdated)]
    assert SECRET not in repr(shown) and "[redacted]" in shown.steps[0].step
    assert shown.steps[0].status is StepStatus.IN_PROGRESS
    assert SECRET not in "".join(repr(e) for e in events)
    assert SECRET not in session.path.read_text(encoding="utf-8")


def test_scrubbing_nested_text_keeps_every_enum_an_enum():
    redactor = Redactor()
    redactor.register(SECRET)
    received: list = []
    sink = EventSink(redactor=redactor)
    sink.subscribe(received.append)
    sink.emit(ApprovalRequest(request_id="r", approval_kind=ApprovalKind.COMMAND,
                              tool="run_command", display=f"echo {SECRET}", reason="x"))
    sink.emit(Termination(TerminalStatus.PARTIAL, f"saw {SECRET}"))
    request, ended = received
    assert SECRET not in request.display and SECRET not in ended.reason
    assert all(type(o) is ApprovalChoice for o in request.options)
    assert type(ended.status) is TerminalStatus and type(request.approval_kind) is ApprovalKind


def test_the_terminal_draws_a_checklist_of_plain_text():
    out = io.StringIO()
    renderer = Renderer(console=Console(file=out, width=100))
    hostile = (PlanStep("Read [bold]x[/bold] in C:\\dir\\", StepStatus.COMPLETED),
               *STEPS[1:])
    renderer.handle(ToolBegin("u1", "update_plan", "update_plan: 3 step(s), 1 completed"))
    renderer.handle(PlanUpdated("u1", hostile, "why [red]"))
    renderer.handle(ToolEnd("u1", "update_plan", True, "Plan updated"))
    renderer.handle(PlanUpdated("u2", ()))
    shown = out.getvalue()
    assert shown.splitlines() == [
        "plan 1/3 done  why [red]",
        "  [x] Read [bold]x[/bold] in C:\\dir\\",
        "  [>] Fix add()",
        "  [ ] Run the tests",
        "plan cleared",
    ]


def test_exec_json_streams_the_plan_as_data():
    payload = _event_payload(PlanUpdated("u1", STEPS, "why"))
    assert json.loads(json.dumps(payload)) == {
        "kind": "plan_updated", "call_id": "u1", "explanation": "why", "steps": [
            {"step": "Reproduce the failure", "status": "completed"},
            {"step": "Fix add()", "status": "in_progress"},
            {"step": "Run the tests", "status": "pending"}]}


def test_exec_json_through_main(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import tool_turn

    httpd, handler = fake_model
    host, port = httpd.server_address
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    handler.turns, handler.index = [
        tool_turn("update_plan", THREE, "u1"),
        tool_turn("finish", {"status": "completed", "summary": "A is 1", "claims": []}, "f1"),
    ], 0
    # Unattended: dont_ask denies whatever is not allowed, and a plan still is.
    assert app.main(["exec", "what is A?", "--json", "--workspace", str(repo)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    (shown,) = [e for e in events if e["kind"] == "plan_updated"]
    assert shown["call_id"] == "u1" and [s["status"] for s in shown["steps"]] == [
        "completed", "in_progress", "pending"]
    assert not [e for e in events if e["kind"] == "tool_rejected"]
    (ended,) = [e for e in events if e["kind"] == "termination"]
    assert ended["status"] == "completed" and "[pending] Run the tests" in ended["summary"]
    sent = httpd.requests[0]
    assert "update_plan" in [t["function"]["name"] for t in sent["tools"]]


def test_the_prompt_tells_the_model_how_to_use_it():
    assert "`update_plan`" in base_system_prompt()
    assert "update_plan" in permissions_paragraph(PolicyEngine(mode=Mode.PLAN))


# --- the REPL and resume -----------------------------------------------------------


def test_slash_plan_shows_the_plan(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("u1", "update_plan", THREE)), turn(text="working on it")])
    typist = _Typist(["/plan", "fix it", "/plan", "/exit"])
    _close(wiring, repl(wiring, typist), "session ended")
    first = typist.printed.index(next(p for p in typist.printed if "no plan" in p))
    later = typist.printed[first + 1:]
    assert any("1/3 done" in line for line in later)
    assert any("Fix add()" in line for line in later)


def test_a_resumed_session_shows_its_plan_again(repo, tmp_path):
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(call("u1", "update_plan", THREE)), turn(text="pausing")])
    first.runtime.run_turn("fix it")
    _close(first, TerminalStatus.CANCELLED, "session ended")

    resumed = _wire(repo, home, [finish()], resume="last")
    events: list = []
    resumed.runtime.events.subscribe(events.append)
    _announce_resume(resumed)
    assert resumed.runtime.plan == STEPS
    (shown,) = [e for e in events if isinstance(e, PlanUpdated)]
    assert shown.call_id == "" and shown.steps == STEPS

    outcome = resumed.runtime.run_turn(with_note(resumed, "carry on"))
    _close(resumed, outcome.status, "session ended")
    assert "[pending] Run the tests" in outcome.summary


def test_the_newest_recorded_plan_wins_and_a_damaged_one_reads_as_none(tmp_path):
    runtime, session, _ = _session(tmp_path, [
        turn(call("u1", "update_plan", THREE)),
        turn(call("u2", "update_plan", plan(("Second", "pending")))),
        turn(text="ok")])
    runtime.run_turn("fix it")
    assert read_journal(session.path).plan == (PlanStep("Second", StepStatus.PENDING),)
    session.append(EventType.TOOL_RESULT, {"call_id": "x", "tool": "update_plan",
                                           "plan": "not a plan"})
    session.close()
    state = read_journal(session.path)
    assert state.plan == () and state.plan_explanation == ""


def test_a_recorded_step_is_read_back_only_if_it_reads_as_one():
    assert steps_from_payload(None) == ((), "")
    assert steps_from_payload({"steps": "x", "explanation": "e"}) == ((), "e")
    steps, explanation = steps_from_payload({"explanation": 7, "steps": [
        {"step": "a\nb\x1b", "status": "pending"}, {"step": "", "status": "pending"},
        {"step": "x", "status": "bogus"}, 5, {"status": "completed"},
        {"step": "z" * 300, "status": "completed"}]})
    assert steps == (PlanStep("a b", StepStatus.PENDING),
                     PlanStep("z" * 200, StepStatus.COMPLETED))
    assert explanation == ""
    many, _ = steps_from_payload({"steps": [{"step": f"s{n}", "status": "pending"}
                                            for n in range(MAX_STEPS + 5)]})
    assert len(many) == MAX_STEPS and many[-1].step == f"s{MAX_STEPS - 1}"
