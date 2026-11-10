"""`foundry resume` with no id: pick from this workspace's recent sessions (D-057).

What carries the feature: a person at a terminal chooses, with the newest as
the default and nothing resumed if they choose none; a script still gets the
newest, as before; and only this workspace's sessions are offered.
"""

from __future__ import annotations

import builtins

import pytest

from foundry.cli import app
from foundry.cli.session_commands import PICK_LIMIT, pick_session
from foundry.cli.wiring import _close
from foundry.core import session as session_module
from foundry.core.errors import FoundryError
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.resume import recent_sessions, summarize
from foundry.core.session import EventType, SessionStore

from test_resume import _wire, repo, turn  # noqa: F401 (fixture)


def _session(home, workspace, number, task="", status=None):
    """A journal with a known id, so 'newest' does not depend on the clock."""
    store = SessionStore(home / "sessions", session_id=f"20261107T0000{number:02d}-0000000{number}")
    store.write_header(workspace=str(workspace), profile="", model="m", foundry_version="t")
    if task:
        store.append(EventType.USER_INPUT, {"text": task})
        store.append(EventType.USER_INPUT, {"text": "a later message"})
    if status:
        store.append(EventType.TERMINATION, {"status": status, "reason": ""})
    store.close()
    return store.session_id


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    return home


