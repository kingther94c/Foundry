"""/review against a base branch or one commit (D-066), as codex's /review
offers them, and the git_diff arguments that show those changes.

A revision is resolved to a commit id before anything else, and only the id
reaches git: a name that starts with "-" -- which git would read as an
option, such as --output= -- is refused.
"""

from __future__ import annotations

import pytest

from foundry.cli.repl import REVIEW_USAGE
from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.git import GitDiff, resolve_commit, run_git
from foundry.core.workspace import Workspace

from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _sent, _shown


def _commit(repo, message):
    run_git(["add", "."], repo)
    run_git(["commit", "-m", message], repo)
    return run_git(["rev-parse", "HEAD"], repo)[1].strip()


@pytest.fixture()
def history(repo):
    """main: initial; feature: A is 2; main then moves on (B is 9); and on
    feature, B is 5 but not committed."""
    run_git(["branch", "-M", "main"], repo)
    initial = run_git(["rev-parse", "HEAD"], repo)[1].strip()
    run_git(["checkout", "-b", "feature"], repo)
    (repo / "a.py").write_text("A = 2\n", encoding="utf-8")
    feature = _commit(repo, "feature: A is 2")
    run_git(["checkout", "main"], repo)
    (repo / "b.py").write_text("B = 9\n", encoding="utf-8")
    _commit(repo, "main moves on")
    run_git(["checkout", "feature"], repo)
    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")
    return {"initial": initial, "feature": feature}


def _diff(repo, **args):
    tool = GitDiff()
    ctx = ToolContext(workspace=Workspace(repo), artifacts=None, read_tracker=ReadTracker())
    return tool.execute(tool.validate(args), ctx).content


# --- git_diff ---------------------------------------------------------------------------


def test_base_shows_what_the_branch_changed_and_what_is_not_committed(repo, history):
    shown = _diff(repo, base="main")
    assert "+A = 2" in shown and "+B = 5" in shown
    assert "B = 9" not in shown                     # main's own change since the fork


def test_base_takes_a_path(repo, history):
    shown = _diff(repo, base="main", path="a.py")
    assert "+A = 2" in shown and "b.py" not in shown


def test_commit_shows_its_message_and_its_changes(repo, history):
    shown = _diff(repo, commit=history["feature"][:10])
    assert f"commit {history['feature']}" in shown and "feature: A is 2" in shown
    assert "+A = 2" in shown and "B = 5" not in shown


def test_the_first_commit_shows_too(repo, history):
    shown = _diff(repo, commit=history["initial"])
    assert "+A = 1" in shown and "+B = 1" in shown


def test_without_them_it_shows_what_it_did(repo, history):
    shown = _diff(repo)
    assert "+B = 5" in shown and "A = 2" not in shown


def test_a_branch_with_no_shared_history_says_so(repo, history):
    tree = run_git(["write-tree"], repo)[1].strip()
    lonely = run_git(["commit-tree", tree, "-m", "lonely"], repo)[1].strip()
    with pytest.raises(ToolError, match="share no history"):
        _diff(repo, base=lonely)


def test_an_unknown_name_says_so(repo, history):
    with pytest.raises(ToolError, match="no commit named 'nope' in this repository"):
        _diff(repo, base="nope")
    with pytest.raises(ToolError, match="no commit named 'main:a.py'"):
        _diff(repo, commit="main:a.py")                # a file, not a commit


@pytest.mark.parametrize("args, said", [
    ({"base": "main", "staged": True}, "give at most one of staged, base and commit"),
    ({"base": "main", "commit": "HEAD"}, "give at most one of staged, base and commit"),
    ({"base": "--output=evil.txt"}, "base must name a branch, tag or commit"),
    ({"commit": "-p"}, "commit must name a branch, tag or commit"),
    ({"base": "two words"}, "base must name a branch, tag or commit"),
    ({"commit": "HEAD\x1b[31m"}, "commit must name a branch, tag or commit"),
    ({"base": "x" * 201}, "base must name a branch, tag or commit"),
    ({"base": 7}, "base must be a string"),
    ({"commit": ["HEAD"]}, "commit must be a string"),
])
def test_what_it_refuses(args, said):
    with pytest.raises(InvalidToolCall, match=said.replace("(", r"\(")):
        GitDiff().validate(args)


