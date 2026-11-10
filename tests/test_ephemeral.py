"""`exec --ephemeral` (D-132), as codex's: a run that keeps no record once it
ends.

Its journal is written as any session's is -- the finish gate, hooks and
artifacts need it while the run goes on -- but in a folder of its own,
outside the sessions, so nothing lists or resumes it even meanwhile; and the
folder is removed as the session closes, however the run ended.
"""

from __future__ import annotations

import json
import tempfile

import pytest

from foundry.cli import app
from foundry.cli.wiring import _close

from test_cli_e2e import text_turn, tool_turn
from test_cli_flags import _exec_home
from test_resume import fake_model, repo  # noqa: F401 (fixtures)

DONE = tool_turn("finish", {"status": "completed", "summary": "done", "claims": []}, "f1")


@pytest.fixture()
def folders(tmp_path, monkeypatch):
    """Where ephemeral journals go, and the journal each run closed."""
    made: list = []
    closed: list = []

    def mkdtemp(prefix="", **kwargs):
        made.append(tmp_path / f"{prefix}{len(made)}")
        made[-1].mkdir()
        return str(made[-1])

    real_close = _close

    def close(wiring, status, reason):
        closed.append((wiring.session.path, wiring.session.path.exists()))
        real_close(wiring, status, reason)

    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr("foundry.cli.unattended._close", close)
    return made, closed


def _home(tmp_path, monkeypatch, fake_model, *turns):
    httpd, handler = fake_model
    home = _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = list(turns) or [DONE], 0
    return home


def _kept(home) -> list:
    sessions = home / "sessions"
    return sorted(sessions.iterdir()) if sessions.exists() else []


def test_nothing_is_kept(repo, tmp_path, monkeypatch, capsys, fake_model, folders):
    made, closed = folders
    home = _home(tmp_path, monkeypatch, fake_model)
    assert app.main(["exec", "--ephemeral", "--json", "fix it", "-C", str(repo)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert events[0]["kind"] == "session_started" and events[0]["ephemeral"] is True
    assert events[-1]["status"] == "completed"
    [(journal, existed)] = closed
    assert existed and journal.parent.parent == made[0]     # written while it ran
    assert journal.parent.name == events[0]["session_id"]
    assert not made[0].exists()                             # and removed
    assert _kept(home) == []


def test_it_says_so(repo, tmp_path, monkeypatch, capsys, fake_model, folders):
    _home(tmp_path, monkeypatch, fake_model)
    assert app.main(["exec", "--ephemeral", "fix it", "-C", str(repo)]) == 0
    err = capsys.readouterr().err
    assert "(ephemeral: not kept)" in err


def test_a_kept_run_is_kept_and_says_nothing(repo, tmp_path, monkeypatch, capsys, fake_model,
                                             folders):
    made, _ = folders
    home = _home(tmp_path, monkeypatch, fake_model)
    assert app.main(["exec", "--json", "fix it", "-C", str(repo)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert events[0]["ephemeral"] is False
    assert [s.name for s in _kept(home)] == [events[0]["session_id"]]
    assert made == []


def test_removed_however_it_ends(repo, tmp_path, monkeypatch, capsys, fake_model, folders):
    made, _ = folders
    home = _home(tmp_path, monkeypatch, fake_model)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("foundry.cli.unattended._run_unattended", interrupted)
    assert app.main(["exec", "--ephemeral", "fix it", "-C", str(repo)]) == 13
    assert len(made) == 1 and not made[0].exists() and _kept(home) == []


def test_removed_when_the_session_cannot_start(repo, tmp_path, monkeypatch, capsys,
                                               fake_model, folders):
    from foundry.core.errors import ConfigError

    made, _ = folders
    _home(tmp_path, monkeypatch, fake_model)

    def broken(*args, **kwargs):
        raise ConfigError("no tools today")

    monkeypatch.setattr("foundry.cli.wiring._assemble", broken)
    assert app.main(["exec", "--ephemeral", "fix it", "-C", str(repo)]) == 12
    assert "no tools today" in capsys.readouterr().err
    assert len(made) == 1 and not made[0].exists()


def test_a_partial_run_is_removed_too(repo, tmp_path, monkeypatch, capsys, fake_model,
                                      folders):
    made, _ = folders
    _home(tmp_path, monkeypatch, fake_model, text_turn("done, I think"))
    assert app.main(["exec", "--ephemeral", "fix it", "-C", str(repo)]) == 10
    assert not made[0].exists()


def test_it_cannot_continue_a_session(repo, tmp_path, monkeypatch, capsys, fake_model,
                                      folders):
    made, _ = folders
    httpd = fake_model[0]
    _home(tmp_path, monkeypatch, fake_model)
    assert app.main(["exec", "--ephemeral", "--resume", "last", "-C", str(repo)]) == 2
    assert "--ephemeral starts a session that is not kept" in capsys.readouterr().err
    assert httpd.requests == [] and made == []


def test_nothing_to_resume_afterwards(repo, tmp_path, monkeypatch, capsys, fake_model,
                                      folders):
    _home(tmp_path, monkeypatch, fake_model)
    assert app.main(["exec", "--ephemeral", "fix it", "-C", str(repo)]) == 0
    capsys.readouterr()
    assert app.main(["exec", "--resume", "last", "-C", str(repo)]) == 12
    assert "no recorded session in" in capsys.readouterr().err


def test_a_review_can_be_ephemeral_too(repo, tmp_path, monkeypatch, capsys, fake_model,
                                       folders):
    made, _ = folders
    home = _home(tmp_path, monkeypatch, fake_model)
    assert app.main(["review", "--uncommitted", "--ephemeral", "-C", str(repo)]) == 0
    assert len(made) == 1 and not made[0].exists() and _kept(home) == []