class _Answers:
    """A console that answers the picker's question from a list."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.printed: list[str] = []
        self.asked = 0

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))

    def input(self, prompt=""):
        self.asked += 1
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


# --- what is offered ---------------------------------------------------------------


def test_this_workspaces_newest_sessions_come_first(home, repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    first = _session(home, repo, 1, "fix the parser", "completed")
    _session(home, other, 2, "elsewhere")
    third = _session(home, repo, 3, "add a flag\nwith details", "partial")
    fourth = _session(home, repo, 4)
    recent = recent_sessions(home / "sessions", repo, limit=10)
    assert [s.session_id for s in recent] == [fourth, third, first]
    assert [s.status for s in recent] == [TerminalStatus.INTERRUPTED, TerminalStatus.PARTIAL,
                                          TerminalStatus.COMPLETED]
    assert [s.first_task for s in recent] == ["", "add a flag\nwith details", "fix the parser"]
    assert [s.session_id for s in recent_sessions(home / "sessions", repo, limit=2)] == [
        fourth, third]
    assert recent_sessions(tmp_path / "nowhere", repo, limit=10) == []


def test_a_session_held_by_a_store_is_in_use(home, repo):
    sid = _session(home, repo, 1, "task")
    with SessionStore(home / "sessions", session_id=sid, resume=True):
        assert summarize(home / "sessions" / sid).in_use
    assert not summarize(home / "sessions" / sid).in_use


# --- choosing ----------------------------------------------------------------------


def test_enter_is_the_newest_and_a_number_picks(home, repo):
    older = _session(home, repo, 1, "fix the parser", "completed")
    newer = _session(home, repo, 2, "add a flag\nwith these details")
    console = _Answers([""])
    assert pick_session(console, repo) == newer
    shown = "\n".join(console.printed)
    assert " 1  " + newer in shown and " 2  " + older in shown
    assert "fix the parser" in shown and "completed" in shown and "interrupted" in shown
    assert "add a flag" in shown and "with these details" not in shown
    assert pick_session(_Answers(["2"]), repo) == older


def test_a_wrong_answer_is_asked_again(home, repo):
    _session(home, repo, 1, "a")
    newer = _session(home, repo, 2, "b")
    console = _Answers(["0", "3", "x", " 1 "])
    assert pick_session(console, repo) == newer
    assert console.asked == 4
    assert sum("a number from 1 to 2" in p for p in console.printed) == 3


@pytest.mark.parametrize("stop", [KeyboardInterrupt(), EOFError()])
def test_ctrl_c_resumes_none(home, repo, stop):
    _session(home, repo, 1, "a")
    _session(home, repo, 2, "b")
    assert pick_session(_Answers([stop]), repo) is None


@pytest.mark.parametrize("count", [0, 1])
def test_with_nothing_to_choose_between_it_does_not_ask(home, repo, count):
    for number in range(count):
        _session(home, repo, number + 1, "a")
    console = _Answers([])
    assert pick_session(console, repo) == "last"
    assert console.asked == 0 and console.printed == []


def test_at_most_ten_are_offered_and_a_long_message_is_cut(home, repo):
    for number in range(1, 13):
        _session(home, repo, number, "word " * 40)
    console = _Answers([""])
    pick_session(console, repo)
    lines = [p for p in console.printed if "-0000000" in p]
    assert len(lines) == PICK_LIMIT
    assert all(("word " * 40).strip() not in line and "..." in line for line in lines)


# --- through the command line ----------------------------------------------------------


def _resumed(monkeypatch, interactive, answer=""):
    monkeypatch.setattr("foundry.cli.session_commands._interactive", lambda: interactive)
    monkeypatch.setattr(builtins, "input", lambda prompt="": answer)
    seen = {}

    def build(workspace, *, console, resume=None, **flags):
        seen["resume"] = resume
        raise FoundryError("stop here")

    monkeypatch.setattr("foundry.cli.session_commands.build", build)
    return seen


def test_at_a_terminal_resume_asks(home, repo, monkeypatch):
    older = _session(home, repo, 1, "a")
    _session(home, repo, 2, "b")
    seen = _resumed(monkeypatch, True, "2")
    assert app.main(["resume", "--workspace", str(repo)]) == EXIT_CODES[TerminalStatus.FAILED]
    assert seen["resume"] == older


def test_choosing_none_starts_nothing(home, repo, monkeypatch):
    _session(home, repo, 1, "a")
    _session(home, repo, 2, "b")
    seen = _resumed(monkeypatch, True)

    def interrupted(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupted)
    assert app.main(["resume", "--workspace", str(repo)]) == EXIT_CODES[TerminalStatus.CANCELLED]
    assert seen == {}


@pytest.mark.parametrize("argv, interactive, expected", [
    (["resume"], False, "last"),
    # Asked, Enter would pick the newest, ...02: these are not asked.
    (["resume", "last"], True, "last"),
    (["resume", "20261107T000001-00000001"], True, "20261107T000001-00000001"),
])
def test_a_script_gets_the_newest_and_a_named_session_is_not_asked_about(
        home, repo, monkeypatch, argv, interactive, expected):
    _session(home, repo, 1, "a")
    _session(home, repo, 2, "b")
    seen = _resumed(monkeypatch, interactive)
    app.main([*argv, "--workspace", str(repo)])
    assert seen["resume"] == expected


def test_run_never_asks(home, repo, monkeypatch):
    _session(home, repo, 1, "a")
    _session(home, repo, 2, "b")
    seen = _resumed(monkeypatch, True)
    app.main(["run", "--workspace", str(repo)])
    assert seen["resume"] is None


def test_the_picked_session_resumes(home, repo, monkeypatch):
    # Ids have a second's resolution: two sessions started in one second
    # would sort by their random part.
    ids = iter(["20261107T000001-0000000a", "20261107T000002-0000000b"])
    monkeypatch.setattr(session_module, "new_session_id", lambda: next(ids))
    first = _wire(repo, home, [turn(text="one")])
    first.runtime.run_turn("the first task")
    _close(first, TerminalStatus.CANCELLED, "done")
    second = _wire(repo, home, [turn(text="two")])
    second.runtime.run_turn("the second task")
    _close(second, TerminalStatus.CANCELLED, "done")
    picked = pick_session(_Answers(["2"]), repo)
    assert picked == first.session.session_id
    third = _wire(repo, home, [], resume=picked)
    try:
        assert third.runtime.context.history[0].text_content == "the first task"
    finally:
        _close(third, TerminalStatus.CANCELLED, "done")


# --- foundry sessions --------------------------------------------------------------


def test_the_listing_shows_each_sessions_first_message(home, repo, capsys):
    _session(home, repo, 1, "fix the parser", "completed")
    _session(home, repo, 2)
    assert app.main(["sessions"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("20261107T000002-00000002  interrupted  (no message)")
    assert out[1].startswith("20261107T000001-00000001  completed  fix the parser")
