"""The user's check, run as the model finishes (D-213): exec --verify, or
runtime.verify_command. A finish with status completed is checked by Foundry
itself; while the check fails the finish is refused and the model is shown
why, and after VERIFY_ATTEMPTS failed checks -- or on a last request, with
nothing left to fix it in -- the task ends partial."""

from __future__ import annotations

import argparse

import pytest

from foundry.cli.app import build_parser
from foundry.cli.wiring import _config_args, build
from foundry.core.config import load_config
from foundry.core.errors import ConfigError
from foundry.core.events import TerminalStatus
from foundry.core.policy.engine import Mode
from foundry.core.runtime_ending import VERIFY_ATTEMPTS, VERIFY_FAILED, verify_problem
from foundry.core.session import EventType
from foundry.core.tools.base import ToolOutput
from foundry.core.tools.finish import Finish
from foundry.core.winapi import IS_WINDOWS
from test_hooks import (  # noqa: F401  (fixtures)
    OVERRIDES,
    _notices,
    _records,
    _result,
    _wire,
    call,
    home,
    machine,
    repo,
    turn,
)
from test_resume import finish

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="run_command runs PowerShell")

CHECK = "python check.py"
# Passes once a.py says A = 2; says what it found either way.
CHECK_SCRIPT = (
    "import sys\n"
    "text = open('a.py', encoding='utf-8').read().strip()\n"
    "print('found:', text)\n"
    "sys.exit(0 if text == 'A = 2' else 1)\n"
)
FIX = ("*** Begin Patch\n*** Update File: a.py\n@@\n-A = 1\n+A = 2\n*** End Patch\n")


@pytest.fixture()
def checked(repo):
    (repo / "check.py").write_text(CHECK_SCRIPT, encoding="utf-8")
    return repo


def _verified(repo, home, turns, **overrides):
    return _wire(repo, home, turns, overrides={"verify_command": CHECK, **overrides})


def _ended(wiring) -> dict:
    return _records(wiring, EventType.TERMINATION)[-1]


def test_a_check_that_passes_lets_the_finish_stand(checked, home):
    (checked / "a.py").write_text("A = 2\n", encoding="utf-8")
    wiring = _verified(checked, home, [finish()])
    outcome = wiring.runtime.run_turn("set A to 2")
    assert outcome.status is TerminalStatus.COMPLETED
    assert "Checked by Foundry as the task ended: python check.py exited 0." in outcome.text
    assert len(wiring.runtime.backend.seen) == 1
    (record,) = _records(wiring, EventType.VERIFY)
    assert record == {"command": CHECK, "attempt": 1, "outcome": "passed",
                      "finish": "accepted", "event_ordinal": record["event_ordinal"]}
    # Its run is a command of the journal's, as the model's are.
    ran = _records(wiring, EventType.COMMAND_EXEC)
    assert record["event_ordinal"] > 0 and len(ran) == 1
    assert "your check passed: python check.py" in _notices(wiring)


def test_a_failed_check_refuses_the_finish_and_the_model_goes_on(checked, home):
    wiring = _verified(checked, home, [
        finish("f1"),
        turn(call("r1", "read_file", {"path": "a.py"}),
             call("p1", "apply_patch", {"patch": FIX})),
        finish("f2"),
    ])
    outcome = wiring.runtime.run_turn("set A to 2")
    assert outcome.status is TerminalStatus.COMPLETED
    refused = _result(wiring, "f1")
    assert refused.is_error
    assert refused.content.startswith(
        "finish was not accepted: the user's check, `python check.py`, ran as you finished "
        f"and failed (exit code 1) -- check 1 of {VERIFY_ATTEMPTS}.")
    # What it printed, and the run's id, as a run_command result shows them.
    assert "$ python check.py" in refused.content and "found: A = 1" in refused.content
    assert "[event_id=" in refused.content
    assert [(r["attempt"], r["outcome"], r["finish"])
            for r in _records(wiring, EventType.VERIFY)] == [
        (1, "exit code 1", "refused"), (2, "passed", "accepted")]
    assert len(_records(wiring, EventType.TERMINATION)) == 1
    assert any("the model is told and goes on" in n for n in _notices(wiring))