def test_what_the_user_is_shown(repo):
    assert GitDiff().validate({"base": "main"}).display == "git diff --merge-base main"
    assert GitDiff().validate({"commit": "abc", "path": "a.py"}).display == "git diff abc^! a.py"
    assert GitDiff().validate({"staged": True}).display == "git diff --staged"


def test_an_option_never_reaches_git(repo, tmp_path):
    for name in ("--output=" + str(tmp_path / "evil.txt"), "-o", "--help"):
        with pytest.raises(ToolError, match="is not a branch, tag or commit"):
            resolve_commit(name, repo)
    assert not (tmp_path / "evil.txt").exists()


def test_only_a_full_id_is_passed_on(repo, monkeypatch):
    import foundry.core.tools.git as git
    monkeypatch.setattr(git, "run_git", lambda args, cwd: (0, "--output=evil.txt\n", ""))
    with pytest.raises(ToolError, match="no commit named 'HEAD'"):
        resolve_commit("HEAD", repo)


def test_a_name_resolves_to_the_full_id(repo, history):
    assert resolve_commit("feature", repo) == history["feature"]
    assert resolve_commit("HEAD~1", repo) == history["initial"]
    assert resolve_commit(history["feature"][:8], repo) == history["feature"]


# --- /review ------------------------------------------------------------------------------


def test_review_base_names_the_commit_and_stays_read_only(repo, history, tmp_path):
    main = resolve_commit("main", repo)
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("d1", "git_diff", {"base": main}),
             call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="Looks right."),
    ])
    shown = _shown(_repl(wiring, "/review --base main the parser"))
    sent = _sent(wiring)[0]
    assert sent.startswith(f"Review what this branch changed since it left main (commit {main})")
    assert f'`git_diff` with {{"base": "{main}"}} shows them' in sent
    assert sent.endswith("The user asks you to focus on: the parser")
    assert f"reviewing the changes since main ({main[:12]}); nothing is changed" in shown
    results = [b for m in wiring.runtime.backend.seen[1].messages if m.role.value == "tool"
               for b in m.blocks]
    assert "+A = 2" in results[0].content and "B = 9" not in results[0].content
    decisions = {r.payload["call_id"]: r.payload for r in SessionStore.read_records(
        wiring.session.path) if r.type == EventType.POLICY_DECISION}
    assert decisions["d1"]["verdict"] == "allow"
    assert decisions["p1"]["rule_id"] == "task.read_only"


def test_review_commit_names_it(repo, history, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="Fine.")])
    shown = _shown(_repl(wiring, "/review --commit HEAD"))
    sent = _sent(wiring)[0]
    feature = history["feature"]
    assert sent.startswith(f"Review what commit {feature} changed, as a careful code reviewer")
    assert f'`git_diff` with {{"commit": "{feature}"}} shows its message' in sent
    assert "focus on" not in sent
    assert f"reviewing commit {feature[:12]}; nothing is changed while it runs" in shown


@pytest.mark.parametrize("typed, said", [
    ("/review --base", REVIEW_USAGE),
    ("/review --commit", REVIEW_USAGE),
    ("/review --base nope", "no commit named 'nope' in this repository"),
    ("/review --commit -x", "'-x' is not a branch, tag or commit"),
])
def test_a_review_that_cannot_start_sends_nothing(repo, history, tmp_path, typed, said):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, typed))
    assert said in shown and wiring.runtime.backend.seen == []
    assert wiring.runtime.policy.read_only == ""


def test_help_shows_the_usage(repo, tmp_path):
    from foundry.cli.repl import COMMANDS
    assert COMMANDS["review"].arguments == "[--base REV | --commit REV] [focus]"
