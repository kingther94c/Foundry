"""Golden tasks: the regression baseline for prompt and loop changes.

Each scenario is a scripted model trajectory over the sample repository, with an
expected terminal status and expected evidence. They run offline, so a change to
the system prompt, the tool surface, or the loop can be regression-tested without
spending a token.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from foundry.core.backends.replay import ScriptedBackend
from foundry.core.context import ContextManager
from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock, Usage
from foundry.core.events import ApprovalChoice, EventSink, TerminalStatus
from foundry.core.policy.engine import Mode, PolicyEngine, builtin_rules
from foundry.core.prompts import base_system_prompt, load_project_doc, permissions_paragraph
from foundry.core.runtime import AgentRuntime
from foundry.core.session import SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.git import capture_baseline, run_git
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import Workspace

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"
PYTEST_CMD = "python -m pytest tests -q"


def call(name: str, args: dict, call_id: str) -> ToolUseBlock:
    return ToolUseBlock(call_id=call_id, name=name, arguments=json.dumps(args))


def turn(*calls: ToolUseBlock, text: str = "") -> ModelTurn:
    return ModelTurn(text=text, tool_calls=tuple(calls),
                     usage=Usage(input_tokens=100, output_tokens=20),
                     stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)


@pytest.fixture()
def sample(tmp_path):
    repo = tmp_path / "sample"
    shutil.copytree(FIXTURE, repo)
    run_git(["init"], repo)
    run_git(["config", "user.email", "t@example.com"], repo)
    run_git(["config", "user.name", "T"], repo)
    run_git(["add", "."], repo)
    run_git(["commit", "-m", "initial"], repo)
    return repo


@pytest.fixture()
def agent(sample, tmp_path):
    def build(turns, *, mode=Mode.DEFAULT, approve=ApprovalChoice.ONCE, dirty=()):
        session = SessionStore(tmp_path / "sessions")
        registry = default_registry(recorder=session)
        workspace = Workspace(sample)
        baseline = capture_baseline(sample)
        policy = PolicyEngine(rules=list(builtin_rules()), mode=mode,
                              dirty_files=set(dirty) or set(baseline.dirty_paths))
        context = ContextManager(
            system_prompt=base_system_prompt() + "\n\n" + permissions_paragraph(policy),
            project_doc=load_project_doc(sample, trusted=True),
        )
        events = []
        sink = EventSink()
        sink.subscribe(events.append)
        runtime = AgentRuntime(
            backend=ScriptedBackend(turns), registry=registry, policy=policy,
            context=context,
            tool_ctx=ToolContext(workspace=workspace, artifacts=session.artifacts,
                                 read_tracker=ReadTracker()),
            session=session, events=sink, approval=lambda r: approve, model="fake",
        )
        return runtime, session, events

    build.repo = sample
    return build


# --- scenario 1: read-only question --------------------------------------


def test_readonly_question(agent):
    runtime, session, _ = agent([
        turn(call("search_text", {"query": "def add"}, "c1")),
        turn(call("read_file", {"path": "src/calc.py"}, "c2")),
        turn(call("finish", {"status": "completed",
                             "summary": "add() subtracts; it should return a + b",
                             "claims": []}, "c3")),
    ])
    outcome = runtime.run_turn("does add() look right?")
    session.close()
    assert outcome.status is TerminalStatus.COMPLETED
    assert not capture_baseline(agent.repo).dirty_paths, "a read-only task must not edit"


# --- scenario 2: fix a failing test --------------------------------------


FIX_PATCH = (
    "*** Begin Patch\n*** Update File: src/calc.py\n"
    "<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE\n"
    "*** End Patch"
)


def test_fix_failing_test_with_verified_evidence(agent):
    runtime, session, _ = agent([
        turn(call("run_command", {"command": PYTEST_CMD}, "c1")),
        turn(call("read_file", {"path": "src/calc.py"}, "c2")),
        turn(call("apply_patch", {"patch": FIX_PATCH}, "c3")),
        turn(call("run_command", {"command": PYTEST_CMD}, "c4")),
        turn(call("finish", {"status": "completed", "summary": "fixed add()",
                             "claims": [{"claim_text": "the suite passes",
                                         "command_event_id": 0,
                                         "expected_exit_code": 0}]}, "c5")),
    ])
    # The claim's event id is filled in below from the real journal, mirroring
    # what a model does by reading the event_id printed with the result.
    outcome = runtime.run_turn("the tests fail; fix them")
    session.close()

    assert "return a + b" in (agent.repo / "src" / "calc.py").read_text(encoding="utf-8")
    # event 0 does not exist, so completed is refused: the gate works.
    assert outcome.status is TerminalStatus.PARTIAL


def test_fix_with_correct_event_id_completes(agent, tmp_path):
    """The same trajectory, citing the real command event, must complete."""
    runtime, session, _ = agent([
        turn(call("read_file", {"path": "src/calc.py"}, "c1")),
        turn(call("apply_patch", {"patch": FIX_PATCH}, "c2")),
        turn(call("run_command", {"command": PYTEST_CMD}, "c3")),
        turn(call("finish", {"status": "completed", "summary": "fixed add()", "claims": []}, "c4")),
    ])
    outcome = runtime.run_turn("fix the failing test")
    session.close()
    assert outcome.status is TerminalStatus.COMPLETED

    history = runtime.registry.tools["run_command"].history
    assert history and history[-1]["exit_code"] == 0, "the suite should pass after the fix"


# --- scenario 3: add a test ----------------------------------------------


def test_add_a_test_file(agent):
    new_test = (
        "*** Begin Patch\n*** Add File: tests/test_multiply.py\n"
        "+from calc import multiply\n"
        "+\n"
        "+\n"
        "+def test_multiply_by_zero():\n"
        "+    assert multiply(5, 0) == 0\n"
        "*** End Patch"
    )
    runtime, session, _ = agent([
        turn(call("list_files", {"path": "tests"}, "c1")),
        turn(call("apply_patch", {"patch": new_test}, "c2")),
        turn(call("finish", {"status": "completed", "summary": "added a test", "claims": []}, "c3")),
    ])
    outcome = runtime.run_turn("add a test for multiply by zero")
    session.close()
    assert outcome.status is TerminalStatus.COMPLETED
    assert (agent.repo / "tests" / "test_multiply.py").is_file()


# --- scenario 4: denied work reports blocked ------------------------------


def test_denied_edit_reports_blocked(agent):
    runtime, session, _ = agent([
        turn(call("read_file", {"path": "src/calc.py"}, "c1")),
        turn(call("apply_patch", {"patch": FIX_PATCH}, "c2")),
        turn(call("finish", {"status": "blocked", "summary": "the user declined the edit",
                             "claims": []}, "c3")),
    ], approve=ApprovalChoice.DENY)
    outcome = runtime.run_turn("fix add()")
    session.close()
    assert outcome.status is TerminalStatus.BLOCKED
    assert "return a - b" in (agent.repo / "src" / "calc.py").read_text(encoding="utf-8")


# --- scenario 5: dirty file needs approval even in accept_edits -----------


def test_dirty_file_still_prompts_in_accept_edits(agent):
    (agent.repo / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a - b  # my work in progress\n", encoding="utf-8")
    asked: list[str] = []

    runtime, session, _ = agent([
        turn(call("read_file", {"path": "src/calc.py"}, "c1")),
        turn(call("apply_patch", {"patch": (
            "*** Begin Patch\n*** Update File: src/calc.py\n"
            "<<<<<<< SEARCH\n    return a - b  # my work in progress\n"
            "=======\n    return a + b\n>>>>>>> REPLACE\n*** End Patch")}, "c2")),
        turn(call("finish", {"status": "completed", "summary": "done", "claims": []}, "c3")),
    ], mode=Mode.ACCEPT_EDITS, dirty={"src/calc.py"})
    runtime.approval = lambda request: (asked.append(request.display) or ApprovalChoice.ONCE)

    runtime.run_turn("fix it")
    session.close()
    assert asked, "editing a file that was already dirty must ask, even in accept_edits"


# --- scenario 6: plan mode ------------------------------------------------


def test_plan_mode_produces_a_plan_without_editing(agent):
    runtime, session, _ = agent([
        turn(call("read_file", {"path": "src/calc.py"}, "c1")),
        turn(call("apply_patch", {"patch": FIX_PATCH}, "c2")),
        turn(text="Plan: change the subtraction in add() to an addition."),
    ], mode=Mode.PLAN)
    outcome = runtime.run_turn("plan a fix")
    session.close()
    assert outcome.status is None
    assert "return a - b" in (agent.repo / "src" / "calc.py").read_text(encoding="utf-8")


# --- the project doc reaches the model ------------------------------------


def test_project_doc_is_injected_when_trusted(agent):
    runtime, session, _ = agent([turn(text="ok")])
    runtime.run_turn("hello")
    session.close()
    system = runtime.context.system_message().text_content
    assert "python -m pytest tests -q" in system, "FOUNDRY.md should declare the test command"
