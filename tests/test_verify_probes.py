"""The probe projects under verify/probes/ measure something (D-199).

A probe is a scaffold, a task, and hidden tests Foundry never sees; a change
to the agent is judged by how many of them it passes before and after. That
only means something if the hidden tests fail on the scaffold and can be
passed. This runs them both ways, in subprocesses, so it is the slowest test
of the kit and is kept apart from tests/test_verify_kit.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1] / "verify"


def _script(name, *args):
    return subprocess.run([sys.executable, str(KIT / "probes" / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_the_probes_are_written_whole_and_their_hidden_tests_judge_something(tmp_path):
    """Each project has what the runner reads; and the second set's hidden
    tests fail on what Foundry is given and pass on a reference fix."""
    projects = tmp_path / "projects"

    made = _script("make_projects_b.py", projects)
    assert made.returncode == 0, made.stderr[-2000:]
    names = sorted(p.name for p in projects.iterdir())
    assert len(names) == 11
    for name in names:
        for part in ("repo", "hidden", "task.txt", "allow.txt", "extra.toml"):
            assert (projects / name / part).exists(), f"{name}/{part}"
        assert list((projects / name / "hidden").glob("*.py")), name

    checked = _script("check_b.py", projects)
    assert checked.returncode == 0, checked.stdout[-3000:]
    assert checked.stdout.count("scaffold ->") == 5
    assert "OK: 5 sets of hidden tests" in checked.stdout


def test_hidden_tests_that_judge_nothing_or_cannot_be_passed_are_reported(tmp_path):
    projects = tmp_path / "projects"
    assert _script("make_projects_b.py", projects).returncode == 0
    for name, body in (("i-scope", "assert True"), ("l-repro", "assert False")):
        for hidden in (projects / name / "hidden").glob("*.py"):
            hidden.write_text(f"def test_it():\n    {body}\n", encoding="utf-8")

    checked = _script("check_b.py", projects, "i-scope", "l-repro")

    assert checked.returncode == 1
    assert "FAIL i-scope: the hidden tests pass on the scaffold" in checked.stdout
    assert "FAIL l-repro: the hidden tests fail on the reference fix" in checked.stdout
    assert "OK:" not in checked.stdout
