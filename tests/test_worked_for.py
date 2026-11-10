"""``worked for 1m 12s`` as each task ends (D-073), as codex says it.

What carries the feature: the time is the task's -- from its first request
to its end, across every round and tool call in it -- and a task that ends
without saying so (an exception, Ctrl+C at the wrong moment) does not lend
its start to the next one.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from foundry.cli.render import Renderer
from foundry.cli.repl import RunTask, run_task
from foundry.cli.wiring import _close
from foundry.core.events import (
    TerminalStatus,
    Termination,
    ToolBegin,
    ToolEnd,
    TurnComplete,
    TurnStarted,
)

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def clocked():
    clock = _Clock()
    renderer = Renderer(console=Console(file=io.StringIO(), width=100), clock=clock)
    return renderer, clock


def _shown(renderer):
    return renderer.console.file.getvalue()


def test_a_task_s_time_runs_from_its_first_request_to_its_answer(clocked):
    renderer, clock = clocked
    renderer.handle(TurnStarted(1))
    clock.now += 30
    renderer.handle(ToolBegin("c1", "run_command", "pytest"))
    clock.now += 30
    renderer.handle(ToolEnd("c1", "run_command", True, "exit code 0"))
    renderer.handle(TurnStarted(2))                    # a later round: same task
    clock.now += 12.9
    renderer.handle(TurnComplete(2, "done"))
    assert _shown(renderer).count("worked for") == 1
    assert _shown(renderer).rstrip().endswith("worked for 1m 12s")


def test_and_to_its_termination(clocked):
    renderer, clock = clocked
    renderer.handle(TurnStarted(1))
    clock.now += 3725
    renderer.handle(Termination(TerminalStatus.COMPLETED, "done", "All green."))
    lines = _shown(renderer).strip().splitlines()
    assert lines[-2:] == ["All green.", "worked for 1h 02m"]


def test_under_a_second_says_nothing(clocked):
    renderer, clock = clocked
    renderer.handle(TurnStarted(1))
    clock.now += 0.99
    renderer.handle(TurnComplete(1, "hi"))
    assert "worked for" not in _shown(renderer)


def test_each_task_has_its_own_time(clocked):
    renderer, clock = clocked
    renderer.handle(TurnStarted(1))
    clock.now += 5
    renderer.handle(TurnComplete(1, "a"))
    clock.now += 600                                   # the user thinks
    renderer.handle(TurnStarted(1))
    clock.now += 2
    renderer.handle(TurnComplete(1, "b"))
    assert "worked for 5s" in _shown(renderer) and "worked for 2s" in _shown(renderer)
    assert "10m" not in _shown(renderer)


def test_a_task_that_ended_without_saying_so_lends_nothing(clocked):
    renderer, clock = clocked
    renderer.handle(TurnStarted(1))                    # then an exception, say
    renderer.settle()
    clock.now += 900
    renderer.handle(TurnStarted(1))
    clock.now += 4
    renderer.handle(TurnComplete(1, "ok"))
    assert "worked for 4s" in _shown(renderer) and "15m" not in _shown(renderer)


def test_an_end_with_no_start_says_nothing(clocked):
    renderer, clock = clocked
    renderer.handle(Termination(TerminalStatus.CANCELLED, "interrupted by user"))
    renderer.handle(TurnComplete(1, "x"))
    assert "worked for" not in _shown(renderer)


def test_a_session_says_it(repo, tmp_path):
    clock = _Clock()

    def slow():
        clock.now += 42
        return turn(text="All done.")

    wiring = _wire(repo, tmp_path / "home", [slow])
    wiring.renderer.clock = clock
    run_task(wiring, RunTask("go"))
    assert "worked for 42s" in wiring.renderer.console.file.getvalue()
    _close(wiring, TerminalStatus.COMPLETED, "test")
