"""/resume [SESSION] (D-134), as codex's /resume: another of this
workspace's sessions goes on -- named by id or name, or picked from the
recent ones -- and this one ends, recorded and resumable.

The other is resumed with every check a resume makes; one that cannot be is
not, and this session goes on.
"""

from __future__ import annotations

import itertools
import subprocess

import pytest

from foundry.cli import app
from foundry.core import session as session_module
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore

from test_cli_e2e import text_turn
from test_fork import _model, _typed
from test_resume import _wire, fake_model, repo  # noqa: F401 (fixtures)
from test_slash_commands import _repl, _shown


@pytest.fixture(autouse=True)
def _in_order(monkeypatch):
    """Ids sort by start time to the second, and these sessions start within
    one: each a second after the last, so newest is newest."""
    seconds = itertools.count(1)
    monkeypatch.setattr(session_module, "new_session_id",
                        lambda: f"20261107T{next(seconds):06d}-0000000a")


def _said(journal) -> list[str]:
    return [m.text_content for m in read_journal(journal).history]


def _ended(journal) -> list[str]:
    return [r.payload["reason"] for r in SessionStore.read_records(journal)
            if r.type == EventType.TERMINATION]


def _earlier(tmp_path, monkeypatch, fake_model, repo, *tasks):
    """Sessions recorded before, one per task, oldest first; their ids."""
    home, httpd = _model(tmp_path, monkeypatch, fake_model)
    ids = []
    for task in tasks:
        httpd.requests.clear()
        fake_model[1].turns, fake_model[1].index = [text_turn(f"{task}: done")], 0
        assert app.main(["exec", task, "-C", str(repo)]) == 10
        ids += [s.name for s in (home / "sessions").iterdir() if s.name not in ids]
    return home, httpd, ids


def _run(monkeypatch, fake_model, repo, *lines, answers=("B is 1",)):
    fake_model[1].turns, fake_model[1].index = [text_turn(a) for a in answers], 0
    _typed(monkeypatch, *lines)
    return app.main(["run", "-C", str(repo)])


def test_a_named_session_goes_on_and_this_one_ends(repo, tmp_path, monkeypatch, capsys,
                                                   fake_model):
    home, httpd, [earlier] = _earlier(tmp_path, monkeypatch, fake_model, repo, "what is A?")
    capsys.readouterr()
    assert _run(monkeypatch, fake_model, repo, "/resume " + earlier, "and B?", "/exit") == 13
    [this] = [s for s in (home / "sessions").iterdir() if s.name != earlier]
    out = capsys.readouterr().out
    assert f"now in {earlier}; {this.name} is kept, and can be resumed" in out
    assert _ended(this / "events.jsonl") == [f"the user resumed session {earlier}"]
    said = _said(home / "sessions" / earlier / "events.jsonl")
    assert said[:2] == ["what is A?", "what is A?: done"] and said[-1] == "B is 1"
    assert said[-2].endswith("and B?")
    assert "what is A?" in [m["content"] for m in httpd.requests[-1]["messages"]]


def test_picked_from_the_recent_others(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, httpd, (older, newer) = _earlier(tmp_path, monkeypatch, fake_model, repo,
                                           "first task", "second task")
    capsys.readouterr()
    assert _run(monkeypatch, fake_model, repo, "/resume", "2", "go on", "/exit") == 13
    out = capsys.readouterr().out
    assert "recent sessions" in out and f"now in {older};" in out
    assert _said(home / "sessions" / older / "events.jsonl")[-1] == "B is 1"
    assert _said(home / "sessions" / newer / "events.jsonl")[-1] == "second task: done"


def test_enter_picks_the_newest_other(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, httpd, (older, newer) = _earlier(tmp_path, monkeypatch, fake_model, repo,
                                           "first task", "second task")
    capsys.readouterr()
    assert _run(monkeypatch, fake_model, repo, "/resume", "", "/exit") == 13
    assert f"now in {newer};" in capsys.readouterr().out


def test_none_to_pick_and_none_picked(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, httpd = _model(tmp_path, monkeypatch, fake_model)
    assert _run(monkeypatch, fake_model, repo, "/resume", "/exit") == 13
    assert "no other session in this workspace to resume" in capsys.readouterr().out
    _earlier(tmp_path, monkeypatch, fake_model, repo, "a task")
    capsys.readouterr()
    lines = iter(["/resume", None, "/exit"])

    def typed(*args):
        line = next(lines)
        if line is None:
            raise KeyboardInterrupt     # Ctrl+C at "resume which?"
        return line

    monkeypatch.setattr("builtins.input", typed)
    fake_model[1].turns, fake_model[1].index = [text_turn("never")], 0
    assert app.main(["run", "-C", str(repo)]) == 13
    assert "now in" not in capsys.readouterr().out


def test_one_that_cannot_go_on_is_not_and_this_one_goes_on(repo, tmp_path, monkeypatch,
                                                           capsys, fake_model):
    home, httpd, [earlier] = _earlier(tmp_path, monkeypatch, fake_model, repo, "what is A?")
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=repo,
                   check=True)
    capsys.readouterr()
    assert _run(monkeypatch, fake_model, repo, "/resume " + earlier, "and B?", "/exit") == 13
    out = capsys.readouterr().out
    assert f"cannot resume {earlier}:" in out and "HEAD" in out and "this session goes on" in out
    [this] = [s for s in (home / "sessions").iterdir() if s.name != earlier]
    assert _said(this / "events.jsonl") == ["and B?", "B is 1"]
    assert _said(home / "sessions" / earlier / "events.jsonl")[-1] == "what is A?: done"


def test_no_such_session(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, httpd = _model(tmp_path, monkeypatch, fake_model)
    assert _run(monkeypatch, fake_model, repo, "/resume nosuch", "go on", "/exit") == 13
    assert "cannot resume nosuch: no such session: nosuch" in capsys.readouterr().out
    [this] = list((home / "sessions").iterdir())
    assert _said(this / "events.jsonl") == ["go on", "B is 1"]


def test_this_session_is_this_session(repo, tmp_path, monkeypatch, capsys, fake_model):
    # `last` is this workspace's newest: the one running.
    _earlier(tmp_path, monkeypatch, fake_model, repo, "a task")
    capsys.readouterr()
    assert _run(monkeypatch, fake_model, repo, "/resume last", "/exit") == 13
    out = capsys.readouterr().out
    assert "that is this session" in out and "now in" not in out


def test_by_name(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, httpd, [earlier] = _earlier(tmp_path, monkeypatch, fake_model, repo, "what is A?")
    _typed(monkeypatch, f"/resume {earlier}", "/rename parser", "/exit")
    assert app.main(["run", "-C", str(repo)]) == 13
    capsys.readouterr()
    assert _run(monkeypatch, fake_model, repo, "/resume parser", "and B?", "/exit") == 13
    assert f"now in {earlier};" in capsys.readouterr().out


def test_help_lists_it(repo, tmp_path):
    shown = _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))
    assert "/resume [SESSION]" in shown
