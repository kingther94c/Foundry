"""A command that keeps failing the same way is said to (D-195).

A failing command is a result, not a tool error, so the repeat counter never
saw one run again and again. OpenHands' stuck detector stops on the same
action and error three times, opencode asks after three identical calls;
here the third time is told that running it again will not change it.
"""

from __future__ import annotations

from foundry.core.runtime import SAME_FAILURE_AFTER, FailureTracker

from test_resume import _core, call, finish, turn


def test_the_third_same_failure_in_a_row_is_counted_as_such():
    tracker = FailureTracker()
    counts = [tracker.failed_again("pytest", 1, "1 failed, 2 passed in 0.23s") for _ in range(3)]
    assert counts == [1, 2, 3] and SAME_FAILURE_AFTER == 3


def test_timings_do_not_make_a_failure_different():
    tracker = FailureTracker()
    tracker.failed_again("pytest", 1, "FAILED t.py::a - x\n1 failed in 0.23s")
    assert tracker.failed_again("pytest", 1, "FAILED t.py::a - x\n1 failed in 1.70s") == 2
    assert tracker.failed_again("pytest", 1, "FAILED t.py::a - x\n1 failed in 12ms") == 3


def test_a_different_outcome_starts_again():
    tracker = FailureTracker()
    tracker.failed_again("pytest", 1, "2 failed")
    tracker.failed_again("pytest", 1, "2 failed")
    assert tracker.failed_again("pytest", 1, "1 failed") == 1  # progress
    assert tracker.failed_again("pytest", 2, "1 failed") == 1  # another exit code
    assert tracker.failed_again("other", 2, "1 failed") == 1  # another command


def test_a_pass_starts_again():
    tracker = FailureTracker()
    tracker.failed_again("pytest", 1, "x")
    tracker.failed_again("pytest", 1, "x")
    tracker.passed("pytest")
    assert tracker.failed_again("pytest", 1, "x") == 1


FAIL = "python -c \"import sys; print('boom'); sys.exit(3)\""


def _results(runtime):
    return [b.content for m in runtime.context.history for b in m.blocks
            if getattr(b, "kind", "") == "tool_result"]


def test_a_session_tells_the_third_run_and_not_before(tmp_path):
    turns = [turn(call(f"c{i}", "run_command", {"command": FAIL})) for i in range(4)]
    runtime, session, repo = _core(tmp_path, [*turns, finish()])
    runtime.run_turn("make it pass")
    session.close()
    said = [("failed the same way" in r) for r in _results(runtime)[:4]]
    assert said == [False, False, True, True]
    assert "failed the same way 4 times" in _results(runtime)[3]
