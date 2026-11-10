"""``@name`` finds the file whose path ends with it (D-107), as codex's @
file search finds one by its name: ``@x.py`` attaches ``src/x.py`` when it is
the only file so named.

What carries it: the names searched are git's -- tracked files, and new ones
it does not ignore -- so an ignored file, a secret's usual place, is never
found by its name alone; a path that is a file wins over any name; and a name
several files share attaches none of them, but says which they are. A file
found so goes the way a path's does (D-058): the workspace, the policy, the
redactor.
"""

from __future__ import annotations

import pytest

from foundry.cli.attachments import files_named, workspace_files
from foundry.core.errors import ToolError
from foundry.core.policy.engine import Rule, Verdict
from foundry.core.tools.git import run_git

from test_mentions import NOTE, _sent_once
from test_resume import repo  # noqa: F401 (fixture)
from test_slash_commands import _shown

NO_SUCH = "no such file in the workspace; sent as written"


def _files(repo, *paths, commit=True):
    for path in paths:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        if not (repo / path).exists():
            (repo / path).write_text(f"# {path}\n", encoding="utf-8")
    if commit:
        run_git(["add", "."], repo)
        run_git(["commit", "-q", "-m", "files"], repo)


# --- what a name finds -----------------------------------------------------------------


def test_a_name_attaches_the_one_file_it_ends(repo, tmp_path):
    _files(repo, "src/x.py")
    wiring, typist, sent = _sent_once(repo, tmp_path, "what is in @x.py?")
    assert sent == f"what is in @x.py?\n\n{NOTE}src/x.py (1 lines total)\n     1\t# src/x.py"
    assert "attached src/x.py" in _shown(typist)
    assert wiring.runtime.tool_ctx.read_tracker.has_read("src/x.py")


def test_a_new_file_git_does_not_ignore_is_found(repo, tmp_path):
    _files(repo, "src/new.py", commit=False)
    _, _, sent = _sent_once(repo, tmp_path, "@new.py")
    assert f"{NOTE}src/new.py (1 lines total)" in sent


def test_an_ignored_file_is_not_found_by_its_name(repo, tmp_path):
    (repo / ".gitignore").write_text("*.env\n", encoding="utf-8")
    _files(repo, ".gitignore", "config/prod.env")
    _, typist, sent = _sent_once(repo, tmp_path, "@prod.env")
    assert sent == "@prod.env" and f"@prod.env: {NO_SUCH}" in _shown(typist)
    _, _, sent = _sent_once(repo, tmp_path, "@config/prod.env")
    assert f"{NOTE}config/prod.env" in sent        # its path still attaches it (D-058)


@pytest.mark.parametrize("name, found", [
    ("X.PY", "src/pkg/x.py"),
    ("pkg/x.py", "src/pkg/x.py"),
    ("pkg\\x.py", "src/pkg/x.py"),
    ("other/y.py", "lib/other/y.py"),
])
def test_in_any_case_and_with_more_of_the_path(repo, tmp_path, name, found):
    _files(repo, "src/pkg/x.py", "lib/other/y.py", "lib/notpkg/z.py")
    _, _, sent = _sent_once(repo, tmp_path, f"see @{name}")
    assert f"{NOTE}{found} (" in sent


def test_a_quoted_name_is_looked_for_too(repo, tmp_path):
    _files(repo, "docs/my notes.md")
    _, _, sent = _sent_once(repo, tmp_path, 'read @"my notes.md"')
    assert f"{NOTE}docs/my notes.md (" in sent


def test_a_path_that_is_a_file_wins(repo, tmp_path):
    _files(repo, "src/a.py")
    _, typist, sent = _sent_once(repo, tmp_path, "@a.py")
    assert f"{NOTE}a.py (" in sent and "src/a.py" not in sent
    assert "files are named so" not in _shown(typist)


# --- what it does not ------------------------------------------------------------------


def test_a_name_several_share_attaches_none_and_says_which(repo, tmp_path):
    _files(repo, "src/x.py", "tests/x.py")
    _, typist, sent = _sent_once(repo, tmp_path, "fix @x.py")
    assert sent == "fix @x.py"
    assert ("@x.py: 2 files are named so (src/x.py, tests/x.py); write more of the path; "
            "sent as written") in _shown(typist)


