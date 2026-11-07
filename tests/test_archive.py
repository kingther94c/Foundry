"""`foundry archive`, `unarchive` and `delete`, `/archive` and `/delete`
(D-128), as codex's.

An archived session's folder moves whole to ``archived_sessions/``, beside
``sessions/``: out of every list and lookup until it is unarchived, and a
resume of it says so. A delete is for good, so it is asked first; ``--force``
skips the question and takes only an id. Neither touches a session a store
holds.
"""

from __future__ import annotations

import builtins

import pytest

from foundry.cli import app
from foundry.core.errors import ResumeError, SessionLocked
from foundry.core.resume import (
    ARCHIVED,
    delete_session,
    move_session,
    resolve_session,
    session_named,
)
from foundry.core.session import EventType, SessionStore

from test_rename import _session, _session_id
from test_fork import _model
from test_resume import _wire, fake_model, repo  # noqa: F401  (fixtures)
from test_resume_picker import home  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown


def _typed(monkeypatch, *lines):
    left = iter(lines)
    monkeypatch.setattr(builtins, "input", lambda *a: next(left))


def _ids(root) -> list[str]:
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


# --- archive and unarchive ------------------------------------------------------------


def test_archive_moves_it_out_of_every_list_and_back(home, repo, capsys):
    sid = _session(home, repo, 1, "parser")
    assert app.main(["archive", sid]) == 0
    assert _ids(home / "sessions") == [] and _ids(home / ARCHIVED) == [sid]
    assert (home / ARCHIVED / sid / "events.jsonl").is_file()
    out = capsys.readouterr().out
    assert f"archived session {sid}; foundry unarchive {sid} brings it back" in out
    assert app.main(["sessions"]) == 0
    assert sid not in capsys.readouterr().out
    assert app.main(["sessions", "--archived"]) == 0
    assert sid in capsys.readouterr().out
    assert app.main(["unarchive", "parser"]) == 0
    assert _ids(home / "sessions") == [sid] and _ids(home / ARCHIVED) == []
    assert f"unarchived session {sid}; foundry resume {sid} continues it" in \
        capsys.readouterr().out


