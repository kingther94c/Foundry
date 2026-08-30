"""The paragraph the model is given must describe the table that runs.

The generated permissions paragraph told the model that `git merge` was always
refused. `git pull` performs exactly that merge and passed the breaker, so a
user allow rule of `git *` auto-approved it -- and since it moves HEAD, the
session could then never report `completed`. The same paragraph omitted eight
shapes the breaker does deny, which the model could only find by being refused.
"""

from __future__ import annotations

import pytest

from foundry.core.policy.engine import (
    DESTRUCTIVE_GIT,
    HISTORY_MOVING_GIT,
    PolicyEngine,
    categorical_denials,
    check_breaker,
)
from foundry.core.prompts import permissions_paragraph
from foundry.core.tools.base import Operation, ToolKind


def _op(command: str) -> Operation:
    return Operation(tool="run_command", kind=ToolKind.MUTATOR,
                     args={"command": command}, display=command, target=command)


@pytest.mark.parametrize("subcommand", HISTORY_MOVING_GIT)
def test_every_history_moving_subcommand_is_refused(subcommand):
    assert check_breaker(_op(f"git {subcommand} something")) is not None


@pytest.mark.parametrize("command", [
    "git pull", "git pull --rebase", "git pull origin main",
    "git cherry-pick abc123", "git revert HEAD", "git am patch.mbox",
])
def test_the_shapes_that_merge_behind_the_promise(command):
    hit = check_breaker(_op(command))
    assert hit is not None, f"{command} moves HEAD but was allowed"


def test_git_apply_does_not_route_around_the_patch_tool():
    """It edits the working tree without the read-before-edit check, the
    dirty-file guard, or the anchored parser -- every protection apply_patch
    exists to apply."""
    hit = check_breaker(_op("git apply changes.diff"))
    assert hit is not None
    assert "apply_patch" in hit.reason, "the model needs to be told what to use instead"


@pytest.mark.parametrize("command", [
    "git clean -n", "git clean --dry-run", "git clean -xdn",
    "git clean -n -x -d", "git -C . clean -n",
])
def test_a_dry_run_clean_is_not_treated_as_destruction(command):
    """It only lists what would be removed, and it is how a model checks before
    asking. Refusing it with "destroys uncommitted work" was simply false."""
    assert check_breaker(_op(command)) is None


@pytest.mark.parametrize("command", [
    "git clean -f", "git clean -fdx", "git clean -xdf", "git clean",
    "git clean --force", "git clean -d",
])
def test_a_real_clean_is_still_refused(command):
    assert check_breaker(_op(command)) is not None


@pytest.mark.parametrize("decoration", [
    "git status; {cmd}", "{cmd} # -n", "git status && {cmd}",
])
def test_a_comment_cannot_forge_the_dry_run_exemption(decoration):
    """The exemption is per-reading: the naive reading ignores comments, so a
    flag that only the lexer sees does not unlock anything."""
    command = decoration.format(cmd="git clean -fdx")
    assert check_breaker(_op(command)) is not None


def test_the_paragraph_names_every_family_the_table_denies():
    paragraph = "\n".join(categorical_denials())
    for subcommand in HISTORY_MOVING_GIT:
        assert subcommand in paragraph, f"git {subcommand} is denied but never mentioned"
    for form in DESTRUCTIVE_GIT:
        assert " ".join(form[1:]) in paragraph, f"{form} is denied but never mentioned"
    for shape in ("filter-branch", "reflog expire", "worktree remove",
                  "checkout-index", "read-tree --reset", "apply"):
        assert shape in paragraph, f"{shape} is denied but never mentioned"


def test_the_paragraph_promises_nothing_the_table_allows():
    """Each git subcommand the paragraph names as categorically refused must
    actually be refused -- this is the direction that lied."""
    paragraph = " ".join(categorical_denials())
    named = {word.strip(",()") for word in paragraph.split()
             if word.strip(",()").isalpha() or "-" in word}
    for candidate in named & {"push", "commit", "rebase", "merge", "pull",
                              "cherry-pick", "revert", "am", "apply", "clean",
                              "restore"}:
        assert check_breaker(_op(f"git {candidate} x")) is not None, (
            f"the paragraph promises git {candidate} is refused, and it is not")


def test_the_rendered_paragraph_reaches_the_model():
    rendered = permissions_paragraph(PolicyEngine())
    assert "always refused and cannot be approved" in rendered
    assert "pull" in rendered and "cherry-pick" in rendered