def test_a_check_that_never_passes_ends_the_task_partial(checked, home):
    wiring = _verified(checked, home, [finish(f"f{i}") for i in range(1, VERIFY_ATTEMPTS + 2)])
    outcome = wiring.runtime.run_turn("set A to 2")
    assert outcome.status is TerminalStatus.PARTIAL
    assert _ended(wiring)["reason"] == VERIFY_FAILED
    # The last check's finish ends the task: no request after it.
    assert len(wiring.runtime.backend.seen) == VERIFY_ATTEMPTS
    assert [r["finish"] for r in _records(wiring, EventType.VERIFY)] == (
        ["refused"] * (VERIFY_ATTEMPTS - 1) + ["partial"])
    assert ("Rejected claims:\n  - the user's check, python check.py, failed as the model "
            f"finished (exit code 1; check {VERIFY_ATTEMPTS} of {VERIFY_ATTEMPTS})"
            in outcome.text)
    assert "Checked by Foundry" not in outcome.text


def test_a_finish_that_is_not_completed_is_not_checked(checked, home):
    wiring = _verified(checked, home, [finish(status="partial")])
    outcome = wiring.runtime.run_turn("set A to 2")
    assert outcome.status is TerminalStatus.PARTIAL
    assert _records(wiring, EventType.VERIFY) == []
    assert _records(wiring, EventType.COMMAND_EXEC) == []


def test_on_the_last_request_a_failed_check_ends_the_task_at_once(checked, home):
    wiring = _verified(checked, home, [turn(call("r1", "read_file", {"path": "a.py"})),
                                       finish("f1"), finish("f2")],
                       max_tool_rounds=1)
    outcome = wiring.runtime.run_turn("set A to 2")
    assert outcome.status is TerminalStatus.PARTIAL
    assert _ended(wiring)["reason"] == VERIFY_FAILED
    assert [(r["attempt"], r["finish"]) for r in _records(wiring, EventType.VERIFY)] == [
        (1, "partial")]
    assert len(wiring.runtime.backend.seen) == 2


def test_each_task_has_its_own_checks(checked, home):
    wiring = _verified(checked, home, [finish("f1"), finish("f2"), finish("f3"),
                                       finish("f4")])
    first = wiring.runtime.run_turn("set A to 2")
    assert first.status is TerminalStatus.PARTIAL
    (checked / "a.py").write_text("A = 2\n", encoding="utf-8")
    second = wiring.runtime.run_turn("again")
    assert second.status is TerminalStatus.COMPLETED
    assert "Rejected claims" not in second.text
    assert [r["attempt"] for r in _records(wiring, EventType.VERIFY)] == [1, 2, 3, 1]


@pytest.mark.parametrize("meta, how", [
    # Killed at its limit, whatever exit code the kill left.
    ({"exit_code": 0, "timed_out": True, "timeout_s": 7}, "timed out after 7s"),
    ({"exit_code": 0, "output_incomplete": True}, "its output could not all be read"),
    ({"exit_code": None}, "it could not be run"),
])
def test_a_check_that_did_not_end_cleanly_fails(checked, home, monkeypatch, meta, how):
    wiring = _verified(checked, home, [finish("f1"), finish("f2", status="partial")])
    tool = wiring.runtime.registry.get("run_command")
    monkeypatch.setattr(type(tool), "execute", lambda self, op, ctx: ToolOutput(
        content="$ python check.py\n...", is_error=True, metadata=meta))
    wiring.runtime.run_turn("set A to 2")
    assert f"failed ({how})" in _result(wiring, "f1").content
    assert _records(wiring, EventType.VERIFY)[0]["outcome"] == how


def test_the_check_runs_up_to_the_command_ceiling(checked, home, monkeypatch):
    wiring = _verified(checked, home, [finish("f1")], command_timeout_s=45)
    tool = wiring.runtime.registry.get("run_command")
    seen = []

    def execute(self, op, ctx):
        seen.append(op.args)
        return ToolOutput(content="ok", metadata={"exit_code": 0, "event_ordinal": 3})

    monkeypatch.setattr(type(tool), "execute", execute)
    assert wiring.runtime.run_turn("go").status is TerminalStatus.COMPLETED
    assert seen == [{"command": CHECK, "cwd": ".", "timeout_s": 45}]


