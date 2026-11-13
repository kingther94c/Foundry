"""`foundry review` (D-131), as `codex review`: the uncommitted changes, a
branch's, one commit's, or what the user's instructions ask, reviewed
headlessly.

It runs as exec runs a task, with every edit and every command refused for
the whole run; the review is the run's finish summary, printed and written
by -o. Which one is reviewed is given once: none, or two, is refused before
anything is sent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from foundry.cli import app
from foundry.cli.slash_commands import REVIEW_AS_ASKED, REVIEW_READ_ONLY, REVIEW_UNCOMMITTED
from foundry.cli.unattended import REVIEW_FINISH
from foundry.core.prompts import read_only_line
from foundry.core.tools.git import run_git

from test_cli_e2e import text_turn, tool_turn
from test_cli_flags import _exec_home
from test_exec_stdin import _Pipe
from test_resume import PATCH_A, fake_model, repo  # noqa: F401 (fixtures)

REVIEW = "b.py: B is now 5, and nothing tests it"
FINISHED = tool_turn("finish", {"status": "completed", "summary": REVIEW, "claims": []}, "f1")


def _model(tmp_path, monkeypatch, fake_model, *turns):
    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = list(turns) or [FINISHED], 0
    return httpd


def _task(httpd) -> str:
    return httpd.requests[0]["messages"][-1]["content"]


def _review(repo, *argv):
    return app.main(["review", *argv, "--workspace", str(repo)])


def test_the_uncommitted_changes_reviewed_read_only(repo, tmp_path, monkeypatch, capsys,
                                                    fake_model):
    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")
    httpd = _model(tmp_path, monkeypatch, fake_model,
                   tool_turn("git_diff", {}, "d1"),
                   tool_turn("apply_patch", {"patch": PATCH_A}, "p1"),
                   tool_turn("run_command", {"command": "python -V"}, "r1"),
                   FINISHED)
    out = tmp_path / "review.txt"
    assert _review(repo, "--uncommitted", "-o", str(out)) == 0
    captured = capsys.readouterr()
    assert REVIEW in captured.out and REVIEW in out.read_text(encoding="utf-8")
    assert "reviewing the uncommitted changes; nothing is changed" in captured.err
    task = _task(httpd)
    assert task.startswith(REVIEW_UNCOMMITTED) and task.endswith(REVIEW_FINISH)
    refused = [m["content"] for m in httpd.requests[3]["messages"] if m["role"] == "tool"]
    assert "+B = 5" in refused[0]
    assert all(REVIEW_READ_ONLY in r for r in refused[1:]) and len(refused) == 3
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_a_review_is_told_only_of_the_tools_it_has(repo, tmp_path, monkeypatch, capsys,
                                                   fake_model):
    # The user's allowances for edits and commands are not a review's: it is
    # offered neither (D-148), and is not told it may use them (D-173).
    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")
    httpd = _model(tmp_path, monkeypatch, fake_model)
    config = Path(os.environ["FOUNDRY_HOME"]) / "config.toml"
    with config.open("a", encoding="utf-8") as out:
        out.write('[[permissions]]\ntool = "apply_patch"\npattern = "*"\ndecision = "allow"\n'
                  '[[permissions]]\ntool = "run_command"\npattern = "python -m pytest*"\n'
                  'decision = "allow"\n'
                  '[[permissions]]\ntool = "read_file"\npattern = "*"\ndecision = "allow"\n')
    assert _review(repo, "--uncommitted") == 0
    request = httpd.requests[0]
    system = request["messages"][0]["content"]
    assert read_only_line(REVIEW_READ_ONLY) in system
    assert "apply_patch (*)" not in system and "python -m pytest*" not in system
    assert "  - read_file (*)" in system, "an allowance it can use is still said"
    offered = {t["function"]["name"] for t in request["tools"]}
    assert not offered & {"apply_patch", "run_command"}


def test_a_branch_since_it_left_its_base(repo, tmp_path, monkeypatch, capsys, fake_model):
    base = run_git(["rev-parse", "HEAD"], repo)[1].strip()
    httpd = _model(tmp_path, monkeypatch, fake_model)
    assert _review(repo, "--base", "HEAD") == 0
    assert f"reviewing the changes since HEAD ({base[:12]})" in capsys.readouterr().err
    assert f'`git_diff` with {{"base": "{base}"}}' in _task(httpd)


def test_one_commit_with_its_title(repo, tmp_path, monkeypatch, capsys, fake_model):
    commit = run_git(["rev-parse", "HEAD"], repo)[1].strip()
    httpd = _model(tmp_path, monkeypatch, fake_model)
    assert _review(repo, "--commit", "HEAD", "--title", "the first one") == 0
    assert f"reviewing commit {commit[:12]} (the first one)" in capsys.readouterr().err
    assert _task(httpd).startswith(
        f'Review what commit {commit} (the user calls it "the first one") changed')


def test_a_commit_without_a_title(repo, tmp_path, monkeypatch, capsys, fake_model):
    commit = run_git(["rev-parse", "HEAD"], repo)[1].strip()
    httpd = _model(tmp_path, monkeypatch, fake_model)
    assert _review(repo, "--commit", commit[:8]) == 0
    assert f"reviewing commit {commit[:12]};" in capsys.readouterr().err
    assert _task(httpd).startswith(f"Review what commit {commit} changed")


def test_what_the_instructions_ask(repo, tmp_path, monkeypatch, capsys, fake_model):
    httpd = _model(tmp_path, monkeypatch, fake_model)
    assert _review(repo, "  look at a.py for bugs ") == 0
    assert "reviewing what you asked" in capsys.readouterr().err
    task = _task(httpd)
    assert task.startswith(f"{REVIEW_AS_ASKED}\n\nThe user asks: look at a.py for bugs\n\n")


def test_the_instructions_from_stdin(repo, tmp_path, monkeypatch, capsys, fake_model):
    httpd = _model(tmp_path, monkeypatch, fake_model)
    monkeypatch.setattr("sys.stdin", _Pipe("only the parser, caf\u00e9\r\n".encode("utf-8")))
    assert _review(repo, "-") == 0
    assert "The user asks: only the parser, caf\u00e9\n\n" in _task(httpd)


def test_the_event_stream_ends_completed(repo, tmp_path, monkeypatch, capsys, fake_model):
    _model(tmp_path, monkeypatch, fake_model)
    assert _review(repo, "--uncommitted", "--json") == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert events[0]["kind"] == "session_started"
    assert events[-1]["kind"] == "termination" and events[-1]["status"] == "completed"
    assert REVIEW in events[-1]["summary"]


def test_a_review_said_in_prose_is_not_completed(repo, tmp_path, monkeypatch, capsys,
                                                 fake_model):
    httpd = _model(tmp_path, monkeypatch, fake_model, text_turn(REVIEW), text_turn(REVIEW))
    assert _review(repo, "--uncommitted") == 10
    assert len(httpd.requests) == 2          # reminded once to finish, as exec is


@pytest.mark.parametrize("argv, said", [
    ((), "review needs one of --uncommitted, --base BRANCH, --commit SHA"),
    (("--title", "x", "--uncommitted"), "--title names a commit: it goes with --commit"),
    (("   ",), "the review's instructions are empty"),
    (("--base", "no-such-branch"), "no commit named 'no-such-branch' in this repository"),
    (("--commit=--output=x",), "'--output=x' is not a branch, tag or commit"),
], ids=["none", "title-alone", "empty-instructions", "unknown-base", "option-as-commit"])
def test_refused_before_anything_is_sent(repo, tmp_path, monkeypatch, capsys, fake_model,
                                         argv, said):
    httpd = _model(tmp_path, monkeypatch, fake_model)
    assert _review(repo, *argv) == 2
    assert said in capsys.readouterr().err
    assert httpd.requests == []


def test_no_instructions_on_stdin(repo, tmp_path, monkeypatch, capsys, fake_model):
    httpd = _model(tmp_path, monkeypatch, fake_model)
    monkeypatch.setattr("sys.stdin", _Pipe(b"", tty=True))
    assert _review(repo, "-") == 2
    assert "review - reads the instructions from stdin" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", _Pipe(b" \n"))
    assert _review(repo, "-") == 2
    assert "stdin held no instructions" in capsys.readouterr().err
    assert httpd.requests == []


@pytest.mark.parametrize("argv", [
    ("--uncommitted", "--base", "main"),
    ("--base", "main", "--commit", "HEAD"),
    ("--commit", "HEAD", "focus"),
    ("--uncommitted", "--mode", "accept_edits"),
])
def test_one_at_a_time_and_no_other_mode(repo, tmp_path, monkeypatch, capsys, fake_model,
                                         argv):
    httpd = _model(tmp_path, monkeypatch, fake_model)
    with pytest.raises(SystemExit):
        _review(repo, *argv)
    assert httpd.requests == []


def test_exec_review_is_review_as_codex_spells_it(repo, tmp_path, monkeypatch, capsys,
                                                   fake_model):
    httpd = _model(tmp_path, monkeypatch, fake_model, FINISHED)
    assert app.main(["exec", "--json", "review", "--uncommitted", "-C", str(repo)]) == 0
    assert _task(httpd).startswith(REVIEW_UNCOMMITTED)
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "completed"
    httpd.requests.clear()
    assert app.main(["exec", "review", "look at a.py", "-C", str(repo)]) == 0
    assert "\n\nThe user asks: look at a.py\n\n" in _task(httpd)
    httpd.requests.clear()
    assert app.main(["exec", "-C", str(repo), "--", "review"]) == 0
    assert _task(httpd) == "review"           # after --, still the task


def test_review_reaches_the_review_from_slash_review_too(repo, tmp_path):
    from foundry.cli.slash_commands import review_task

    text, what = review_task(repo, focus="naming")
    assert text.startswith(REVIEW_UNCOMMITTED) and text.endswith(
        "The user asks you to focus on: naming")
    assert what == "the uncommitted changes"


@pytest.mark.parametrize("scope", [{}, {"instructions": "look at a.py"}, {"base": "HEAD"}],
                         ids=["uncommitted", "as-asked", "base"])
def test_a_review_is_told_a_new_file_is_not_in_the_diff(repo, scope):
    # git_diff shows the files git tracks: a review's model, told the diff
    # showed what changed, found a new file only through git_status (D-179).
    from foundry.cli.slash_commands import review_task

    text, _ = review_task(repo, **scope)
    first = text.split("\n\n")[0]
    assert "new file" in first and "read_file" in first


def test_a_review_still_runs_in_dont_ask(repo, tmp_path, monkeypatch, capsys, fake_model):
    """exec patches unasked by default (D-188); a review changes nothing and
    says so: its run is dont_ask, not exec's accept_edits."""
    _model(tmp_path, monkeypatch, fake_model)
    assert _review(repo, "--uncommitted", "--json") == 0
    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert first["mode"] == "dont_ask"
