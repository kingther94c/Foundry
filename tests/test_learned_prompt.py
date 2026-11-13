"""What the prompt takes from other coding agents (D-192).

Not invented here: each line is one that codex, Cline, OpenHands, aider or
Anthropic's own guidance for Claude gives, most of them several. These tests
pin the wording that carries the instruction, so a later edit that drops one
is a decision, not an accident.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli.unattended import FINISH_REMINDER, _open_steps
from foundry.core.events import PlanStep, StepStatus
from foundry.core.policy.engine import Mode, PolicyEngine
from foundry.core.prompts import UNATTENDED_WORK, base_system_prompt, permissions_paragraph
from foundry.core.tools.finish import Finish

from test_headless import repo  # noqa: F401 (fixture)


def _said(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("line", [
    # Scope: Anthropic's "avoid over-engineering", codex, aider's overeager prompt.
    "Do what was asked, all of it, and then stop",
    "unrelated bugs or failing tests, go in your summary, not into the code",
    # The project's own checks: codex, Claude Code.
    "Find out how the project checks itself",
    # What counts as a check: Anthropic's verification guidance, codex, OpenHands.
    "A check is a command that exercises the change",
    "Start with the narrowest and widen",
    "That a file parses, or a command that failed to start, is not a check",
    "For a bug, reproduce it first and run the reproduction again after the fix",
    # Tests are not the spec: Anthropic.
    "Tests check the solution; they do not define it",
    # Repeated failure: Cline, OpenHands, codex.
    "If two fixes for the same failure have not worked, stop making similar edits",
    # The worktree: codex, Anthropic.
    "The worktree may hold changes you did not make: leave them alone",
    # Scratch files: Anthropic, OpenHands.
    "Delete scratch scripts you made before you finish",
    # Compaction: Anthropic's context awareness.
    "do not cut work short because the conversation is long",
    # Batching: Anthropic, Cline, opencode, OpenHands.
    "Put independent reads and searches in one reply",
    # PowerShell quoting: Cline.
    "Put literal strings and paths in single quotes",
])
def test_the_system_prompt_says_it(line):
    assert line in _said(base_system_prompt())


def _unattended(mode=Mode.ACCEPT_EDITS) -> PolicyEngine:
    policy = PolicyEngine(mode=mode)
    policy.interactive = False
    return policy


@pytest.mark.parametrize("policy", [_unattended(), _unattended(Mode.DEFAULT),
                                    PolicyEngine(mode=Mode.DONT_ASK)])
def test_a_run_nobody_watches_is_told_what_blocked_means(policy):
    told = _said(permissions_paragraph(policy))
    assert _said(UNATTENDED_WORK) in told
    assert "take the most reasonable reading and state it in `finish`'s summary" in told
    assert "Errors, timeouts, failing commands and empty results are ordinary obstacles" in told
    assert told.count("Nobody answers questions") == 1


def test_a_watched_session_is_not():
    assert "Nobody answers questions" not in permissions_paragraph(PolicyEngine(mode=Mode.DEFAULT))


def test_the_reminder_does_not_push_an_early_finish():
    # "Call finish now" turned a stop part-way through into a finish.
    assert "Call finish now" not in FINISH_REMINDER
    assert "If work you were asked for remains, carry on with it now" in FINISH_REMINDER


class _Runtime:
    def __init__(self, plan):
        self.plan = plan


def test_the_reminder_names_the_plan_s_open_steps():
    plan = [PlanStep("read the spec", StepStatus.COMPLETED),
            PlanStep("add --json", StepStatus.IN_PROGRESS),
            PlanStep("write tests", StepStatus.PENDING)]
    assert _open_steps(_Runtime(plan)) == (
        " Your plan has steps not marked completed: add --json; write tests.")
    assert _open_steps(_Runtime(plan[:1])) == ""
    assert _open_steps(_Runtime([])) == ""


def test_finish_asks_for_a_summary_a_stranger_can_follow():
    description = _said(Finish().schema().description)
    assert ("Write it for someone who saw none of the work, the main point first: what changed "
            "and where, what you checked, what you could not do, and what you noticed but left "
            "alone.") in description
    json.dumps(Finish().schema().parameters)  # still a plain schema


def test_a_prose_reply_is_reminded_of_the_plan_s_open_steps(repo, tmp_path):  # noqa: F811
    from test_headless import _finished, _unattended
    from test_headless import _said as _prose
    from test_plan import THREE
    from test_resume import call, turn

    from foundry.cli.unattended import _run_unattended

    wiring = _unattended(repo, tmp_path, [turn(call("u1", "update_plan", THREE)),
                                          _prose("Fixed add(); tests next."), _finished()])
    _run_unattended(wiring.runtime, "fix add")
    wiring.session.close()
    reminder = wiring.runtime.backend.seen[2].messages[-1].blocks[0].text
    assert reminder.startswith(FINISH_REMINDER)
    assert "Your plan has steps not marked completed: Fix add(); Run the tests." in reminder
