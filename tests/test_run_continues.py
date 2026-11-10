"""``foundry run TASK`` at a terminal goes on after the task (D-078), as
``codex "task"`` does: the task is the first message, and the prompt comes
back for the next.

What carries the feature: the task is taken exactly as if it had been typed
at the prompt (a Ctrl+C stops it, not the session), it is sent once -- not
again after /new -- and where nobody is at a terminal to go on, the run ends
with the task and its exit code is the task's, as before.
"""

from __future__ import annotations

import builtins

import pytest

from foundry.cli import app
from foundry.core.events import EXIT_CODES, TerminalStatus

from test_resume import _wire, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _sent


def _typed(monkeypatch, *lines):
    queue = list(lines)

    def typed(prompt=""):
        line = queue.pop(0)
        if isinstance(line, type) and issubclass(line, BaseException):
            raise line
        return line

    monkeypatch.setattr(builtins, "input", typed)
    return queue


@pytest.fixture()
def at_a_terminal(monkeypatch):
    monkeypatch.setattr("foundry.cli.session_commands._interactive", lambda: True)


def test_the_session_goes_on_after_the_task(repo, tmp_path, monkeypatch, at_a_terminal):
    wiring = _wire(repo, tmp_path / "home", [turn(text="It is a parser."), turn(text="Yes.")])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    left = _typed(monkeypatch, "and is it fast?", "/exit")
    code = app.main(["run", "what is this repo?", "--workspace", str(repo)])
    assert _sent(wiring) == ["what is this repo?", "and is it fast?"]
    assert left == [] and code == EXIT_CODES[TerminalStatus.CANCELLED]


def test_as_if_typed_ctrl_c_stops_the_task_not_the_session(repo, tmp_path, monkeypatch,
                                                            at_a_terminal):
    asked = []

    def stopped_once():
        asked.append(1)
        if len(asked) == 1:
            raise KeyboardInterrupt
        return turn(text="Carrying on.")

    wiring = _wire(repo, tmp_path / "home", [stopped_once])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    left = _typed(monkeypatch, "carry on", "/exit")
    app.main(["run", "a long task", "--workspace", str(repo)])
    assert _sent(wiring)[-1].endswith("carry on") and left == []


def test_as_if_typed_a_command_is_a_command(repo, tmp_path, monkeypatch, at_a_terminal,
                                            capsys):
    wiring = _wire(repo, tmp_path / "home", [])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    _typed(monkeypatch, "/exit")
    app.main(["run", "/status", "--workspace", str(repo)])
    assert _sent(wiring) == [] and "model" in capsys.readouterr().out


def test_sent_once_not_again_after_new(repo, tmp_path, monkeypatch, at_a_terminal):
    first = _wire(repo, tmp_path / "home", [turn(text="done")])
    second = _wire(repo, tmp_path / "home", [])
    built = iter([first, second])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: next(built))
    left = _typed(monkeypatch, "/new", "/exit")
    app.main(["run", "do it", "--workspace", str(repo)])
    assert _sent(first) == ["do it"] and _sent(second) == [] and left == []


def test_resume_with_a_task_goes_on_too(repo, tmp_path, monkeypatch, at_a_terminal):
    wiring = _wire(repo, tmp_path / "home", [turn(text="One."), turn(text="Two.")])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    left = _typed(monkeypatch, "and then?", "/exit")
    app.main(["resume", "last", "go on", "--workspace", str(repo)])
    assert _sent(wiring)[-1] == "and then?" and left == []


def test_with_nobody_at_a_terminal_it_ends_with_the_task(repo, tmp_path, monkeypatch):
    monkeypatch.setattr("foundry.cli.session_commands._interactive", lambda: False)
    wiring = _wire(repo, tmp_path / "home", [turn(text="done")])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    left = _typed(monkeypatch, "never read")
    code = app.main(["run", "do it", "--workspace", str(repo)])
    assert _sent(wiring) == ["do it"] and left == ["never read"]
    assert code == EXIT_CODES[TerminalStatus.PARTIAL]       # a reply, not a finish


def test_no_task_is_the_prompt_as_before(repo, tmp_path, monkeypatch, at_a_terminal):
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi")])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    left = _typed(monkeypatch, "hello", "/exit")
    app.main(["run", "--workspace", str(repo)])
    assert _sent(wiring) == ["hello"] and left == []
