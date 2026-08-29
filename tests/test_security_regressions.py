"""Regressions for defects an adversarial review of the implementation found.

Every case here was verified to be exploitable before the fix. They are grouped
by the guarantee they defend so a future change that reopens one fails loudly.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from foundry.core.policy.engine import (
    Layer,
    Mode,
    PolicyEngine,
    Rule,
    Verdict,
    check_breaker,
)
from foundry.core.policy.segmenter import canonicalize, effective_argv, segment_command
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import Operation, ReadTracker, ToolContext, ToolKind
from foundry.core.tools.files import ReadFile, SearchText
from foundry.core.tools.git import run_git
from foundry.core.tools.patch import ApplyPatch
from foundry.core.workspace import Workspace


def cmd_op(command: str) -> Operation:
    return Operation(tool="run_command", kind=ToolKind.MUTATOR,
                     args={"command": command}, display=command, target=command)


# --- a chained command cannot ride an allowlisted prefix -----------------


@pytest.mark.parametrize("command", [
    "pytest -q; Remove-Item -Recurse -Force C:/Users/bob",
    "pytest -q; git push origin main",
    "pytest -q | Out-File evil.txt",
    "pytest -q; something-else",
])
def test_allow_rule_does_not_cover_a_chained_second_command(command):
    """The bypass the segmenter exists to stop: an ALLOW must cover every
    segment, not just the string the command happens to start with."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="pytest*", verdict=Verdict.ALLOW,
                         layer=Layer.USER, rule_id="user.pytest"))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is not Verdict.ALLOW, f"{command!r} was auto-allowed"


def test_allow_rule_still_covers_a_single_matching_command():
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="pytest*", verdict=Verdict.ALLOW,
                         rule_id="user.pytest"))
    decision, _ = engine.evaluate(cmd_op("pytest -q"))
    assert decision.verdict is Verdict.ALLOW


