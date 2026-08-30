from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import pytest

from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.session import ArtifactStore, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.command import RunCommand, run_process
from foundry.core.tools.git import (
    GitBaseline,
    capture_baseline,
    collect_evidence,
    GitDiff,
    GitStatus,
    is_git_repository,
    run_git,
)
from foundry.core.winapi import child_environment, decode_output
from foundry.core.workspace import Workspace


@pytest.fixture()
def ctx(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return ToolContext(
        workspace=Workspace(repo),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        read_tracker=ReadTracker(),
    )


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    run_git(["init"], repo)
    run_git(["config", "user.email", "test@example.com"], repo)
    run_git(["config", "user.name", "Test"], repo)
    (repo / "tracked.py").write_text("A = 1\n", encoding="utf-8")
    run_git(["add", "."], repo)
    run_git(["commit", "-m", "initial"], repo)
    return repo


# --- environment filtering ------------------------------------------------


def test_child_environment_drops_credential_shaped_names():
    env = child_environment({
        "PATH": "C:/bin",
        "OPENAI_API_KEY": "sk-secret",
        "AWS_SECRET_ACCESS_KEY": "x",
        "GITHUB_TOKEN": "y",
        "MY_PASSWORD": "z",
        "PYTHONPATH": "C:/lib",
    })
    assert env["PATH"] == "C:/bin"
    assert env["PYTHONPATH"] == "C:/lib"
    for leaked in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "MY_PASSWORD"):
        assert leaked not in env
    assert env["PYTHONUTF8"] == "1"


# --- output decoding ------------------------------------------------------


def test_decode_prefers_utf8():
    assert decode_output("héllo".encode("utf-8")).text == "héllo"


@pytest.mark.skipif(sys.platform != "win32", reason="oem codec is Windows-only")
def test_decode_falls_back_for_legacy_codepage():
    data = "中文".encode("cp936")
    result = decode_output(data)
    assert "\ufffd" not in result.text or result.encoding != "utf-8"


# --- run_command ----------------------------------------------------------


def test_validation_rejects_unknown_and_bad_arguments():
    tool = RunCommand()
    with pytest.raises(InvalidToolCall):
        tool.validate({"command": "echo hi", "shell": "bash"})
    with pytest.raises(InvalidToolCall):
        tool.validate({"command": ""})
    with pytest.raises(InvalidToolCall):
        tool.validate({"command": "echo hi", "timeout_s": 99999})


def test_display_is_the_exact_command():
    op = RunCommand().validate({"command": "python -c \"print(1)\""})
    assert op.display == "python -c \"print(1)\""
    assert op.target == op.display


def test_run_command_captures_exit_code(ctx):
    tool = RunCommand()
    out = tool.execute(tool.validate({"command": 'python -c "print(7)"'}), ctx)
    assert "7" in out.content
    assert out.metadata["exit_code"] == 0
    assert not out.is_error


@pytest.mark.parametrize("code", [1, 3, 42])
def test_true_exit_code_survives_the_shell(ctx, code):
    """PowerShell 5.1's -Command collapses every failure to 1 on its own; a
    validation claim checked against 1 instead of 3 is evidence of nothing."""
    tool = RunCommand()
    out = tool.execute(
        tool.validate({"command": f'python -c "raise SystemExit({code})"'}), ctx)
    assert out.is_error
    assert out.metadata["exit_code"] == code


def test_failing_cmdlet_reports_nonzero(ctx):
    tool = RunCommand()
    out = tool.execute(tool.validate({"command": "Get-Item C:\\nope_missing_xyz"}), ctx)
    assert out.is_error
    assert out.metadata["exit_code"] != 0


def test_run_command_records_evidence_in_journal(tmp_path, ctx):
    store = SessionStore(tmp_path / "sessions")
    tool = RunCommand(recorder=store)
    out = tool.execute(tool.validate({"command": 'python -c "print(1)"'}), ctx)
    store.close()
    assert out.metadata["event_ordinal"] > 0
    types = [r.type for r in SessionStore.read_records(store.path)]
    assert "command_exec" in types


def test_large_output_spills_to_artifact(ctx):
    ctx.max_output_bytes = 500
    tool = RunCommand()
    script = "for i in range(400): print('line', i)"
    out = tool.execute(tool.validate({"command": f'python -c "{script}"'}), ctx)
    assert out.truncated
    assert out.artifact_id
    assert ctx.artifacts.has(out.artifact_id)


def test_timeout_kills_the_process(ctx):
    tool = RunCommand()
    started = time.monotonic()
    out = tool.execute(
        tool.validate({"command": 'python -c "import time; time.sleep(30)"',
                       "timeout_s": 2}), ctx)
    elapsed = time.monotonic() - started
    assert out.is_error
    assert out.metadata["timed_out"]
    assert elapsed < 15


