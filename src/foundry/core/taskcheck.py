"""The task level of ``foundry checkup`` (D-206).

The levels before it ask whether each part works; this one asks whether they
work together. A session is given a small task with nobody at the terminal,
as ``foundry exec`` runs one, in a repository the checkup makes for it and
removes after: a module with an off-by-one and the unittest that finds it.
The session may edit files and run ``python -m unittest`` and nothing else
that would ask, in at most eight rounds, with a Foundry folder of its own,
so nothing it records -- a session, a trusted folder -- stays behind. Then
the checkup runs the tests itself: the session's word is not the verdict.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from foundry.core.checkup import FAIL, OK, WARN, Case
from foundry.core.tools.git import run_git

SAMPLE = {
    "README.md": "# interval\n\nClosed intervals of whole numbers: `overlap(a, b)`.\n",
    "interval.py": (
        '"""Closed intervals of whole numbers, as (start, end) with both ends in."""\n'
        "\n"
        "\n"
        "def overlap(a, b):\n"
        '    """How many whole numbers two intervals share; 0 when none."""\n'
        "    start = max(a[0], b[0])\n"
        "    end = min(a[1], b[1])\n"
        "    return max(0, end - start)\n"
    ),
    "test_interval.py": (
        "import unittest\n"
        "\n"
        "from interval import overlap\n"
        "\n"
        "\n"
        "class OverlapTest(unittest.TestCase):\n"
        "    def test_apart(self):\n"
        "        self.assertEqual(overlap((1, 3), (5, 8)), 0)\n"
        "\n"
        "    def test_inside(self):\n"
        "        self.assertEqual(overlap((1, 10), (3, 4)), 2)\n"
        "\n"
        "    def test_touching(self):\n"
        "        self.assertEqual(overlap((1, 3), (3, 6)), 1)\n"
    ),
}
TESTS = "test_interval.py"
TASK = ("The tests in test_interval.py fail. Find out why and fix interval.py so that "
        "`python -m unittest` passes. Do not change the tests.")
ROUNDS = 8
# The one command the session may run unasked; every other rule is the
# user's own, the machine's first.
ALLOW = ('\n\n# Added by foundry checkup for its task (D-206).\n[[permissions]]\n'
         'tool = "run_command"\npattern = "python -m unittest*"\ndecision = "allow"\n')
TIMEOUT_S = 600


@dataclass(slots=True)
class Ran:
    """What the session's run said of itself, read from its --json events."""

    exit_code: int | None = None
    rounds: int = 0
    tools: int = 0
    refused: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = ""
    reason: str = ""
    seconds: float = 0.0
    timed_out: bool = False
    stderr: str = ""


def make_sample(folder: Path) -> Path:
    """The repository, committed, so the session starts from a clean tree."""
    repo = folder / "interval"
    repo.mkdir(parents=True)
    for name, text in SAMPLE.items():
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    who = ["-c", "user.name=foundry checkup", "-c", "user.email=checkup@localhost",
           "-c", "commit.gpgsign=false"]
    for argv in (["init", "-q"], ["add", "-A"], [*who, "commit", "-q", "-m", "a module"]):
        code, _, err = run_git(argv, repo)
        if code != 0:
            raise OSError(f"git {' '.join(argv[-4:])}: {err.strip()}")
    return repo


def child_config(user_config: str) -> str:
    """The session's configuration: the user's, and the rule the task needs."""
    return user_config + ALLOW


def exec_argv(repo: Path) -> list[str]:
    """The session, as a script runs one."""
    return [sys.executable, "-m", "foundry", "exec", "--json", "--ephemeral", "-C", str(repo),
            "-c", f"runtime.max_tool_rounds={ROUNDS}", TASK]


def read_events(stdout: str, ran: Ran) -> None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if kind == "turn_started":
            ran.rounds += 1
        elif kind == "tool_end":
            ran.tools += 1
        elif kind == "tool_rejected":
            ran.refused.append(f"{event.get('tool')} {str(event.get('display', ''))[:60]}")
        elif kind == "token_count":
            total = event.get("session_total") or {}
            ran.input_tokens = int(total.get("input_tokens") or 0)
            ran.output_tokens = int(total.get("output_tokens") or 0)
        elif kind == "termination":
            ran.status = str(event.get("status") or "")
            ran.reason = str(event.get("reason") or "")


def tests_pass(repo: Path) -> bool:
    """The sample's tests, run by the checkup itself."""
    try:
        done = subprocess.run([sys.executable, "-m", "unittest"], cwd=repo,
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def tests_kept(repo: Path) -> bool:
    try:
        return (repo / TESTS).read_text(encoding="utf-8") == SAMPLE[TESTS]
    except (OSError, UnicodeDecodeError):
        return False


def task_case(ran: Ran, passed: bool, kept: bool) -> Case:
    took = (f"{ran.rounds} round(s), {ran.tools} tool call(s), {ran.input_tokens} token(s) "
            f"in and {ran.output_tokens} out, {ran.seconds:.0f} s")
    refused = f"; refused: {', '.join(ran.refused[:3])}" if ran.refused else ""
    if ran.timed_out:
        return Case("T", "task", FAIL, f"no end after {TIMEOUT_S} s: {took}", "TSKT",
                    "the session's events, kept beside the report, show where it waited")
    if not ran.status:
        last = (ran.stderr.strip().splitlines() or ["(nothing on stderr)"])[-1]
        return Case("T", "task", FAIL, f"the session ended without an outcome, exit "
                    f"{ran.exit_code}: {last[:160]}", "TSKX",
                    "what it printed is kept beside the report; say this line")
    if not kept:
        return Case("T", "task", FAIL, f"the session changed the tests; the run ended "
                    f"{ran.status}: {took}", "TSKE",
                    "it was told not to: a model that does this cannot be left alone")
    if not passed:
        return Case("T", "task", FAIL, f"the tests still fail; the run ended {ran.status} "
                    f"({ran.reason[:100]}): {took}{refused}", "TSK0",
                    f"the model did not fix it in {ROUNDS} rounds: its events, kept beside "
                    "the report, show what it tried")
    if ran.status != "completed":
        return Case("T", "task", WARN, f"the tests pass, but the run ended {ran.status} "
                    f"({ran.reason[:100]}): {took}", "TSKP",
                    "the fix works and the session did not say so as finish asks")
    return Case("T", "task", OK, f"fixed, and said so: {took}{refused}")
