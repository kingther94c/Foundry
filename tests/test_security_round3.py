"""Third-round regressions.

Three adversarial rounds each breached the command segmenter, twice through a
form the previous round's fix had not imagined. These lock the third round's
findings, including two defects the second round's own fixes introduced.
"""

from __future__ import annotations

import pytest

from foundry.core.policy.engine import (
    Mode,
    PolicyEngine,
    Rule,
    Verdict,
    check_breaker,
    normalize_path,
)
from foundry.core.policy.segmenter import segment_command, strip_comments
from foundry.core.tools.base import Operation, ToolKind
from foundry.core.tools.git import parse_porcelain_path


def cmd_op(command: str) -> Operation:
    return Operation(tool="run_command", kind=ToolKind.MUTATOR,
                     args={"command": command}, display=command, target=command)


def patch_op(*paths: str) -> Operation:
    return Operation(tool="apply_patch", kind=ToolKind.MUTATOR,
                     args={"paths": list(paths)}, display="patch",
                     target=" ".join(paths))


# --- PowerShell comments -------------------------------------------------


@pytest.mark.parametrize("command", [
    "git status # don't\ngit reset --hard\n# that's all",
    "Write-Output hi # don't\nRemove-Item -Force C:\\ -Recurse\n# that's all",
    'git status # a "quote\ngit reset --hard\n# another "quote',
    "git status <# don't #>\ngit reset --hard",
])
def test_a_comment_cannot_swallow_the_next_statement(command):
    """PowerShell strips comments before lexing, so an apostrophe in one is
    inert there -- but to a quote-tracking splitter it opened a region that ate
    every following newline, collapsing a script into one benign statement.
    Verified to run `git reset --hard` past the breaker before the fix."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="git status*", verdict=Verdict.ALLOW))
    engine.add_rule(Rule(tool="run_command", pattern="Write-Output*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY


def test_strip_comments_leaves_quoted_hashes_alone():
    assert strip_comments("git commit -m 'fix #42'") == "git commit -m 'fix #42'"
    assert strip_comments('echo "a # b"') == 'echo "a # b"'


def test_strip_comments_removes_a_trailing_comment():
    assert strip_comments("git status # a comment").strip() == "git status"


# --- an unreadable segment must not disarm the breaker -------------------


@pytest.mark.parametrize("command", [
    "git reset --hard; (foo)",
    "(foo); git reset --hard",
    "git reset --hard; cmd /c echo hi",
    "git clean -fdx; (x)",
    "git push origin main; (x)",
])
def test_unreadable_segment_does_not_downgrade_a_breaker_denial(command):
    """Returning early on an unparseable head discarded the segments already
    parsed, turning a categorical DENY into an approvable ASK -- for operations
    the system prompt tells the model cannot be approved."""
    engine = PolicyEngine()
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0


def test_an_unreadable_segment_still_blocks_auto_allow():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op("git status; (x)"))
    assert decision.verdict is Verdict.ASK


# --- delete targets, whatever the argument order -------------------------


@pytest.mark.parametrize("command", [
    "Remove-Item -Force C:\\ -Recurse",     # switch after the path
    "rm ~ -r",
    "Remove-Item -Recurse -Force $HOME",    # unexpanded variable
    "Remove-Item -Recurse $env:USERPROFILE",
    "Remove-Item C:\\Windows -Recurse",
])
def test_dangerous_delete_target_found_in_any_position(command):
    """The pattern was anchored to the end of the joined argument string, so a
    switch written last pushed the target out of range."""
    assert check_breaker(cmd_op(command)) is not None


def test_ordinary_recursive_delete_is_not_a_breaker_hit():
    assert check_breaker(cmd_op("Remove-Item -Recurse build/")) is None


# --- the rest of git's working-tree destroyers ---------------------------


@pytest.mark.parametrize("command", [
    "git switch --discard-changes other",   # the breaker's own suggestion!
    "git switch -f other",
    "git rm -f -r .",
    "git worktree remove --force wt",
    "git branch -D main",
    "git branch --delete main",
    "git update-ref -d HEAD",
    "git reflog expire --all",
    "git filter-branch --all",
    "git read-tree --reset -u HEAD",
    "git checkout-index -f -a",
])
def test_other_destructive_git_commands_are_refused(command):
    assert check_breaker(cmd_op(command)) is not None


def test_safe_git_switch_is_not_a_breaker_hit():
    assert check_breaker(cmd_op("git switch main")) is None
    assert check_breaker(cmd_op("git switch -c feature")) is None


# --- whole-command DENY patterns -----------------------------------------


@pytest.mark.parametrize("pattern,command", [
    ("*;*", "python -m pytest; ruff check ."),
    ("*|*", "git status | Select-String M"),
    ("git status; *", "git status; npm test"),
])
def test_a_deny_pattern_may_span_a_separator(pattern, command):
    """Matching only per segment made these rules match nothing, silently."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern=pattern, verdict=Verdict.DENY))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY


