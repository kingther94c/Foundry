"""A structural invariant, rather than one test per bypass found.

Twice now a fix has converted a categorical breaker DENY into an approvable
ASK -- once by returning early on an unreadable command head, once on the
pre-split untrusted scan. Both were caught by review, one round apart, and both
would have been caught here.

The invariant: if a command is forbidden on its own, no wrapper, suffix, or
unparseable neighbour makes it approvable. These tests generate the
combinations instead of enumerating the ones someone happened to think of.
"""

from __future__ import annotations

import pytest

from foundry.core.policy.engine import Mode, PolicyEngine, Rule, Verdict, check_breaker
from foundry.core.tools.base import Operation, ToolKind

# One representative of every breaker rule.
FORBIDDEN = [
    "git reset --hard HEAD",
    "git --attr-source HEAD reset --hard",   # an option taking a separate value
    "git -C . clean -fdx",
    "git clean -fdx",
    "git checkout -- .",
    "git restore src/app.py",
    "git stash drop",
    "git stash clear",
    "git switch --discard-changes main",
    "git push origin main",
    "git commit -am wip",
    "git rebase main",
    "git rm -f -r .",
    "git branch -D main",
    "git worktree remove --force wt",
    "git update-ref -d HEAD",
    "git reflog expire --all",
    "git filter-branch --all",
    "git read-tree --reset -u HEAD",
    "git checkout-index -f -a",
    "git merge feature",
    # The system prompt promised the model that merge was always refused while
    # these four -- pull being literally that merge -- walked straight past the
    # table.
    "git pull",
    "git cherry-pick abc123",
    "git revert abc123",
    "git am patch.mbox",
    "git apply patch.diff",
    "Remove-Item -Recurse -Force C:\\Windows",
    "rm -r ~",
]

# Ways to dress a command up. None may weaken the verdict.
DECORATIONS = [
    "{cmd}",
    "{cmd}; (unparseable)",
    "(unparseable); {cmd}",
    "{cmd}; cmd /c echo hi",
    "echo before; {cmd}",
    "{cmd}; echo after",
    "{cmd}\recho after",                       # CR separator
    "echo hi # a comment\n{cmd}",              # comment-terminated statement
    "echo hi # don't\n{cmd}\n# that's all",    # apostrophes inside comments
    "{cmd} ; echo $env:PATH",                  # untrusted neighbour
    "echo a; {cmd}; echo b",
    "echo 'x'# don't\n{cmd}\necho 'y'# it's",  # comment after a quote
    "echo (1)# don't\n{cmd}\necho (2)# it's",  # comment after a paren
    "echo a<# ; {cmd} #> b",                   # '<#' mid-token is not a comment
    "({cmd})",                                 # grouped
    "&{{{cmd}}}",                              # invoked script block
    ". {cmd}",                                 # dot-sourced
]


def cmd_op(command: str) -> Operation:
    return Operation(tool="run_command", kind=ToolKind.MUTATOR,
                     args={"command": command}, display=command, target=command)


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_each_forbidden_command_is_a_breaker_hit_on_its_own(forbidden):
    assert check_breaker(cmd_op(forbidden)) is not None


@pytest.mark.parametrize("forbidden", FORBIDDEN)
@pytest.mark.parametrize("decoration", DECORATIONS)
def test_no_decoration_makes_a_forbidden_command_approvable(forbidden, decoration):
    """The breaker is documented as unreachable by any rule, mode, or callback.
    An ASK is reachable -- a user can say yes -- so a downgrade to ASK breaks
    the guarantee just as an ALLOW would."""
    command = decoration.format(cmd=forbidden)
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY, (
        f"{command!r} was {decision.verdict.value} at step {decision.step} "
        f"({decision.rule_id})"
    )


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("forbidden", FORBIDDEN[:6])
def test_no_mode_makes_a_forbidden_command_approvable(mode, forbidden):
    engine = PolicyEngine(mode=mode)
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(forbidden))
    assert decision.verdict is Verdict.DENY


@pytest.mark.parametrize("forbidden", FORBIDDEN[:6])
def test_a_session_grant_does_not_unlock_a_forbidden_command(forbidden):
    engine = PolicyEngine()
    op = cmd_op(forbidden)
    engine.grant_for_session(op)
    decision, _ = engine.evaluate(op)
    assert decision.verdict is Verdict.DENY


@pytest.mark.parametrize("forbidden", FORBIDDEN[:6])
def test_a_pre_tool_hook_cannot_rewrite_into_a_forbidden_command(forbidden):
    engine = PolicyEngine()
    engine.pre_tool = lambda op: cmd_op(forbidden)
    decision, _ = engine.evaluate(cmd_op("python -m pytest"))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0