def test_ask_rule_fires_on_a_later_segment():
    """`echo x; git log` must still reach the git rule."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    engine.add_rule(Rule(tool="run_command", pattern="git*", verdict=Verdict.ASK))
    decision, _ = engine.evaluate(cmd_op("write-output x; git log"))
    assert decision.verdict is Verdict.ASK


# --- the breaker cannot be dodged with argument shape --------------------


@pytest.mark.parametrize("command", [
    "git -C . reset --hard HEAD",
    "git.exe reset --hard HEAD",
    "git --git-dir=.git --work-tree=. reset --hard",
    "git -c core.pager=cat clean -fdx",
    "git.exe stash drop",
    "git -c user.name=x commit -am wip",
    "git checkout .",
    "git checkout HEAD -- src",
    "git -C subdir push origin main",
])
def test_breaker_is_not_fooled_by_global_options_or_exe_suffix(command):
    assert check_breaker(cmd_op(command)) is not None, f"{command!r} slipped past the breaker"


def test_checkout_is_refused_wholesale_and_points_at_switch():
    """A branch name and a pathspec are not distinguishable here -- git itself
    could not, which is why `switch` exists. So the ambiguous form is refused
    and the model is told the unambiguous one."""
    hit = check_breaker(cmd_op("git checkout main"))
    assert hit is not None
    assert "git switch" in hit.reason


def test_unambiguous_branch_commands_are_not_breaker_hits():
    assert check_breaker(cmd_op("git switch main")) is None
    assert check_breaker(cmd_op("git status")) is None
    assert check_breaker(cmd_op("git log --oneline")) is None


@pytest.mark.parametrize("command", [
    "&'git' push origin main",
    "&\"git\" push origin main",
    "&'rm' -r -fo C:\\Users\\bob",
    "&'C:\\Windows\\System32\\cmd.exe' /c del /s /q C:\\Users\\bob\\Documents",
])
def test_call_operator_without_a_space_is_untrusted(command):
    """`&'foo'` invokes just like `& 'foo'`; only the spaced form was caught."""
    result = segment_command(command)
    assert not result.trusted, f"{command!r} was treated as parseable"


def test_canonicalize_strips_a_leading_call_operator():
    assert canonicalize("&'rm'") == "remove-item"
    assert canonicalize("&git") == "git"


def test_effective_argv_skips_git_global_options():
    assert effective_argv(("git", "-C", ".", "reset", "--hard")) == ("git", "reset", "--hard")
    assert effective_argv(("git.exe", "stash", "drop")) == ("git", "stash", "drop")


# --- second round: bypasses the first round of fixes still allowed -------


@pytest.mark.parametrize("command", [
    'python -c "print(1)"\rgit reset --hard',
    "echo hi\r\ngit reset --hard",
    "echo hi\rgit clean -fd",
])
def test_carriage_return_separates_statements(command):
    """A bare CR ends a statement in PowerShell. Treating it as whitespace
    collapsed a whole chain into one segment headed by the harmless command --
    verified to actually revert a file before the fix."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="python*", verdict=Verdict.ALLOW))
    engine.add_rule(Rule(tool="run_command", pattern="echo*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.DENY


@pytest.mark.parametrize("command", [
    "git --no-advice reset --hard",
    "git --icase-pathspecs reset --hard",
    "git --glob-pathspecs reset --hard",
    "git --noglob-pathspecs clean -fd",
    "git --no-lazy-fetch reset --hard",
    "git --attr-source=HEAD reset --hard",
])
def test_unenumerated_git_global_options_do_not_hide_the_subcommand(command):
    """Enumerating git's global flags was the bug: the first one missing from
    the list stopped the scan and the breaker read the wrong argv index."""
    assert check_breaker(cmd_op(command)) is not None


@pytest.mark.parametrize("command", [
    "(git reset --hard)",
    "( git reset --hard )",
    "((git reset --hard))",
    "&{git reset --hard}",
    ". git reset --hard",
    "cmd /c git reset --hard",
    "powershell -NoProfile -Command git reset --hard",
    "$cmd = 'git'; git reset --hard",
])
def test_wrapped_command_forms_are_never_auto_allowed(command):
    """A head that is not a plain command name hides what runs. Allowlisting
    the shape we can parse beats enumerating the shapes we cannot."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is not Verdict.ALLOW


@pytest.mark.parametrize("command,pattern", [
    ("dir src", "get-childitem*"),          # canonical spelling
    ("dir src", "dir*"),                    # raw spelling
    ("git.exe status", "git status*"),
    (".\\scripts\\build.ps1 -Release", ".\\scripts\\build.ps1*"),
    ("python -m pytest -q", "python -m pytest*"),
])
def test_a_rule_can_be_written_for_the_spelling_the_user_types(command, pattern):
    """Requiring a pattern to match both the canonical and raw spelling made
    every alias rule impossible and pushed users toward pattern='*'."""
    engine = PolicyEngine()
    engine.add_rule(Rule(tool="run_command", pattern=pattern, verdict=Verdict.ALLOW))
    decision, _ = engine.evaluate(cmd_op(command))
    assert decision.verdict is Verdict.ALLOW


def test_interpreters_are_still_allowable():
    """python -m pytest is the most common legitimate command; refusing to
    auto-allow it would buy nothing, since no parsing catches
    python -c "subprocess.run(['git','reset'])" anyway."""
    assert segment_command("python -m pytest -q").trusted


# --- a repository cannot loosen its own settings -------------------------


def test_repo_config_cannot_grant_itself_accept_edits(tmp_path):
    from foundry.core.config import load_config
    from foundry.core.errors import ConfigError

    workspace = tmp_path / "repo"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        '[runtime]\nmode = "accept_edits"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="more restrictive"):
        load_config(workspace, home=tmp_path / "home")


def test_repo_config_may_select_a_stricter_mode(tmp_path):
    from foundry.core.config import load_config

    workspace = tmp_path / "repo"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        '[runtime]\nmode = "plan"\n', encoding="utf-8")
    assert load_config(workspace, home=tmp_path / "home").mode is Mode.PLAN


def test_repo_config_cannot_raise_the_budget(tmp_path):
    from foundry.core.config import load_config
    from foundry.core.errors import ConfigError

    workspace = tmp_path / "repo"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        '[runtime]\nmax_tool_calls = 100000\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="may only lower"):
        load_config(workspace, home=tmp_path / "home")


# --- a hostile repo config cannot make git launch a program --------------


@pytest.mark.skipif(sys.platform != "win32", reason="uses a .bat payload")
def test_git_diff_does_not_run_a_configured_external_diff(tmp_path):
    """A repo's own .git/config is attacker-controlled whenever the repo came
    from an archive or a share."""
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "PWNED.txt"
    payload = tmp_path / "pwn.bat"
    payload.write_text(f'@echo off\r\necho x > "{marker}"\r\necho fake\r\n', encoding="utf-8")

    for args in (["init", "-q"], ["config", "user.email", "t@e.com"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, capture_output=True)
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "diff.external", str(payload)], cwd=repo,
                   capture_output=True)
    (repo / "a.txt").write_text("two\n", encoding="utf-8")

    code, out, _ = run_git(["diff"], repo)
    assert not marker.exists(), "a repository config made git launch a program"
    assert "diff --git" in out, "hardening should not break real diff output"


def test_git_environment_excludes_credentials(monkeypatch):
    from foundry.core.tools.git import _git_env

    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-inherited")
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    env = _git_env()
    assert "OPENAI_API_KEY" not in env
    assert "GIT_DIR" not in env


# --- read-only tools respect the workspace boundary ----------------------


@pytest.fixture()
def ctx(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "inside.txt").write_text("ordinary content\n", encoding="utf-8")
    return ToolContext(workspace=Workspace(repo),
                       artifacts=ArtifactStore(tmp_path / "artifacts"),
                       read_tracker=ReadTracker())


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_search_does_not_descend_through_a_junction(ctx, tmp_path):
    """os.walk follows a junction: islink() reports False for one, so the
    read-only tools needed the same reparse check resolve() already had."""
    outside = tmp_path / "secrets"
    outside.mkdir()
    (outside / "credentials").write_text("aws_secret_access_key = HUNTER2\n", encoding="utf-8")
    link = ctx.workspace.root / "link"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:  # pragma: no cover - environment dependent
        pytest.skip("could not create a junction")

    tool = SearchText()
    out = tool.execute(tool.validate({"query": "aws_secret_access_key"}), ctx)
    assert "HUNTER2" not in out.content
    assert "credentials" not in out.content

    # And the direct path is still refused, as it always was.
    from foundry.core.errors import ToolError

    reader = ReadFile()
    with pytest.raises(ToolError):
        reader.execute(reader.validate({"path": "link/credentials"}), ctx)


# --- apply_patch move semantics ------------------------------------------


def envelope(body: str) -> str:
    return f"*** Begin Patch\n{body}\n*** End Patch"


def test_move_destination_is_visible_to_policy(ctx):
    (ctx.workspace.root / "a.py").write_text("A = 1\n", encoding="utf-8")
    op = ApplyPatch().validate({"patch": envelope(
        "*** Update File: a.py\n*** Move to: .git/hooks/pre-commit\n"
        "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE")})
    assert ".git/hooks/pre-commit" in op.args["paths"]
    assert ".git/hooks/pre-commit" in op.display
    assert check_breaker(op) is not None, "a move into .git bypassed the breaker"


def test_move_refuses_to_overwrite_an_existing_file(ctx):
    root = ctx.workspace.root
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "keep.py").write_text("precious uncommitted work\n", encoding="utf-8")
    reader = ReadFile()
    reader.execute(reader.validate({"path": "a.py"}), ctx)

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: a.py\n*** Move to: keep.py\n"
        "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE")}), ctx)

    assert out.is_error
    assert (root / "keep.py").read_text(encoding="utf-8") == "precious uncommitted work\n"
    assert (root / "a.py").read_text(encoding="utf-8") == "A = 1\n", "source must be untouched"


def test_rejected_move_destination_does_not_half_apply(ctx):
    """The destination is resolved during planning, so a rejected path fails
    the file cleanly instead of aborting after the source was rewritten."""
    root = ctx.workspace.root
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    reader = ReadFile()
    reader.execute(reader.validate({"path": "a.py"}), ctx)

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: a.py\n*** Move to: ../escape.py\n"
        "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE")}), ctx)

    assert out.is_error
    assert (root / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_successful_move_updates_the_read_tracker(ctx):
    root = ctx.workspace.root
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    reader = ReadFile()
    reader.execute(reader.validate({"path": "a.py"}), ctx)

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: a.py\n*** Move to: b.py\n"
        "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE")}), ctx)

    assert not out.is_error
    assert (root / "b.py").read_text(encoding="utf-8") == "A = 2\n"
    assert not (root / "a.py").exists()
    assert ctx.read_tracker.has_read("b.py"), "the model should be able to edit what it moved"
    assert not ctx.read_tracker.has_read("a.py")


# --- apply_patch must not mislocate --------------------------------------


def test_indentation_rung_does_not_swallow_surrounding_text(ctx):
    """A mid-line match used to be widened to whole lines, silently deleting
    the assignment around it and reporting success."""
    root = ctx.workspace.root
    (root / "app.py").write_text(
        "def f(a):\n    result = compute(a)\n    return result\n", encoding="utf-8")
    reader = ReadFile()
    reader.execute(reader.validate({"path": "app.py"}), ctx)

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: app.py\n<<<<<<< SEARCH\n  compute(a)\n"
        "=======\n  compute(a, mode)\n>>>>>>> REPLACE")}), ctx)

    assert out.is_error, "a sub-line match must fail rather than guess"
    assert (root / "app.py").read_text(encoding="utf-8") == (
        "def f(a):\n    result = compute(a)\n    return result\n")


def test_trailing_whitespace_rung_preserves_indentation(ctx):
    root = ctx.workspace.root
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    reader = ReadFile()
    reader.execute(reader.validate({"path": "app.py"}), ctx)

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: app.py\n<<<<<<< SEARCH\n    return 1 \n"
        "=======\n    return 2\n>>>>>>> REPLACE")}), ctx)

    assert not out.is_error
    assert (root / "app.py").read_text(encoding="utf-8") == "def run():\n    return 2\n"


@pytest.mark.parametrize("second", ["a.py", "./a.py", "A.py", "sub/../a.py"])
def test_same_file_twice_in_one_patch_is_rejected(ctx, second):
    """Two Update blocks for one path each plan against the original text, so
    the second write silently discards the first. Comparing raw strings let
    './a.py' and 'A.py' slip past as different files."""
    from foundry.core.errors import InvalidToolCall

    with pytest.raises(InvalidToolCall, match="more than once"):
        ApplyPatch().validate({"patch": envelope(
            "*** Update File: a.py\n<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n"
            f"*** Update File: {second}\n<<<<<<< SEARCH\nB = 1\n=======\nB = 2\n>>>>>>> REPLACE")})


def test_two_moves_onto_one_destination_are_rejected(ctx):
    """Both planned cleanly (neither destination existed yet) and both wrote,
    so the first file's content was destroyed while both reported success."""
    from foundry.core.errors import InvalidToolCall

    with pytest.raises(InvalidToolCall, match="more than once"):
        ApplyPatch().validate({"patch": envelope(
            "*** Update File: a.py\n*** Move to: merged.py\n"
            "<<<<<<< SEARCH\nA\n=======\nA1\n>>>>>>> REPLACE\n"
            "*** Update File: b.py\n*** Move to: merged.py\n"
            "<<<<<<< SEARCH\nB\n=======\nB1\n>>>>>>> REPLACE")})


def test_a_move_onto_an_added_file_is_rejected(ctx):
    from foundry.core.errors import InvalidToolCall

    with pytest.raises(InvalidToolCall, match="more than once"):
        ApplyPatch().validate({"patch": envelope(
            "*** Update File: a.py\n*** Move to: dest.py\n"
            "<<<<<<< SEARCH\nA\n=======\nA1\n>>>>>>> REPLACE\n"
            "*** Add File: dest.py\n+added")})


@pytest.mark.parametrize("value", ["9999.0", "1e9", '"9999"', "true"])
def test_repo_config_budget_must_be_an_integer(tmp_path, value):
    """The tighten check sat inside an isinstance(int) test, so a TOML float
    skipped it entirely: 9999 was refused while 9999.0 was accepted."""
    from foundry.core.config import load_config
    from foundry.core.errors import ConfigError

    workspace = tmp_path / "repo"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        f"[runtime]\nmax_tool_rounds = {value}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(workspace, home=tmp_path / "home")


def test_user_config_budget_must_also_be_an_integer(tmp_path):
    """A string reached the code that slices tool output and broke every later
    tool call, so the type check belongs in the user path too."""
    from foundry.core.config import load_config
    from foundry.core.errors import ConfigError

    (tmp_path / "config.toml").write_text(
        '[runtime]\nmax_output_bytes = "99999"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config(home=tmp_path)