def test_an_allow_still_cannot_span_a_separator():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op("pytest; git reset --hard"))
    assert decision.verdict is Verdict.DENY


# --- dirty-file guard across path spellings ------------------------------


@pytest.mark.parametrize("spelling", [
    "src/app.py", "./src/app.py", "src\\app.py", "src/../src/app.py", "SRC/app.py",
])
def test_dirty_guard_survives_alternate_spellings(spelling):
    """The guard compared model-supplied spellings against git's output; any
    other spelling of the same file skipped the one rule that outranks
    accept_edits."""
    engine = PolicyEngine(mode=Mode.ACCEPT_EDITS, dirty_files={"src/app.py"})
    decision, _ = engine.evaluate(patch_op(spelling))
    assert decision.verdict is Verdict.ASK
    assert decision.rule_id == "builtin.dirty_file"


def test_a_clean_file_is_still_allowed_in_accept_edits():
    engine = PolicyEngine(mode=Mode.ACCEPT_EDITS, dirty_files={"src/other.py"})
    decision, _ = engine.evaluate(patch_op("src/app.py"))
    assert decision.verdict is Verdict.ALLOW


def test_normalize_path():
    assert normalize_path("./src/app.py") == "src/app.py"
    assert normalize_path("src\\app.py") == "src/app.py"
    assert normalize_path("src/../src/app.py") == "src/app.py"
    assert normalize_path("SRC/App.PY") == "src/app.py"


# --- porcelain v2 parsing ------------------------------------------------


@pytest.mark.parametrize("line,expected", [
    ("1 .M N... 100644 100644 100644 abc def my dir/notes.md", "my dir/notes.md"),
    ("2 R. N... 100644 100644 100644 abc def R100 new name.md\told name.md",
     "new name.md"),
    ("u UU N... 100644 100644 100644 100644 a b c my dir/conflict me.md",
     "my dir/conflict me.md"),
    ("? untracked file.txt", "untracked file.txt"),
    ("1 .M N... 100644 100644 100644 abc def plain.py", "plain.py"),
])
def test_porcelain_paths_with_spaces_and_renames(line, expected):
    """Splitting on whitespace mangled spaced paths and the rename form yielded
    the *old* path -- names that are not on disk, so the real file skipped the
    dirty guard and attribution reported files that do not exist."""
    assert parse_porcelain_path(line) == expected


def test_porcelain_ignores_unrecognized_lines():
    assert parse_porcelain_path("") is None
    assert parse_porcelain_path("# branch.head main") is None
    assert parse_porcelain_path("! ignored.txt") is None


# --- ordinary use is unaffected ------------------------------------------


@pytest.mark.parametrize("command,pattern", [
    ("python -m pytest -q", "python -m pytest*"),
    ("npm test", "npm test*"),
    ("dotnet build", "dotnet build*"),
    ("git status", "git status*"),
    ("dir src", "dir*"),
    ("cargo test --all", "cargo test*"),
])
def test_everyday_commands_remain_allowable(command, pattern):
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern=pattern, verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.ALLOW


@pytest.mark.parametrize("command", [
    "python -m pytest -q",
    "npm run build",
    "git commit -m 'fix #42'",   # a hash inside quotes is not a comment
    ".\\gradlew build",
    "my-tool.exe --flag",
])
def test_everyday_commands_are_parseable(command):
    assert segment_command(command).trusted
