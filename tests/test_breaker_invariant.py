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


# ---------------------------------------------------------------------------
# Two more axes, added after a review found four bypasses that seven prior
# rounds had missed. Every one of them lived in a dimension this generator did
# not vary: DECORATIONS wraps a command, but never moves its arguments around
# and never respells a variable. The invariant's own claim -- that it defends a
# whole class rather than the spellings someone thought of -- is only true
# along the axes it actually enumerates.
# ---------------------------------------------------------------------------

#: git global options that take their value as a separate argument. When that
#: value happens to be a subcommand name, a scan looking for "the first token
#: that is a subcommand" stops on it and the real subcommand slides out of the
#: compared prefix. `git -C log reset --hard` was allowed, and trusted.
GIT_VALUE_OPTION_INSERTS = [
    "-C log", "-C config", "-C tag", "-c status", "--work-tree diff",
    "--git-dir show", "--attr-source HEAD", "--namespace add",
]

FORBIDDEN_GIT = [c for c in FORBIDDEN if c.split()[0] == "git"]


@pytest.mark.parametrize("insert", GIT_VALUE_OPTION_INSERTS)
@pytest.mark.parametrize("forbidden", FORBIDDEN_GIT)
def test_a_global_option_value_cannot_shift_the_subcommand(insert, forbidden):
    """The value of a global option can itself be a subcommand name."""
    head, rest = forbidden.split(" ", 1)
    command = f"{head} {insert} {rest}"
    decision, _ = PolicyEngine().evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY, (
        f"{command!r} was {decision.verdict.value} at step {decision.step}")


#: An option whose value is arbitrary text, where that text looks like a flag.
#: `git clean -e <pattern>` with the pattern `-n` read as `--dry-run`, so the
#: exemption carved out to make `git clean -n` usable deleted files instead.
VALUE_LOOKS_LIKE_A_FLAG = [
    "git clean -fd -e -n",
    "git clean -fdx -e -n",
    "git clean -f --exclude -n",
    "git clean -e --dry-run -fd",
]


@pytest.mark.parametrize("command", VALUE_LOOKS_LIKE_A_FLAG)
def test_an_option_value_that_looks_like_a_flag_is_not_read_as_one(command):
    decision, _ = PolicyEngine().evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY, (
        f"{command!r} was {decision.verdict.value} at step {decision.step}")


@pytest.mark.parametrize("command", ["git clean -n", "git clean --dry-run",
                                     "git clean -xdn", "git clean -n -e build"])
def test_a_real_dry_run_is_still_exempt(command):
    """The other direction. A fix for the hole that broke the exemption would
    just be a different usability regression."""
    assert check_breaker(cmd_op(command)) is None


#: The same variable, spelled every way PowerShell accepts. Only the bare form
#: was listed, and the structural scan blanked quoted spans -- but a
#: double-quoted span interpolates, so `"${HOME}"` expands exactly like $HOME.
VARIABLE_SPELLINGS = [
    "$env:USERPROFILE", "${env:USERPROFILE}", '"$env:USERPROFILE"',
    '"${env:USERPROFILE}"', "$HOME", "${HOME}", '"$HOME"', '"${HOME}"',
    "$PROFILE", "${PROFILE}",
]


@pytest.mark.parametrize("target", VARIABLE_SPELLINGS)
def test_a_variable_is_visible_to_the_structural_scan_in_every_spelling(target):
    """The other half of the defence, pinned separately.

    Two independent things stop these: the breaker's target table matches the
    variable, and the structural scan refuses to call the command parseable so
    it can never be auto-allowed. Asserting only DENY tests the first and lets
    the second rot -- which is exactly how `"${HOME}"` came to be trusted while
    `$env:USERPROFILE` was not.
    """
    from foundry.core.policy.segmenter import segment_command

    command = f"Remove-Item -Recurse -Force {target}"
    assert not segment_command(command).trusted, (
        f"{command!r} parsed as trusted; an ALLOW rule could auto-approve it")


@pytest.mark.parametrize("target", VARIABLE_SPELLINGS)
def test_no_spelling_of_a_home_variable_is_auto_allowable(target):
    command = f"Remove-Item -Recurse -Force {target}"
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY, (
        f"{command!r} was {decision.verdict.value} at step {decision.step}")


@pytest.mark.parametrize("target", ["\\\\?\\C:\\", "\\\\.\\C:\\",
                                    "C:\\", "/"])
def test_no_device_path_spelling_of_a_drive_root_is_allowed(target):
    command = f"Remove-Item -Recurse -Force {target}"
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY, (
        f"{command!r} was {decision.verdict.value} at step {decision.step}")
