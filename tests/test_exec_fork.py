"""`foundry exec --fork SESSION [TASK]` (D-133), and `exec fork [--last |
SESSION] [TASK]` as codex spells it: a copy of a recorded session, continued
headlessly apart from it.

The copy is made as `foundry fork` makes it (D-126), then resumed with every
check a resume makes; a copy that cannot be continued is dropped. The source
is only read.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from foundry.cli import app

from test_cli_e2e import text_turn, tool_turn
from test_fork import FORK_NOTE, _journals, _model, _said
from test_resume import fake_model, repo  # noqa: F401 (fixtures)


def _source(tmp_path, monkeypatch, fake_model, repo, *answers):
    """A recorded session that asked what A is; the fork's answers after."""
    home, httpd = _model(tmp_path, monkeypatch, fake_model, "A is 1", "A again", *answers)
    assert app.main(["exec", "what is A?", "-C", str(repo)]) == 10
    return home, httpd


def _then(fake_model, *turns):
    httpd, handler = fake_model
    handler.turns, handler.index = list(turns), 0
    httpd.requests.clear()
    return httpd


@pytest.mark.parametrize("argv", [
    ("exec", "--fork", "last", "and B?"),
    ("exec", "fork", "--last", "and B?"),
], ids=["--fork", "exec-fork"])
def test_a_copy_goes_on_and_the_source_is_kept(repo, tmp_path, monkeypatch, capsys,
                                               fake_model, argv):
    home, _ = _source(tmp_path, monkeypatch, fake_model, repo)
    capsys.readouterr()
    _then(fake_model, text_turn("B is 1"), text_turn("B again"))
    assert app.main([*argv, "-C", str(repo)]) == 10
    journals = _journals(home)
    source, fork = journals["source"].parent.name, journals["fork"].parent.name
    assert f"forked {source} into {fork}; {source} is kept as it was" in capsys.readouterr().err
    kept, forked = _said(journals["source"]), _said(journals["fork"])
    assert kept == ["what is A?", "A is 1", app.FINISH_REMINDER, "A again"]
    assert forked[:len(kept)] == kept and forked[-1] == "B again"
    assert forked[len(kept)].startswith(FORK_NOTE.format(source))
    assert forked[len(kept)].endswith("and B?")


def test_by_id_and_completed(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, _ = _source(tmp_path, monkeypatch, fake_model, repo)
    source = _journals(home)["source"].parent.name
    capsys.readouterr()
    _then(fake_model, tool_turn("finish", {"status": "completed", "summary": "B is 1",
                                           "claims": []}, "f1"))
    assert app.main(["exec", "--json", "fork", source, "and B?", "-C", str(repo)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    fork = _journals(home)["fork"].parent.name
    assert events[0]["kind"] == "session_started" and events[0]["session_id"] == fork
    assert events[0]["resumed"] is True and events[-1]["status"] == "completed"


def test_without_a_task_it_carries_on(repo, tmp_path, monkeypatch, capsys, fake_model):
    _source(tmp_path, monkeypatch, fake_model, repo)
    httpd = _then(fake_model, text_turn("going on"), text_turn("still"))
    assert app.main(["exec", "--fork", "last", "-C", str(repo)]) == 10
    assert httpd.requests[0]["messages"][-1]["content"].endswith(app.CONTINUE_PROMPT)


def test_a_completed_source_needs_a_task(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, _ = _model(tmp_path, monkeypatch, fake_model)
    _then(fake_model, tool_turn("finish", {"status": "completed", "summary": "A is 1",
                                           "claims": []}, "f1"))
    assert app.main(["exec", "what is A?", "-C", str(repo)]) == 0
    capsys.readouterr()
    httpd = _then(fake_model, text_turn("never"))
    assert app.main(["exec", "--fork", "last", "-C", str(repo)]) == 2
    assert "that session completed; give the task" in capsys.readouterr().err
    assert list(_journals(home)) == ["source"] and httpd.requests == []


def test_a_copy_that_cannot_go_on_is_dropped(repo, tmp_path, monkeypatch, capsys,
                                             fake_model):
    home, _ = _source(tmp_path, monkeypatch, fake_model, repo)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=repo,
                   check=True)
    capsys.readouterr()
    httpd = _then(fake_model, text_turn("never"))
    assert app.main(["exec", "--fork", "last", "and B?", "-C", str(repo)]) == 12
    assert "HEAD" in capsys.readouterr().err
    assert list(_journals(home)) == ["source"] and httpd.requests == []


def test_a_copy_that_cannot_be_made_runs_nothing(repo, tmp_path, monkeypatch, capsys,
                                                 fake_model):
    home, _ = _source(tmp_path, monkeypatch, fake_model, repo)
    source = _journals(home)["source"]
    before = source.read_bytes()
    capsys.readouterr()

    def full(*args):
        raise OSError("the disk is full")

    monkeypatch.setattr(app, "fork_journal", full)
    httpd = _then(fake_model, text_turn("never"))
    assert app.main(["exec", "--fork", "last", "and B?", "-C", str(repo)]) == 12
    assert "cannot fork: the disk is full" in capsys.readouterr().err
    assert httpd.requests == [] and source.read_bytes() == before


def test_no_such_session(repo, tmp_path, monkeypatch, capsys, fake_model):
    _source(tmp_path, monkeypatch, fake_model, repo)
    capsys.readouterr()
    assert app.main(["exec", "--fork", "nosuch", "go", "-C", str(repo)]) == 12
    assert "cannot fork: no such session: nosuch" in capsys.readouterr().err


@pytest.mark.parametrize("argv, said", [
    (("--fork", "a", "--resume", "b", "go"), "--fork and --resume each name the session"),
    (("--fork", "last", "--ephemeral", "go"), "--ephemeral starts a session that is not kept"),
    (("fork",), "exec fork needs a session id, or --last"),
    (("resume",), "exec resume needs a session id, or --last"),
])
def test_refused(repo, tmp_path, monkeypatch, capsys, fake_model, argv, said):
    home, httpd = _model(tmp_path, monkeypatch, fake_model, "never")
    assert app.main(["exec", *argv, "-C", str(repo)]) == 2
    assert said in capsys.readouterr().err
    assert httpd.requests == [] and not (home / "sessions").exists()


@pytest.mark.parametrize("argv, expected", [
    (["exec", "fork", "--last", "go", "on"], ["exec", "--fork", "last", "--", "go", "on"]),
    (["exec", "--json", "fork", "abc"], ["exec", "--json", "--fork", "abc"]),
    (["exec", "-m", "fork", "go"], ["exec", "-m", "fork", "go"]),
    (["exec", "--", "fork", "x"], ["exec", "--", "fork", "x"]),
])
def test_exec_fork_is_exec_with_fork(argv, expected):
    assert app.exec_resume(argv, app.build_parser()) == expected
