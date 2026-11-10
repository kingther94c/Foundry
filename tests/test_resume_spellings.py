"""Resuming as codex spells it (D-088): `foundry resume --last [TASK]`,
`foundry resume --all` (every workspace's sessions, each continued where it
was recorded), and `foundry exec resume [--last | SESSION] [TASK]`.

What carries it: with --last a word is the task, never a session id; a
session from another workspace is continued in its own, never in this one;
and the exec form is read only where `resume` is the first word, so an
option's value or a task after `--` is never taken for it.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app
from foundry.cli.session_commands import _choose_resume
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.resume import latest_session, recent_sessions

from test_cli_e2e import text_turn, tool_turn
from test_exec_session_id import _configure
from test_resume import fake_model, repo  # noqa: F401 (fixtures)
from test_resume_picker import _Answers, _session, home  # noqa: F401 (fixture)

FAILED = EXIT_CODES[TerminalStatus.FAILED]


def _chosen(argv, monkeypatch, *, interactive=False, answers=()):
    """What `foundry resume` would continue: the parsed arguments after the
    choice, and what it said."""
    monkeypatch.setattr("foundry.cli.session_commands._interactive", lambda: interactive)
    console = _Answers(answers)
    args = app.build_parser().parse_args(argv)
    stop = _choose_resume(args, console)
    return stop, args, " ".join(console.printed)


# --- --last ------------------------------------------------------------------------------


@pytest.mark.parametrize("argv, task", [
    (["resume", "--last"], None),
    (["resume", "--last", "fix the parser"], "fix the parser"),
    (["resume", "fix the parser", "--last"], "fix the parser"),
])
def test_last_is_the_newest_and_a_word_is_the_task(home, repo, monkeypatch, argv, task):
    stop, args, _ = _chosen(argv, monkeypatch, interactive=True)
    assert stop is None and args.resume == "last" and args.task == task


def test_last_takes_no_session_id_besides_a_task(home, repo, monkeypatch):
    stop, args, said = _chosen(["resume", "--last", "2026-x", "fix it"], monkeypatch)
    assert stop == 2 and "give it a task, not a session id" in said


def test_last_is_not_asked_about(home, repo, monkeypatch):
    _session(home, repo, 1, "a")
    _session(home, repo, 2, "b")
    stop, args, _ = _chosen(["resume", "--last"], monkeypatch, interactive=True)
    assert stop is None and args.resume == "last"


# --- --all -------------------------------------------------------------------------------


def test_every_workspaces_sessions_are_listed_with_where_they_were(home, repo, tmp_path):
    other = tmp_path / "other"
    _session(home, repo, 1, "here")
    newest = _session(home, other, 2, "there")
    _session(home, "", 3, "nowhere")
    listed = recent_sessions(home / "sessions", None, limit=10)
    assert [(s.session_id, s.workspace) for s in listed] == [
        (newest, str(other)), ("20261107T000001-00000001", str(repo))]
    assert latest_session(home / "sessions", None) == newest
    assert latest_session(home / "sessions", repo) == "20261107T000001-00000001"


def test_at_a_terminal_all_offers_every_workspaces_and_resumes_where_it_was(
        home, repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    _session(home, repo, 1, "here")
    there = _session(home, other, 2, "there")
    stop, args, said = _chosen(["resume", "--all"], monkeypatch, interactive=True,
                               answers=["1"])
    assert stop is None and args.resume == there and args.workspace == str(other)
    assert str(repo) in said and str(other) in said
    assert f"resuming {there} in {other}" in said


@pytest.mark.parametrize("argv, task", [
    (["resume", "--all"], None),
    (["resume", "--all", "--last", "go on"], "go on"),
])
def test_without_a_terminal_all_is_the_newest_anywhere(home, repo, tmp_path, monkeypatch,
                                                       argv, task):
    _session(home, repo, 1, "here")
    there = _session(home, tmp_path / "other", 2, "there")
    stop, args, _ = _chosen(argv, monkeypatch)
    assert stop is None and args.resume == there and args.task == task
    assert args.workspace == str(tmp_path / "other")


def test_a_named_session_is_continued_where_it_was_recorded(home, repo, tmp_path,
                                                            monkeypatch):
    here = _session(home, repo, 1, "here")
    _session(home, tmp_path / "other", 2, "there")
    stop, args, _ = _chosen(["resume", "--all", here, "-C", str(tmp_path)], monkeypatch)
    assert stop is None and args.resume == here and args.workspace == str(repo)


@pytest.mark.parametrize("argv, said", [
    (["resume", "--all"], "no recorded session"),
    (["resume", "--all", "20261107T000009-00000009"], "no such session"),
    (["resume", "--all", "../escape"], "no such session"),
])
def test_all_says_when_there_is_nothing_to_continue(home, monkeypatch, argv, said):
    stop, _, printed = _chosen(argv, monkeypatch)
    assert stop == FAILED and said in printed


def test_a_session_that_names_no_workspace_is_refused(home, monkeypatch):
    nowhere = _session(home, "", 1, "a")
    stop, _, said = _chosen(["resume", "--all", nowhere], monkeypatch)
    assert stop == FAILED and "does not say where it was recorded" in said


def test_choosing_none_from_all_starts_nothing(home, repo, tmp_path, monkeypatch):
    _session(home, repo, 1, "here")
    _session(home, tmp_path / "other", 2, "there")
    stop, _, _ = _chosen(["resume", "--all"], monkeypatch, interactive=True,
                         answers=[KeyboardInterrupt()])
    assert stop == EXIT_CODES[TerminalStatus.CANCELLED]


def test_without_all_nothing_changes_where(home, repo, tmp_path, monkeypatch):
    _session(home, tmp_path / "other", 1, "there")
    stop, args, _ = _chosen(["resume", "-C", str(repo)], monkeypatch)
    assert stop is None and args.resume == "last" and args.workspace == str(repo)


# --- exec resume -------------------------------------------------------------------------


@pytest.mark.parametrize("argv, expected", [
    (["exec", "resume", "--last"], ["exec", "--resume", "last"]),
    (["exec", "resume", "--last", "do X"], ["exec", "--resume", "last", "--", "do X"]),
    (["exec", "--json", "resume", "S1", "do X"],
     ["exec", "--json", "--resume", "S1", "--", "do X"]),
    (["exec", "resume", "S1", "--", "-a dash first"],
     ["exec", "--resume", "S1", "--", "-a dash first"]),
    # An option's value is not the word, and not the flag.
    (["exec", "-C", "resume", "resume", "--last"], ["exec", "-C", "resume", "--resume", "last"]),
    (["exec", "-o", "--last", "resume", "S1"], ["exec", "-o", "--last", "--resume", "S1"]),
    # Not this form: left as it was.
    (["exec", "resume the work"], ["exec", "resume the work"]),
    (["exec", "--", "resume"], ["exec", "--", "resume"]),
    (["exec", "fix it", "resume"], ["exec", "fix it", "resume"]),
    (["run", "resume"], ["run", "resume"]),
    ([], []),
])
def test_exec_resume_is_exec_with_resume(argv, expected):
    assert app.exec_resume(argv, app.build_parser()) == expected


def test_exec_resume_needs_a_session(capsys):
    assert app.exec_resume(["exec", "--json", "resume"], app.build_parser()) is None
    assert app.main(["exec", "resume"]) == 2
    assert "exec resume needs a session id, or --last" in capsys.readouterr().err


def test_exec_resume_last_continues_the_session(repo, tmp_path, monkeypatch, capsys,
                                                fake_model):
    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch)
    workspace = ["--workspace", str(repo)]
    handler.turns, handler.index = [text_turn("A is 1")], 0
    app.main(["exec", "what is A?", "--json", *workspace])
    first = json.loads(capsys.readouterr().out.splitlines()[0])

    handler.turns, handler.index = [tool_turn("finish", {"status": "completed",
                                                         "summary": "B is 2",
                                                         "claims": []}, "f1")], 0
    assert app.main(["exec", "resume", "--last", "and B?", "--json", *workspace]) == 0
    again = json.loads(capsys.readouterr().out.splitlines()[0])
    assert again == {**first, "resumed": True}
    assert "and B?" in json.dumps(httpd.requests[-1])