def test_a_cancel_during_the_check_cancels_the_task(checked, home, monkeypatch):
    wiring = _verified(checked, home, [finish("f1"), finish("f2")])
    runtime = wiring.runtime
    tool = runtime.registry.get("run_command")

    def execute(self, op, ctx):
        runtime.cancel()
        return ToolOutput(content="stopped", metadata={"exit_code": 0})

    monkeypatch.setattr(type(tool), "execute", execute)
    # Not completed on a check that was stopped, whatever it returned.
    assert runtime.run_turn("go").status is TerminalStatus.CANCELLED
    assert len(runtime.backend.seen) == 1
    assert _ended(wiring)["reason"] == "interrupted by user"


def test_a_run_that_changes_nothing_is_not_checked(checked, home):
    wiring = _verified(checked, home, [finish("f1")], mode=Mode.PLAN)
    assert wiring.runtime.run_turn("plan it").status is TerminalStatus.COMPLETED
    assert _records(wiring, EventType.VERIFY) == []
    review = build(checked, home=home, review=True,
                   overrides={**OVERRIDES, "verify_command": CHECK})
    assert review.runtime.verify_command == ""
    assert "Foundry runs the user's check" not in (
        review.runtime.registry.get("finish").schema().description)
    read_only = _verified(checked, home, [finish("f1")])
    read_only.runtime.policy.read_only = "a review changes nothing"
    assert read_only.runtime.run_turn("go").status is TerminalStatus.COMPLETED
    assert _records(read_only, EventType.VERIFY) == []


def test_the_model_is_told_of_the_check_before_it_finishes(checked, home):
    wiring = _verified(checked, home, [])
    description = wiring.runtime.registry.get("finish").schema().description
    assert description.endswith(
        "When you finish with status completed, Foundry runs the user's check, "
        "`python check.py`, itself: if it fails, the finish is refused and you are shown "
        "what it printed. Run it yourself before you finish.")
    assert wiring.runtime.verify_command == CHECK
    assert "user's check" not in Finish().schema().description
    unchecked = _wire(checked, home, [])
    assert unchecked.runtime.verify_command == ""
    assert "user's check" not in unchecked.runtime.registry.get("finish").schema().description


def test_the_note_goes_with_a_result_schema(checked, home):
    tool = Finish(verify_command=CHECK, result_schema={"type": "object"})
    schema = tool.schema()
    assert "Foundry runs the user's check" in schema.description
    assert "This run asks for a result" in schema.description
    assert "result" in schema.parameters["properties"]


def test_a_list_of_commands_cannot_be_the_check(checked, home):
    assert verify_problem(CHECK) == ""
    assert "only the last one's exit code survives" in verify_problem(
        "python a.py; python b.py")
    with pytest.raises(ConfigError, match="verify_command 'ruff check; pytest': it runs "
                                          "several commands"):
        _verified(checked, home, [], verify_command="ruff check; pytest")


def test_only_the_users_own_settings_name_the_check(tmp_path, home, checked):
    (home / "config.toml").write_text('[runtime]\nverify_command = " python check.py "\n',
                                      encoding="utf-8")
    assert load_config(None, home=home).verify_command == CHECK
    flagged = load_config(None, home=home,
                          settings={"runtime": {"verify_command": "pytest -q"}})
    assert flagged.verify_command == "pytest -q"
    # "" turns off one the user's file names.
    assert load_config(None, home=home,
                       settings={"runtime": {"verify_command": ""}}).verify_command == ""
    with pytest.raises(ConfigError, match="the client's config may not set verify_command"):
        load_config(None, home=home, client_settings={"runtime": {"verify_command": "x"}})
    with pytest.raises(ConfigError, match="verify_command must be a command as a string"):
        load_config(None, home=home, settings={"runtime": {"verify_command": 3}})
    (checked / ".foundry").mkdir()
    (checked / ".foundry" / "config.toml").write_text(
        '[runtime]\nverify_command = "python evil.py"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not set verify_command"):
        load_config(checked, home=home)


def test_exec_takes_the_check_as_verify():
    args = build_parser().parse_args(["exec", "--verify", "python -m pytest -q", "fix it"])
    assert args.verify == "python -m pytest -q"
    assert _config_args(args)["settings"]["runtime"] == {"verify_command": "python -m pytest -q"}
    plain = build_parser().parse_args(["exec", "fix it"])
    assert "runtime" not in _config_args(plain)["settings"]
    assert "runtime" not in _config_args(argparse.Namespace(
        model=None, mode=None, config=None, profile=None))["settings"]
