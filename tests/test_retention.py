"""How long a recorded session is kept (OQ-19, D-163).

``runtime.session_retention_days`` is the user's: 0, the default, keeps every
session; N removes, as a session starts, the sessions whose journals were last
written more than N days ago, and says so in the new session's journal and on
screen. A repository or a foundry mcp-server client may not set it. An
archived session, a session in use, a folder that is not a session's, and
anything reached through a junction are never removed. `foundry prune` does
the same on demand, listed and asked first.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import sys
import time

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.wiring import _announce_found, _close, build
from foundry.core.config import load_config
from foundry.core.errors import ConfigError
from foundry.core.events import Notice, TerminalStatus
from foundry.core.resume import ARCHIVED, summarize
from foundry.core.retention import DAY_S, expired, sweep
from foundry.core.session import EventType, SessionStore

from test_cli_e2e import tool_turn
from test_cli_flags import _exec_home
from test_rename import _session
from test_resume import OVERRIDES, _wire, fake_model, repo, turn  # noqa: F401  (fixtures)
from test_resume_picker import home  # noqa: F401  (fixture)

NOW = time.time()


def _aged(home, number, days, *, root="sessions"):
    """A recorded session whose journal was last written ``days`` days ago."""
    sid = _session(home, home.parent / "project", number)
    folder = home / "sessions" / sid
    if root != "sessions":
        (home / root).mkdir(parents=True, exist_ok=True)
        folder = folder.rename(home / root / sid)
    then = NOW - days * DAY_S
    os.utime(folder / "events.jsonl", (then, then))
    return sid


def _ids(root) -> list[str]:
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def _user_config(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def _typed(monkeypatch, *lines):
    left = iter(lines)
    monkeypatch.setattr(builtins, "input", lambda *a: next(left))


# --- the setting -------------------------------------------------------------------


def test_every_session_is_kept_unless_the_user_says_otherwise(home):
    assert load_config(home=home).session_retention_days == 0


@pytest.mark.parametrize("days", [0, 1, 90])
def test_the_user_sets_it(home, days):
    _user_config(home, f"[runtime]\nsession_retention_days = {days}\n")
    config = load_config(home=home)
    assert config.session_retention_days == days
    assert config.provenance["session_retention_days"].layer == "user"


def test_a_command_line_sets_it(home):
    config = load_config(home=home, settings={"runtime": {"session_retention_days": 3}})
    assert config.session_retention_days == 3


def test_a_repository_may_not_set_it_even_lower(home, tmp_path):
    workspace = tmp_path / "project"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        "[runtime]\nsession_retention_days = 1\n", encoding="utf-8")
    _user_config(home, "[runtime]\nsession_retention_days = 30\n")
    with pytest.raises(ConfigError, match="project config may not set "
                                          "session_retention_days: how long your sessions "
                                          "are kept is yours to say"):
        load_config(workspace, home=home)


def test_a_client_may_not_set_it(home):
    with pytest.raises(ConfigError, match="may not set session_retention_days"):
        load_config(home=home, client_settings={"runtime": {"session_retention_days": 1}})


@pytest.mark.parametrize("value", ["-1", "1.5", "true", '"7"'])
def test_only_a_whole_number_of_days(home, value):
    _user_config(home, f"[runtime]\nsession_retention_days = {value}\n")
    with pytest.raises(ConfigError, match="session_retention_days must be a whole number "
                                          "of days, 0 to keep every session"):
        load_config(home=home)


# --- which sessions have expired ---------------------------------------------------


def test_expired_are_those_last_written_before_the_cutoff_oldest_first(home):
    old = _aged(home, 1, 40)
    older = _aged(home, 2, 90)
    _aged(home, 3, 5)
    sessions = home / "sessions"
    assert expired(sessions, 30, NOW) == [sessions / older, sessions / old]
    assert expired(sessions, 60, NOW) == [sessions / older]
    assert expired(sessions, 100, NOW) == []


def test_none_expire_at_zero_or_without_sessions(home):
    _aged(home, 1, 4000)
    assert expired(home / "sessions", 0, NOW) == []
    assert expired(home / "nothing", 1, NOW) == []


def test_a_folder_that_is_not_a_session_is_not_judged(home):
    _aged(home, 1, 1)
    sessions = home / "sessions"
    notes = sessions / "notes"
    notes.mkdir()
    (notes / "events.jsonl").write_text("", encoding="utf-8")
    bare = sessions / "20200101T000000-0000abcd"
    bare.mkdir()                                     # an id, but no journal
    then = NOW - 400 * DAY_S
    os.utime(notes / "events.jsonl", (then, then))
    os.utime(bare, (then, then))
    assert expired(sessions, 30, NOW) == []
    assert sweep(sessions, 30, now=NOW).removed == ()
    assert notes.is_dir() and bare.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is Windows'")
def test_nothing_is_removed_through_a_junction(home, tmp_path):
    import _winapi

    sid = _aged(home, 1, 90)
    target = tmp_path / "elsewhere" / sid
    target.parent.mkdir()
    (home / "sessions" / sid).rename(target)
    _winapi.CreateJunction(str(target), str(home / "sessions" / sid))
    assert expired(home / "sessions", 30, NOW) == []
    assert (target / "events.jsonl").is_file()


def test_listing_a_session_does_not_make_it_new(home):
    sid = _aged(home, 1, 90)
    summarize(home / "sessions" / sid)              # asks whether it is in use
    assert expired(home / "sessions", 30, NOW) == [home / "sessions" / sid]


# --- the sweep ---------------------------------------------------------------------


def test_the_sweep_removes_the_expired_and_keeps_the_rest(home):
    old = _aged(home, 1, 40)
    recent = _aged(home, 2, 5)
    swept = sweep(home / "sessions", 30, now=NOW)
    assert swept.removed == (old,) and swept.left == ()
    assert _ids(home / "sessions") == [recent]


def test_an_archived_session_is_kept_whole(home):
    archived = _aged(home, 1, 400, root=ARCHIVED)
    assert sweep(home / "sessions", 1, now=NOW).removed == ()
    assert _ids(home / ARCHIVED) == [archived]


def test_a_session_in_use_is_left_for_next_time(home):
    sid = _aged(home, 1, 90)
    store = SessionStore(home / "sessions", session_id=sid, resume=True)
    try:
        os.utime(store.path, (NOW - 90 * DAY_S,) * 2)
        swept = sweep(home / "sessions", 30, now=NOW)
    finally:
        store.close()
    assert swept.removed == () and swept.left == (sid,)
    assert _ids(home / "sessions") == [sid]


def test_one_that_cannot_be_removed_does_not_stop_the_rest(home, monkeypatch):
    stuck = _aged(home, 1, 90)
    gone = _aged(home, 2, 80)
    real = __import__("shutil").rmtree

    def rmtree(folder, *a, **k):
        if os.path.basename(folder) == stuck:
            raise OSError("held open")
        return real(folder, *a, **k)

    monkeypatch.setattr("foundry.core.resume.shutil.rmtree", rmtree)
    swept = sweep(home / "sessions", 30, now=NOW)
    assert swept.removed == (gone,) and swept.left == (stuck,)


# --- as a session starts -----------------------------------------------------------


def _start(repo, home, **kwargs):
    console = Console(file=io.StringIO(), width=200)
    wiring = build(repo, home=home, overrides=dict(OVERRIDES), console=console, **kwargs)
    return wiring, console


def _pruned(wiring):
    return [r.payload for r in SessionStore.read_records(wiring.session.path)
            if r.type == EventType.SESSIONS_PRUNED]


def test_a_session_starting_removes_what_has_expired_and_says_so(home, repo):
    _user_config(home, "[runtime]\nsession_retention_days = 30\n")
    old = _aged(home, 1, 40)
    recent = _aged(home, 2, 5)
    wiring, console = _start(repo, home)
    try:
        assert _pruned(wiring) == [{"retention_days": 30, "removed": [old], "left": []}]
        assert sorted(_ids(home / "sessions")) == sorted([recent, wiring.session.session_id])
        # Said once the front end listens (D-168), as what a resume found is.
        assert "session_retention_days" not in console.file.getvalue()
        _announce_found(wiring)
        assert ("removed 1 session(s) last written more than 30 day(s) ago "
                "(session_retention_days)") in console.file.getvalue()
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "session ended")


def test_exec_json_hears_what_went_once_the_session_has_started(repo, tmp_path, monkeypatch,
                                                                capsys, fake_model):
    """Found in P9 (D-168): said as the session was built, before exec --json
    was listening, so a script never heard that sessions were deleted."""
    httpd, handler = fake_model
    home = _exec_home(tmp_path, httpd, monkeypatch)
    with open(home / "config.toml", "a", encoding="utf-8") as config:
        config.write("[runtime]\nsession_retention_days = 30\n")
    old = _aged(home, 1, 40)
    handler.turns, handler.index = [tool_turn("finish", {"status": "completed",
                                                         "summary": "done", "claims": []},
                                              "f1")], 0
    assert app.main(["exec", "--json", "fix it", "-C", str(repo)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert events[0]["kind"] == "session_started"
    assert [e["text"] for e in events if e["kind"] == "notice"][:1] == [
        "removed 1 session(s) last written more than 30 day(s) ago (session_retention_days)"]
    assert old not in _ids(home / "sessions")


def test_what_is_left_for_next_time_is_said_and_journaled(home, repo):
    _user_config(home, "[runtime]\nsession_retention_days = 30\n")
    held = _aged(home, 1, 90)
    store = SessionStore(home / "sessions", session_id=held, resume=True)
    try:
        os.utime(store.path, (NOW - 90 * DAY_S,) * 2)
        wiring, console = _start(repo, home)
        try:
            assert _pruned(wiring) == [{"retention_days": 30, "removed": [], "left": [held]}]
            _announce_found(wiring)
            assert ("removed 0 session(s) last written more than 30 day(s) ago "
                    "(session_retention_days); 1 in use or not removable, left for next "
                    "time") in console.file.getvalue()
        finally:
            _close(wiring, TerminalStatus.CANCELLED, "session ended")
    finally:
        store.close()


SWEPT = "removed 2 session(s) last written more than 30 day(s) ago (session_retention_days)"


def _notices(wiring) -> list[str]:
    heard: list[str] = []
    wiring.runtime.events.subscribe(
        lambda event: heard.append(event.text) if isinstance(event, Notice) else None)
    return heard


def test_new_says_what_its_session_s_start_removed(repo, tmp_path, monkeypatch):
    monkeypatch.setattr("foundry.cli.session_commands._interactive", lambda: True)
    first = _wire(repo, tmp_path / "home", [])
    second = _wire(repo, tmp_path / "home", [])
    second.swept = SWEPT
    heard = _notices(second)
    built = iter([first, second])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: next(built))
    _typed(monkeypatch, "/new", "/exit")
    app.main(["run", "--workspace", str(repo)])
    assert heard == [SWEPT]


def test_record_says_what_its_session_s_start_removed(repo, tmp_path, monkeypatch):
    wiring = _wire(repo, tmp_path / "home", [turn(text="done")])
    wiring.swept = SWEPT
    heard = _notices(wiring)
    monkeypatch.setattr("foundry.cli.unattended.build", lambda *a, **k: wiring)
    app.main(["record", "a task", "--output", str(tmp_path / "f.json"),
              "--workspace", str(repo)])
    assert heard[:1] == [SWEPT]


def test_by_default_a_session_starting_removes_nothing(home, repo):
    old = _aged(home, 1, 4000)
    wiring, console = _start(repo, home)
    try:
        assert _pruned(wiring) == []
        assert old in _ids(home / "sessions")
        assert "session_retention_days" not in console.file.getvalue()
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "session ended")


def test_nothing_expired_nothing_journaled(home, repo):
    _user_config(home, "[runtime]\nsession_retention_days = 30\n")
    _aged(home, 1, 5)
    wiring, console = _start(repo, home)
    try:
        assert _pruned(wiring) == []
        assert "session_retention_days" not in console.file.getvalue()
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "session ended")


def test_the_session_being_resumed_is_never_swept(home, repo):
    first, _ = _start(repo, home)
    sid = first.session.session_id
    _close(first, TerminalStatus.CANCELLED, "session ended")
    _user_config(home, "[runtime]\nsession_retention_days = 30\n")
    then = NOW - 90 * DAY_S
    os.utime(home / "sessions" / sid / "events.jsonl", (then, then))
    wiring, _ = _start(repo, home, resume=sid)
    try:
        assert wiring.session.session_id == sid
        assert _ids(home / "sessions") == [sid]
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "session ended")


# --- foundry prune -----------------------------------------------------------------


def test_prune_dry_run_lists_and_deletes_nothing(home, capsys):
    old = _aged(home, 1, 40)
    assert app.main(["prune", "--days", "30", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert old in out and "1 session(s) would be removed; nothing was" in out
    assert _ids(home / "sessions") == [old]


def test_prune_asks_and_a_no_keeps_them(home, monkeypatch, capsys):
    old = _aged(home, 1, 40)
    _typed(monkeypatch, "n")
    assert app.main(["prune", "--days", "30"]) == 1
    assert "nothing was deleted" in capsys.readouterr().out
    assert _ids(home / "sessions") == [old]


def test_prune_after_a_yes(home, monkeypatch, capsys):
    _aged(home, 1, 40)
    recent = _aged(home, 2, 5)
    _typed(monkeypatch, "y")
    assert app.main(["prune", "--days", "30"]) == 0
    assert "deleted 1 session(s)" in capsys.readouterr().out
    assert _ids(home / "sessions") == [recent]


def test_prune_reads_the_setting(home, capsys):
    _user_config(home, "[runtime]\nsession_retention_days = 30\n")
    _aged(home, 1, 40)
    recent = _aged(home, 2, 5)
    assert app.main(["prune", "--force"]) == 0
    assert _ids(home / "sessions") == [recent]


def test_prune_with_every_session_kept_says_how(home, capsys):
    old = _aged(home, 1, 4000)
    assert app.main(["prune", "--force"]) == 0
    assert "session_retention_days is 0: every session is kept" in capsys.readouterr().out
    assert _ids(home / "sessions") == [old]


def test_prune_takes_at_least_a_day(home, capsys):
    _aged(home, 1, 40)
    assert app.main(["prune", "--days", "0", "--force"]) == 2
    assert "at least 1" in capsys.readouterr().out
    assert len(_ids(home / "sessions")) == 1


def test_prune_with_nothing_expired(home, capsys):
    _aged(home, 1, 5)
    assert app.main(["prune", "--days", "30"]) == 0
    assert "no session was last written more than 30 day(s) ago" in capsys.readouterr().out


def test_doctor_shows_the_setting(home, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    app.main(["doctor"])
    assert "sessions  every one kept (session_retention_days)" in capsys.readouterr().out
    _user_config(home, "[runtime]\nsession_retention_days = 14\n")
    app.main(["doctor"])
    assert ("sessions  kept 14 day(s) after their last write (session_retention_days)"
            in capsys.readouterr().out)