def test_only_the_first_five_are_said(repo, tmp_path):
    _files(repo, *(f"d{i}/x.py" for i in range(7)))
    _, typist, _ = _sent_once(repo, tmp_path, "@x.py")
    assert ("@x.py: 7 files are named so (d0/x.py, d1/x.py, d2/x.py, d3/x.py, d4/x.py, ...)"
            in _shown(typist))


def test_five_are_said_without_more(repo, tmp_path):
    _files(repo, *(f"d{i}/x.py" for i in range(5)))
    _, typist, _ = _sent_once(repo, tmp_path, "@x.py")
    assert "(d0/x.py, d1/x.py, d2/x.py, d3/x.py, d4/x.py);" in _shown(typist)


def test_part_of_a_name_is_not_a_name(repo, tmp_path):
    _files(repo, "src/ax.py", "src/x.pyc.txt")
    _, typist, sent = _sent_once(repo, tmp_path, "@x.py")
    assert sent == "@x.py" and f"@x.py: {NO_SUCH}" in _shown(typist)


def test_what_does_not_look_like_a_file_is_not_looked_for(repo, tmp_path, monkeypatch):
    _files(repo, "src/property")
    asked = []
    monkeypatch.setattr("foundry.cli.attachments.workspace_files",
                        lambda root: asked.append(root) or [])
    _, typist, sent = _sent_once(repo, tmp_path, "what does @property do?")
    assert sent == "what does @property do?" and asked == [] and NO_SUCH not in _shown(typist)


def test_a_file_git_knows_but_that_is_gone_is_not_found(repo, tmp_path):
    _files(repo, "src/gone.py")
    (repo / "src" / "gone.py").unlink()
    _, typist, sent = _sent_once(repo, tmp_path, "@gone.py")
    assert sent == "@gone.py" and f"@gone.py: {NO_SUCH}" in _shown(typist)


def test_the_list_is_asked_for_once_a_message(repo, tmp_path, monkeypatch):
    _files(repo, "src/x.py", "src/y.py")
    asked = []
    listing = workspace_files
    monkeypatch.setattr("foundry.cli.attachments.workspace_files",
                        lambda root: asked.append(root) or listing(root))
    _, _, sent = _sent_once(repo, tmp_path, "@x.py and @y.py and @z.py")
    assert sent.count(NOTE) == 2 and len(asked) == 1


def test_a_rule_that_refuses_reading_it_refuses_this(repo, tmp_path):
    _files(repo, "src/x.py")

    def deny(wiring):
        wiring.runtime.policy.rules.append(
            Rule(tool="read_file", pattern="src/x.py", verdict=Verdict.DENY, reason="private"))

    _, typist, sent = _sent_once(repo, tmp_path, "@x.py", deny)
    assert sent == "@x.py"
    assert "@x.py: not attached, the policy refuses reading it (private)" in _shown(typist)


# --- git's list --------------------------------------------------------------------------


def test_what_git_lists(repo):
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _files(repo, ".gitignore", "src/x.py")
    _files(repo, "src/new.py", "out/run.log", commit=False)
    assert sorted(workspace_files(repo)) == [".gitignore", "a.py", "b.py", "src/new.py",
                                                  "src/x.py"]


def test_no_names_when_git_cannot_say(repo, monkeypatch):
    def broken(args, cwd):
        raise ToolError("git is not on PATH")

    monkeypatch.setattr("foundry.cli.attachments.run_git", broken)
    assert workspace_files(repo) == []
    monkeypatch.setattr("foundry.cli.attachments.run_git",
                        lambda args, cwd: (128, "a.py\0", "fatal"))
    assert workspace_files(repo) == []


@pytest.mark.parametrize("name, files, found", [
    ("x.py", ["x.py", "src/x.py", "src/ax.py"], ["src/x.py", "x.py"]),
    ("Src/X.py", ["src/x.py"], ["src/x.py"]),
    ("src\\x.py", ["src/x.py"], ["src/x.py"]),
    ("rc/x.py", ["src/x.py"], []),
    ("../x.py", ["x.py"], []),
    ("/x.py", ["x.py"], []),
])
def test_files_named(name, files, found):
    assert files_named(name, files) == found
