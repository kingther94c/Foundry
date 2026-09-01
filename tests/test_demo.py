"""The demo is a teaching artifact, so it has to keep working.

Docs rot silently; a demo that no longer runs teaches the wrong thing twice --
once about the code, once about whether anything here is checked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).parent.parent / "demo" / "mini_foundry.py"


def _run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(DEMO), "--workdir", str(tmp_path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


def test_the_happy_path_completes_with_verified_evidence(tmp_path):
    done = _run(tmp_path, "--yes")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "completed" in done.stdout
    assert "证据核对通过" in done.stdout
    # It really did fix the bug, not merely claim to.
    assert "return a + b" in (tmp_path / "sample_repo" / "calc.py").read_text(encoding="utf-8")


def test_the_breaker_denies_at_step_zero_even_with_yes(tmp_path):
    done = _run(tmp_path, "--script", "destructive", "--yes")
    assert "第 0 步" in done.stdout
    assert "git reset --hard" in done.stdout
    # --yes approves everything approvable; the breaker is not approvable.
    assert done.stdout.count("拒绝（第 0 步）") == 2


def test_a_false_claim_is_caught_and_downgraded(tmp_path):
    done = _run(tmp_path, "--script", "liar", "--yes")
    assert done.returncode == 10, done.stdout
    assert "partial" in done.stdout
    assert "exit code 是 1" in done.stdout


def test_plan_mode_refuses_every_mutation(tmp_path):
    done = _run(tmp_path, "--mode", "plan", "--no")
    assert "plan 模式下不做任何改动" in done.stdout
    assert (tmp_path / "sample_repo" / "calc.py").read_text(encoding="utf-8").endswith(
        "return a - b\n"), "plan mode let a write through"


def test_the_journal_records_every_decision(tmp_path):
    import json

    _run(tmp_path, "--yes")
    entries = [json.loads(line) for line
               in (tmp_path / "session.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = [e["type"] for e in entries]

    assert kinds[0] == "task"
    assert "policy_decision" in kinds
    assert "command_exec" in kinds
    assert kinds[-1] == "termination"
    assert entries[-1]["payload"]["status"] == "completed"


@pytest.mark.parametrize("script", ["fix", "destructive", "liar"])
def test_no_script_crashes(tmp_path, script):
    done = _run(tmp_path, "--script", script, "--yes")
    assert "Traceback" not in done.stderr, done.stderr