def test_by_name_the_newest_of_that_name(home, repo, tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    _session(home, repo, 1, "parser")
    newest = _session(home, other, 2, "parser")
    assert app.main(["archive", "parser"]) == 0
    assert _ids(home / ARCHIVED) == [newest]


def test_resuming_an_archived_session_says_how_to_bring_it_back(home, repo):
    sid = _session(home, repo, 1, "parser")
    app.main(["archive", sid])
    for target in (sid, "parser"):
        with pytest.raises(ResumeError, match=f"session {target} is archived; foundry "
                                              f"unarchive {target} brings it back"):
            resolve_session(home / "sessions", target, repo)
    with pytest.raises(ResumeError, match="no such session: lexer"):
        resolve_session(home / "sessions", "lexer", repo)


@pytest.mark.parametrize("command, said", [
    (["archive", "nothing"], "no such session: nothing"),
    (["unarchive", "nothing"], "no such session: nothing"),
])
def test_a_session_there_is_not(home, repo, capsys, command, said):
    _session(home, repo, 1)
    assert app.main(command) == 1
    assert said in capsys.readouterr().out
    assert _ids(home / "sessions") == [_session_id(1)]


def test_archived_twice_or_unarchived_when_it_is_not(home, repo, capsys):
    sid = _session(home, repo, 1)
    assert app.main(["unarchive", sid]) == 1
    assert f"session {sid} is not archived" in capsys.readouterr().out
    app.main(["archive", sid])
    capsys.readouterr()
    assert app.main(["archive", sid]) == 1
    assert f"session {sid} is already archived" in capsys.readouterr().out


def test_a_session_in_use_is_not_moved(home, repo):
    sid = _session(home, repo, 1)
    store = SessionStore(home / "sessions", session_id=sid, resume=True)
    try:
        with pytest.raises(SessionLocked, match=f"session {sid} is in use"):
            move_session(home / "sessions" / sid, home / ARCHIVED / sid)
        with pytest.raises(SessionLocked):
            delete_session(home / "sessions" / sid)
        assert app.main(["archive", sid]) == 1
    finally:
        store.close()
    assert _ids(home / "sessions") == [sid] and _ids(home / ARCHIVED) == []


def test_nothing_is_moved_over_another(home, repo):
    sid = _session(home, repo, 1)
    (home / ARCHIVED / sid).mkdir(parents=True)
    with pytest.raises(ResumeError, match="already exists"):
        move_session(home / "sessions" / sid, home / ARCHIVED / sid)
    assert (home / "sessions" / sid / "events.jsonl").is_file()


def test_a_session_is_named_by_id_first(home, repo):
    first = _session(home, repo, 1)
    _session(home, repo, 2, first)       # another, named as the first one's id
    assert session_named(home / "sessions", first) == first
    assert session_named(home / "sessions", "../sessions") is None


def test_no_archived_sessions(home, capsys):
    assert app.main(["sessions", "--archived"]) == 0
    assert "no archived sessions" in capsys.readouterr().out


# --- delete -----------------------------------------------------------------------------


def test_delete_asks_and_a_no_keeps_it(home, repo, monkeypatch, capsys):
    sid = _session(home, repo, 1, "parser")
    _typed(monkeypatch, "n")
    assert app.main(["delete", "parser"]) == 1
    out = capsys.readouterr().out
    assert "parser" in out and "task 1" in out and "nothing was deleted" in out
    assert _ids(home / "sessions") == [sid]


def test_delete_after_a_yes(home, repo, monkeypatch, capsys):
    sid = _session(home, repo, 1)
    _typed(monkeypatch, "y")
    assert app.main(["delete", sid]) == 0
    assert _ids(home / "sessions") == []
    assert f"deleted session {sid}" in capsys.readouterr().out


def test_delete_with_force_takes_only_an_id(home, repo, capsys):
    sid = _session(home, repo, 1, "parser")
    assert app.main(["delete", "--force", "parser"]) == 2
    assert "--force takes a session id" in capsys.readouterr().out
    assert _ids(home / "sessions") == [sid]
    assert app.main(["delete", "--force", sid]) == 0
    assert _ids(home / "sessions") == []


def test_an_archived_session_can_be_deleted(home, repo):
    sid = _session(home, repo, 1)
    app.main(["archive", sid])
    assert app.main(["delete", "--force", sid]) == 0
    assert _ids(home / ARCHIVED) == []


def test_delete_a_session_there_is_not(home, capsys):
    assert app.main(["delete", "--force", "nothing"]) == 1
    assert "no such session: nothing" in capsys.readouterr().out


def test_a_delete_that_fails_says_so(home, repo, monkeypatch, capsys):
    sid = _session(home, repo, 1)

    def broken(folder):
        raise OSError("held open")

    monkeypatch.setattr("foundry.core.resume.shutil.rmtree", broken)
    assert app.main(["delete", "--force", sid]) == 1
    assert f"cannot delete {sid}: held open" in capsys.readouterr().out


# --- /archive and /delete ---------------------------------------------------------------


def test_slash_archive_ends_the_session_and_archives_it(repo, tmp_path, monkeypatch, capsys,
                                                       fake_model):
    home, _ = _model(tmp_path, monkeypatch, fake_model, "A is 1")
    _typed(monkeypatch, "what is A?", "/archive")
    assert app.main(["run", "--workspace", str(repo)]) == 13
    (sid,) = _ids(home / ARCHIVED)
    assert _ids(home / "sessions") == []
    records = list(SessionStore.read_records(home / ARCHIVED / sid / "events.jsonl"))
    assert records[-1].type == EventType.TERMINATION
    assert records[-1].payload["reason"] == "the user archived this session"
    out = capsys.readouterr().out
    assert f"archived session {sid}; foundry unarchive {sid} brings it back" in out
    assert "To continue this session" not in out


def test_slash_delete_asks_then_deletes(repo, tmp_path, monkeypatch, capsys, fake_model):
    home, _ = _model(tmp_path, monkeypatch, fake_model, "A is 1")
    _typed(monkeypatch, "what is A?", "/delete", "n", "/delete", "y")
    assert app.main(["run", "--workspace", str(repo)]) == 13
    assert _ids(home / "sessions") == [] and _ids(home / ARCHIVED) == []
    out = capsys.readouterr().out
    assert "nothing was deleted" in out and "deleted session " in out


def test_a_session_that_cannot_be_archived_says_so(repo, tmp_path, monkeypatch, capsys,
                                                    fake_model):
    _model(tmp_path, monkeypatch, fake_model)

    def broken(source, destination):
        raise OSError("held open")

    monkeypatch.setattr(app, "_move_session", broken)
    _typed(monkeypatch, "/archive")
    assert app.main(["run", "--workspace", str(repo)]) == 12
    assert "cannot archive session " in capsys.readouterr().out


def test_help_lists_them(repo, tmp_path):
    shown = _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))
    assert "/archive " in shown and "/delete " in shown
