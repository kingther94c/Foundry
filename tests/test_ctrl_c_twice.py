"""Ctrl+C at the prompt, once, says how to end the session; twice in a row
ends it (D-077), as codex asks for a second one.

What carries the feature: a Ctrl+C meant for a task that has just ended --
or a second one pressed to be sure it stopped -- no longer ends the session,
while ending it stays one key away and end of input still ends it at once.
"""

from __future__ import annotations

from foundry.cli.repl import CTRL_C_AGAIN, repl
from foundry.cli.wiring import _close
from foundry.core.events import TerminalStatus
from foundry.core.session import SessionStore

from test_resume import _wire, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _Recorder, _sent


class _Keys(_Recorder):
    """A typist whose lines may be keys: KeyboardInterrupt or EOFError."""

    def input(self, prompt=""):
        line = self.lines.pop(0)
        if isinstance(line, type) and issubclass(line, BaseException):
            raise line
        return line


def _session(repo, tmp_path, turns, *keys):
    wiring = _wire(repo, tmp_path / "home", turns)
    typist = _Keys(list(keys))
    status = repl(wiring, typist)
    path = wiring.session.path
    _close(wiring, status, "session ended")
    return wiring, typist, status, path


def _hints(typist):
    return sum(1 for line in typist.printed if line == CTRL_C_AGAIN)


def test_one_ctrl_c_says_how_and_the_session_goes_on(repo, tmp_path):
    wiring, typist, status, _ = _session(repo, tmp_path, [turn(text="Hello.")],
                                         KeyboardInterrupt, "hi", "/exit")
    assert _hints(typist) == 1 and _sent(wiring) == ["hi"]
    assert typist.lines == []                            # /exit was read: it ended it


def test_two_in_a_row_end_it(repo, tmp_path):
    _, typist, status, path = _session(repo, tmp_path, [],
                                       KeyboardInterrupt, KeyboardInterrupt, "never read")
    assert status is TerminalStatus.CANCELLED and _hints(typist) == 1
    assert typist.lines == ["never read"]
    assert SessionStore.terminal_status(path) is TerminalStatus.CANCELLED


def test_not_in_a_row_does_not(repo, tmp_path):
    wiring, typist, _, _ = _session(
        repo, tmp_path, [turn(text="One."), turn(text="Two.")],
        KeyboardInterrupt, "first", KeyboardInterrupt, "second", "/exit")
    assert _hints(typist) == 2 and _sent(wiring) == ["first", "second"]


def test_an_empty_line_between_counts_as_between(repo, tmp_path):
    _, typist, _, _ = _session(repo, tmp_path, [],
                               KeyboardInterrupt, "", KeyboardInterrupt, "/exit")
    assert _hints(typist) == 2 and typist.lines == []


def test_end_of_input_ends_it_at_once(repo, tmp_path):
    _, typist, status, _ = _session(repo, tmp_path, [], EOFError, "never read")
    assert status is TerminalStatus.CANCELLED and _hints(typist) == 0
    assert typist.lines == ["never read"]


def test_one_meant_for_a_task_that_just_ended(repo, tmp_path):
    """The task is stopped by the first (D-062); the second, landing at the
    prompt, used to end the session."""
    asked = []

    def stopped_once():
        # The scripted backend counts only answered requests, so the second
        # request comes back here.
        asked.append(1)
        if len(asked) == 1:
            raise KeyboardInterrupt
        return turn(text="Carrying on.")

    wiring, typist, _, _ = _session(repo, tmp_path, [stopped_once],
                                    "go", KeyboardInterrupt, "carry on", "/exit")
    assert _hints(typist) == 1 and _sent(wiring)[-1].endswith("carry on")
    assert typist.lines == []