@pytest.mark.skipif(sys.platform != "win32", reason="process-tree kill is the Windows path")
def test_timeout_kills_descendant_processes(tmp_path):
    """A grandchild must not survive, and must not hold the pipe open.

    The survivor query used to filter on the literal string 'child.py', which
    the querying powershell's own command line contains -- so it always matched
    itself, PowerShell 5.1 prints nothing for a single-object .Count, and the
    isdigit() guard skipped the one assertion this test exists for. On a machine
    where anything else mentioned the file it failed instead. A per-run token,
    handed to the probe through the environment so it never appears in the
    probe's own command line, distinguishes real survivors from the search.
    """
    token = f"foundrykill{uuid.uuid4().hex}"
    child = tmp_path / "child.py"
    child.write_text("import sys, time\ntime.sleep(60)\n", encoding="utf-8")
    spawn = (f"import subprocess,time; subprocess.Popen([r'{sys.executable}', "
             f"r'{child}', '{token}']); time.sleep(60)")
    command = f'python -c "{spawn}"'

    started = time.monotonic()
    result = run_process(command, cwd=str(tmp_path), timeout_s=3)
    elapsed = time.monotonic() - started

    assert result.timed_out
    assert elapsed < 20, "communicate() blocked: a descendant kept the pipe open"

    time.sleep(1)
    probe = (
        "$m = $env:FOUNDRY_KILL_MARKER; "
        "@(Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%$m%'\" | "
        "Where-Object { $_.ProcessId -ne $PID }).Count"
    )
    survivors = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", probe],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "FOUNDRY_KILL_MARKER": token},
    )
    # Unconditional: a probe that cannot be evaluated is a test failure, not a
    # silent pass. Nothing about the guarantee is verified if this is skipped.
    assert survivors.returncode == 0, f"survivor probe failed: {survivors.stderr}"
    count = survivors.stdout.strip()
    assert count.isdigit(), f"survivor probe printed {count!r}, not a count"
    assert int(count) == 0, "orphaned grandchild survived the process-tree kill"


# --- git tools ------------------------------------------------------------


def test_is_git_repository(git_repo, tmp_path):
    assert is_git_repository(git_repo)
    assert not is_git_repository(tmp_path)


def test_baseline_records_dirty_and_untracked(git_repo):
    (git_repo / "tracked.py").write_text("A = 2\n", encoding="utf-8")
    (git_repo / "new.py").write_text("B = 1\n", encoding="utf-8")
    baseline = capture_baseline(git_repo)
    assert baseline.head
    assert "tracked.py" in baseline.dirty_paths
    assert "new.py" in baseline.untracked_paths


def test_evidence_separates_session_changes_from_preexisting(git_repo):
    (git_repo / "tracked.py").write_text("A = 2\n", encoding="utf-8")
    baseline = capture_baseline(git_repo)

    (git_repo / "fresh.py").write_text("C = 1\n", encoding="utf-8")
    evidence = collect_evidence(git_repo, baseline)

    assert "fresh.py" in evidence.session_changed
    assert "tracked.py" in evidence.preexisting_changed
    assert not evidence.head_moved


def test_evidence_detects_moved_head(git_repo):
    baseline = capture_baseline(git_repo)
    (git_repo / "tracked.py").write_text("A = 9\n", encoding="utf-8")
    run_git(["add", "."], git_repo)
    run_git(["commit", "-m", "second"], git_repo)
    evidence = collect_evidence(git_repo, baseline)
    assert evidence.head_moved


def test_git_status_tool(git_repo, tmp_path):
    ctx = ToolContext(workspace=Workspace(git_repo),
                      artifacts=ArtifactStore(tmp_path / "a"), read_tracker=ReadTracker())
    tool = GitStatus()
    out = tool.execute(tool.validate({}), ctx)
    assert "##" in out.content or "clean" in out.content


def test_git_diff_tool(git_repo, tmp_path):
    (git_repo / "tracked.py").write_text("A = 5\n", encoding="utf-8")
    ctx = ToolContext(workspace=Workspace(git_repo),
                      artifacts=ArtifactStore(tmp_path / "a"), read_tracker=ReadTracker())
    tool = GitDiff()
    out = tool.execute(tool.validate({}), ctx)
    assert "A = 5" in out.content


def test_git_tools_reject_unknown_arguments():
    with pytest.raises(InvalidToolCall):
        GitStatus().validate({"all": True})
    with pytest.raises(InvalidToolCall):
        GitDiff().validate({"cached": True})
