"""Working, and for how long (D-069): a line that says Foundry is waiting on
the model or running a tool, with the time it has taken, as codex shows
`Working (12s • esc to interrupt)`.

What carries the feature: the line is redrawn in place by a thread, so it must
be down before anything else reaches the screen -- an event, an approval
question, the prompt -- however the turn ends; and it is drawn only on a
terminal, never into a pipe or a log.
"""

from __future__ import annotations

import io
import threading
from types import SimpleNamespace

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.render import Renderer, Working, elapsed
from foundry.cli.repl import RunTask, run_task
from foundry.core.conversation import Usage
from foundry.core.events import (
    ApprovalChoice,
    ApprovalKind,
    ApprovalRequest,
    ErrorEvent,
    MessageDelta,
    Notice,
    Reasoning,
    TerminalStatus,
    Termination,
    TokenCount,
    ToolBegin,
    ToolEnd,
    TurnComplete,
    TurnStarted,
)

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)


def _terminal():
    return Console(file=io.StringIO(), force_terminal=True, width=100, color_system=None)


@pytest.fixture()
def renderer():
    renderer = Renderer(console=_terminal())
    yield renderer
    renderer.idle()


def _threads():
    return {t.name for t in threading.enumerate()}


# --- the line ----------------------------------------------------------------------------


@pytest.mark.parametrize("seconds, said", [
    (0, "0s"), (7.9, "7s"), (59, "59s"), (60, "1m 00s"), (125, "2m 05s"),
    (3599, "59m 59s"), (3600, "1h 00m"), (3725, "1h 02m"),
])
def test_how_long_is_said(seconds, said):
    assert elapsed(seconds) == said


def test_the_line_counts_from_when_it_went_up():
    line = Working("running", 100.0, clock=lambda: 112.7)
    assert line.text() == "running (12s, Ctrl+C to stop)"


def test_it_goes_up_while_the_model_is_asked(renderer):
    renderer.handle(TurnStarted(1))
    assert renderer._live is not None and renderer._live.is_started
    renderer.idle()
    assert "working (0s, Ctrl+C to stop)" in renderer.console.file.getvalue()


def test_and_while_a_tool_runs(renderer):
    renderer.handle(ToolBegin("c1", "run_command", "python -m pytest"))
    assert renderer._live is not None and renderer._live.is_started
    renderer.idle()
    shown = renderer.console.file.getvalue()
    assert "-> python -m pytest" in shown and "running (0s, Ctrl+C to stop)" in shown


@pytest.mark.parametrize("event", [
    MessageDelta(text="Here is"),
    Reasoning("thinking it over"),
    TokenCount(Usage(), Usage()),
    ToolEnd("c1", "run_command", True, "exit code 0"),
    Notice("note"),
    ErrorEvent("boom"),
    TurnComplete(1, "done"),
    Termination(TerminalStatus.COMPLETED, "done"),
])
def test_anything_else_takes_it_down_first(renderer, event):
    renderer.handle(TurnStarted(1))
    live = renderer._live
    renderer.handle(event)
    assert renderer._live is None and not live.is_started


def test_it_is_down_before_an_approval_is_asked(renderer):
    renderer.handle(TurnStarted(1))
    live = renderer._live
    asked = []

    def answer(prompt):
        asked.append(live.is_started or renderer._live is not None)
        return "y"

    renderer.console.input = answer
    choice = renderer.ask_approval(ApprovalRequest(
        request_id="r1", approval_kind=ApprovalKind.COMMAND, tool="run_command",
        display="git push", reason="not allowlisted"))
    assert choice is ApprovalChoice.ONCE and asked == [False]


def test_never_into_a_pipe_or_a_log():
    renderer = Renderer(console=Console(file=io.StringIO(), width=100))
    before = _threads()
    renderer.handle(TurnStarted(1))
    renderer.handle(ToolBegin("c1", "run_command", "pytest"))
    assert renderer._live is None and _threads() == before
    assert "working" not in renderer.console.file.getvalue()
    assert "running (" not in renderer.console.file.getvalue()


def test_one_line_at_a_time(renderer):
    renderer.work("working")
    first = renderer._live
    renderer.work("running")
    assert not first.is_started and renderer._live is not first and renderer._live.is_started


def test_taking_it_down_stops_its_thread(renderer):
    before = _threads()
    renderer.work("working")
    assert len(_threads() - before) == 1
    [thread] = [t for t in threading.enumerate() if t.name not in before]
    renderer.idle()
    renderer.idle()                                 # twice is harmless
    # Told to stop, it ends at its next tick, without drawing again.
    thread.join(timeout=2)
    assert not thread.is_alive()


# --- however the turn ends ---------------------------------------------------------------


def test_a_task_that_raises_leaves_no_line_up():
    renderer = Renderer(console=_terminal())

    def run_turn(text, images, typed=None):
        renderer.work("working")
        raise RuntimeError("the backend fell over")

    wiring = SimpleNamespace(runtime=SimpleNamespace(policy=SimpleNamespace(read_only=""),
                                                     run_turn=run_turn),
                             note="", images=[], renderer=renderer)
    with pytest.raises(RuntimeError):
        run_task(wiring, RunTask("go"))
    assert renderer._live is None


def test_ctrl_c_during_a_command_leaves_no_line_up(repo, tmp_path):
    def stopped():
        raise KeyboardInterrupt

    wiring = _wire(repo, tmp_path / "home", [stopped])
    wiring.renderer.console = _terminal()
    outcome = run_task(wiring, RunTask("go"))
    assert outcome.status is TerminalStatus.CANCELLED
    assert wiring.renderer._live is None
    app._close(wiring, outcome.status, "test")


def test_closing_the_session_takes_it_down(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.renderer.console = _terminal()
    wiring.renderer.work("working")
    app._close(wiring, TerminalStatus.COMPLETED, "test")
    assert wiring.renderer._live is None


def test_a_session_on_a_terminal_shows_it_and_ends_with_it_down(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "run_command", {"command": 'python -c "print(42)"'})),
        turn(text="It printed 42."),
    ])
    wiring.renderer.console = _terminal()
    run_task(wiring, RunTask("print 42"))
    shown = wiring.renderer.console.file.getvalue()
    assert "working (0s, Ctrl+C to stop)" in shown and "running (" in shown
    assert "It printed 42." in shown and wiring.renderer._live is None
    app._close(wiring, TerminalStatus.COMPLETED, "test")
