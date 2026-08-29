"""Policy decision table.

The four cases in requirements section 4.1 that the design review flagged --
accept_edits allowing a clean patch, accept_edits still asking on a dirty file,
a persisted allow firing for run_command, dont_ask failing closed -- are each
asserted here, because each of them breaks if the built-in defaults are placed
at the wrong pipeline step.
"""

from __future__ import annotations

import pytest

from foundry.core.policy.engine import (
    Decision,
    Layer,
    Mode,
    PolicyEngine,
    Rule,
    Verdict,
)
from foundry.core.tools.base import Operation, ToolKind


def read_op(path: str = "src/app.py") -> Operation:
    return Operation(tool="read_file", kind=ToolKind.READ_ONLY,
                     args={"path": path}, display=f"read {path}", target=path)


def patch_op(*paths: str) -> Operation:
    paths = paths or ("src/app.py",)
    return Operation(tool="apply_patch", kind=ToolKind.MUTATOR,
                     args={"paths": list(paths)}, display="apply_patch",
                     target=" ".join(paths), digest="abc123")


def cmd_op(command: str) -> Operation:
    return Operation(tool="run_command", kind=ToolKind.MUTATOR,
                     args={"command": command}, display=command, target=command)


def verdict(engine: PolicyEngine, op: Operation) -> Verdict:
    return engine.evaluate(op)[0].verdict


# --- built-in defaults ---------------------------------------------------


def test_read_only_tools_are_allowed_by_builtin_rule():
    engine = PolicyEngine()
    decision, _ = engine.evaluate(read_op())
    assert decision.verdict is Verdict.ALLOW
    assert decision.step == 5


def test_mutator_falls_through_to_interactive_approval():
    engine = PolicyEngine()
    decision, _ = engine.evaluate(patch_op())
    assert decision.verdict is Verdict.ASK
    assert decision.step == 6


# --- the four review cases ----------------------------------------------


def test_accept_edits_allows_a_clean_file_patch():
    engine = PolicyEngine(mode=Mode.ACCEPT_EDITS)
    assert verdict(engine, patch_op()) is Verdict.ALLOW


def test_accept_edits_still_asks_for_a_dirty_file():
    """The dirty guard is a step-3 rule, so it outranks the step-4 mode."""
    engine = PolicyEngine(mode=Mode.ACCEPT_EDITS, dirty_files={"src/app.py"})
    decision, _ = engine.evaluate(patch_op("src/app.py"))
    assert decision.verdict is Verdict.ASK
    assert decision.step == 3
    assert "uncommitted changes" in decision.reason


def test_persisted_allow_rule_fires_for_run_command():
    """Would be dead if mutators carried a blanket step-3 ASK rule."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="pytest*", verdict=Verdict.ALLOW,
                         layer=Layer.USER, rule_id="user.pytest"))
    decision, _ = engine.evaluate(cmd_op("pytest -q"))
    assert decision.verdict is Verdict.ALLOW
    assert decision.rule_id == "user.pytest"


def test_dont_ask_fails_closed():
    engine = PolicyEngine(mode=Mode.DONT_ASK)
    decision, _ = engine.evaluate(patch_op())
    assert decision.verdict is Verdict.DENY
    assert decision.step == 6


def test_non_interactive_fails_closed():
    engine = PolicyEngine(interactive=False)
    assert verdict(engine, cmd_op("pytest -q")) is Verdict.DENY


# --- precedence law ------------------------------------------------------


def test_deny_beats_allow_regardless_of_layer_order():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW,
                         layer=Layer.USER))
    engine.add_rule(Rule(tool="run_command", pattern="*rm*", verdict=Verdict.DENY,
                         layer=Layer.MANAGED, reason="managed policy"))
    assert verdict(engine, cmd_op("rm file.txt")) is Verdict.DENY


def test_managed_deny_survives_session_grant():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.DENY,
                         layer=Layer.MANAGED, reason="managed"))
    op = cmd_op("pytest -q")
    engine.grant_for_session(op)
    assert verdict(engine, op) is Verdict.DENY


def test_ask_beats_allow():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    engine.add_rule(Rule(tool="run_command", pattern="git*", verdict=Verdict.ASK))
    assert verdict(engine, cmd_op("git log")) is Verdict.ASK


# --- circuit breaker -----------------------------------------------------


@pytest.mark.parametrize("command", [
    "git checkout -- .",
    "git reset --hard HEAD",
    "git clean -fd",
    "git stash drop",
    "git stash clear",
    "git restore src/app.py",
    "git push origin main",
    "git commit -m x",
])
def test_breaker_blocks_destructive_git(command):
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="*", pattern="*", verdict=Verdict.ALLOW))  # try to override
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0


@pytest.mark.parametrize("command", [
    "rm -r C:/Windows",
    "Remove-Item -Recurse C:/Users",
    "del /s C:/Windows",
])
def test_breaker_blocks_recursive_system_delete(command):
    engine = PolicyEngine(mode=Mode.ACCEPT_EDITS)
    engine.add_rule(Rule(tool="*", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0


def test_breaker_blocks_writes_into_git_directory():
    engine = PolicyEngine(mode=Mode.ACCEPT_EDITS)
    decision, _ = engine.evaluate(patch_op(".git/hooks/pre-commit"))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0


def test_breaker_blocks_writes_into_workspace_foundry_config():
    """Closes the self-privilege-escalation path the review found."""
    engine = PolicyEngine(mode=Mode.ACCEPT_EDITS)
    decision, _ = engine.evaluate(patch_op(".foundry/settings.toml"))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0


# --- unparseable commands ------------------------------------------------


@pytest.mark.parametrize("command", [
    "pytest $(whoami)",
    "pytest > out.txt",
    "iex $payload",
])
def test_unparseable_command_is_never_auto_allowed(command):
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.ASK
    assert decision.rule_id == "builtin.unparseable"


# --- plan mode -----------------------------------------------------------


def test_plan_mode_denies_mutators_but_allows_reads():
    engine = PolicyEngine(mode=Mode.PLAN)
    assert verdict(engine, patch_op()) is Verdict.DENY
    assert verdict(engine, read_op()) is Verdict.ALLOW


# --- pre_tool hook -------------------------------------------------------


def test_hook_allow_does_not_skip_a_deny_rule():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.DENY,
                         reason="blocked"))
    engine.pre_tool = lambda op: Decision(Verdict.ALLOW, "hook says fine")
    assert verdict(engine, cmd_op("pytest")) is Verdict.DENY


def test_hook_deny_wins():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    engine.pre_tool = lambda op: Decision(Verdict.DENY, "hook blocked it")
    assert verdict(engine, cmd_op("pytest")) is Verdict.DENY


def test_hook_rewrite_reenters_the_breaker():
    """A rewrite must not smuggle a forbidden operation past step 0."""
    engine = PolicyEngine()
    engine.pre_tool = lambda op: cmd_op("git reset --hard")
    decision, final = engine.evaluate(cmd_op("pytest -q"))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0
    assert final.target == "git reset --hard"


# --- audit fields --------------------------------------------------------


def test_decision_records_audit_fields():
    engine = PolicyEngine()
    decision, _ = engine.evaluate(read_op())
    assert decision.policy_digest
    assert decision.operation_digest
    assert decision.rule_id


def test_dead_rules_are_detectable():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="no_such_tool", pattern="*", verdict=Verdict.DENY))
    dead = engine.dead_rules(known_tools=["read_file", "run_command"])
    assert len(dead) == 1
