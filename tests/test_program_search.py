"""Programs Foundry starts are found on PATH, never in the current directory
(D-049).

Windows looks for a program in the current directory before PATH --
CreateProcess and shutil.which both do -- and Foundry is usually started in
the repository it works on. A ``git.exe`` committed at the top of a clone ran
in git's place when a session began, before anything was asked; a
``powershell.exe`` there ran every command.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from foundry.core.config import McpServerConfig
from foundry.core.mcp import start_servers
from foundry.core.tools.command import run_process
from foundry.core.tools.git import run_git
from foundry.core.tools.registry import default_registry
from foundry.core.winapi import IS_WINDOWS, find_program

windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="the current-directory search is Windows'")
# A real program that is not the one asked for: what a repository would plant.
IMPOSTOR = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32" / "whoami.exe"


def _program(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (f"{name}.exe" if IS_WINDOWS else name)
    path.write_bytes(b"MZ" if IS_WINDOWS else b"#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture()
def search_cwd(monkeypatch, tmp_path):
    """The current directory, as Windows treats it without the opt-out this
    test harness happens to set."""
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    here = tmp_path / "clone"
    here.mkdir()
    monkeypatch.chdir(here)
    return here


# --- find_program -----------------------------------------------------------------------


def test_a_program_on_path_is_found_as_an_absolute_path(tmp_path):
    program = _program(tmp_path / "bin", "tool")
    found = find_program("tool", {"PATH": str(tmp_path / "bin")})
    assert found is not None and os.path.isabs(found)
    assert os.path.samefile(found, program)


def test_the_current_directory_is_not_searched(search_cwd, tmp_path):
    _program(search_cwd, "tool")
    (tmp_path / "empty").mkdir()
    assert find_program("tool", {"PATH": str(tmp_path / "empty")}) is None


@windows_only
def test_what_windows_would_have_run(search_cwd, tmp_path):
    # The hazard itself: shutil.which, given PATH, still looks here first.
    _program(search_cwd, "tool")
    (tmp_path / "empty").mkdir()
    assert shutil.which("tool", path=str(tmp_path / "empty")) is not None


@pytest.mark.parametrize("entry", [".", "bin", ""])
def test_a_relative_path_entry_is_the_current_directory_by_another_name(search_cwd, entry):
    _program(search_cwd, "tool")
    _program(search_cwd / "bin", "tool")
    assert find_program("tool", {"PATH": entry}) is None


def test_the_first_absolute_entry_wins_and_quotes_are_allowed(tmp_path):
    first = _program(tmp_path / "with space", "tool")
    _program(tmp_path / "second", "tool")
    path = os.pathsep.join([f'"{tmp_path / "with space"}"', str(tmp_path / "second")])
    assert os.path.samefile(find_program("tool", {"PATH": path}), first)


def test_a_name_with_a_directory_must_be_absolute(search_cwd, tmp_path):
    program = _program(tmp_path / "bin", "tool")
    _program(search_cwd / "bin", "tool")
    assert os.path.samefile(find_program(str(program), {"PATH": ""}), program)
    assert find_program(os.path.join("bin", "tool"), {"PATH": ""}) is None
    assert find_program(os.path.join(".", "tool"), {"PATH": ""}) is None


def test_the_path_variable_is_found_whatever_its_case(tmp_path):
    program = _program(tmp_path / "bin", "tool")
    assert os.path.samefile(find_program("tool", {"Path": str(tmp_path / "bin")}), program)


def test_the_environment_defaults_to_the_processes(monkeypatch, tmp_path):
    program = _program(tmp_path / "bin", "tool")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert os.path.samefile(find_program("tool"), program)


def test_a_missing_program_is_none(tmp_path):
    (tmp_path / "bin").mkdir()
    assert find_program("no-such-tool", {"PATH": str(tmp_path / "bin")}) is None


@windows_only
def test_windows_tries_each_pathext_unless_the_name_has_one(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "tool").write_bytes(b"")              # no extension: not a program
    (bin_ / "tool.cmd").write_bytes(b"@echo off\n")
    env = {"PATH": str(bin_), "PATHEXT": ".EXE;.CMD"}
    assert find_program("tool", env) == str(bin_ / "tool.CMD") or \
        os.path.samefile(find_program("tool", env), bin_ / "tool.cmd")
    assert find_program("tool.cmd", env) == str(bin_ / "tool.cmd")
    assert find_program("tool", {"PATH": str(bin_), "PATHEXT": ".EXE"}) is None


@pytest.mark.skipif(IS_WINDOWS, reason="Windows has no execute bit")
def test_a_file_that_cannot_be_run_is_skipped(tmp_path):
    program = _program(tmp_path / "bin", "tool")
    program.chmod(0o644)
    assert find_program("tool", {"PATH": str(tmp_path / "bin")}) is None


# --- every program Foundry starts ---------------------------------------------------------


@windows_only
def test_git_is_the_one_on_path_not_one_the_repository_planted(search_cwd):
    shutil.copy(IMPOSTOR, search_cwd / "git.exe")
    code, out, _ = run_git(["--version"], cwd=search_cwd)
    assert code == 0 and out.startswith("git version")


@windows_only
def test_commands_run_in_the_real_powershell(search_cwd):
    shutil.copy(IMPOSTOR, search_cwd / "powershell.exe")
    result = run_process("Write-Output from-powershell", cwd=str(search_cwd), timeout_s=60)
    assert result.exit_code == 0 and b"from-powershell" in result.stdout


@windows_only
def test_an_mcp_server_is_not_started_from_the_current_directory(search_cwd, tmp_path):
    shutil.copy(IMPOSTOR, search_cwd / "docs-server.exe")
    found = start_servers({"docs": McpServerConfig(name="docs", command="docs-server")},
                          tmp_path, default_registry())
    try:
        assert found.failures == {"docs": "'docs-server' was not found on PATH"}
    finally:
        found.close()


def test_without_git_on_path_the_error_says_so(monkeypatch, search_cwd, tmp_path):
    from foundry.core.errors import ToolError
    if IS_WINDOWS:
        shutil.copy(IMPOSTOR, search_cwd / "git.exe")      # not a fallback either
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(ToolError, match="git is not on PATH"):
        run_git(["--version"], cwd=search_cwd)


@windows_only
def test_without_powershell_on_path_the_error_says_so(monkeypatch, tmp_path):
    from foundry.core.errors import ToolError
    (tmp_path / "empty").mkdir()
    with pytest.raises(ToolError, match="powershell.exe was not found on PATH"):
        run_process("Write-Output x", cwd=str(tmp_path), timeout_s=10,
                    env={"PATH": str(tmp_path / "empty"), "SYSTEMROOT": os.environ["SYSTEMROOT"]})


def test_the_interpreter_running_these_tests_is_found(tmp_path):
    folder = os.path.dirname(sys.executable)
    name = os.path.basename(sys.executable)
    assert os.path.samefile(find_program(name, {"PATH": folder}), sys.executable)


@windows_only
def test_a_tree_is_ended_by_the_real_taskkill(search_cwd):
    import subprocess

    from foundry.core.winapi import taskkill_tree
    shutil.copy(IMPOSTOR, search_cwd / "taskkill.exe")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        taskkill_tree(child.pid)
        assert child.wait(timeout=20) is not None
    finally:
        child.kill()
        child.wait()


@windows_only
def test_the_doctor_asks_the_real_powershell(search_cwd, tmp_path, monkeypatch, capsys):
    import argparse
    import re

    from foundry.cli import app
    shutil.copy(IMPOSTOR, search_cwd / "powershell.exe")
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path / "home"))
    app.cmd_doctor(argparse.Namespace())
    assert re.search(r"powershell \d", capsys.readouterr().out)


def test_a_folder_with_a_programs_name_is_not_a_program(tmp_path):
    (tmp_path / "bin" / ("tool.exe" if IS_WINDOWS else "tool")).mkdir(parents=True)
    assert find_program("tool", {"PATH": str(tmp_path / "bin")}) is None
