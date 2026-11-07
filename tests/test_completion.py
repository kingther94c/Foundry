"""Tab completion for the shell (D-093), as codex's ``completion`` command
prints one: ``foundry completion powershell`` (the default) and ``bash``.

What it must get right: it is built from the parser, so every subcommand and
flag is offered and none that is not; a flag's value is not taken for a
subcommand; a value from a fixed list is offered after its flag; a path, a
name, and anything after ``--`` are left to the shell. The bash script must
reach Git Bash without carriage returns, which bash would read as part of
each line.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from foundry.cli import app
from foundry.cli.completion import SHELLS, command_tree, script

TREE = command_tree(app.build_parser())
SRC = str(Path(__file__).resolve().parents[1] / "src")


# --- the tree ------------------------------------------------------------------------------


def test_every_subcommand_is_offered_and_nested_ones_under_theirs():
    parser = app.build_parser()
    [sub] = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert TREE["foundry"]["subcommands"] == sorted(sub.choices)
    assert {"completion", "exec", "resume", "mcp"} <= set(TREE["foundry"]["subcommands"])
    assert TREE["foundry mcp"]["subcommands"] == ["add", "list", "remove"]
    assert "foundry mcp add" in TREE


def test_flags_and_their_values():
    exec_ = TREE["foundry exec"]
    assert {"--json", "--mode", "-m", "--model", "--resume", "-h"} <= set(exec_["flags"])
    assert exec_["choices"]["--mode"] == ["dont_ask", "plan", "accept_edits"]
    assert {"-m", "--model", "--resume", "-c"} <= set(exec_["takes_value"])
    assert not {"--json", "-h", "--help"} & set(exec_["takes_value"])


def test_before_a_subcommand_what_run_takes_is_offered():
    root, run = TREE["foundry"], TREE["foundry run"]
    assert set(run["flags"]) <= set(root["flags"]) and "--version" in root["flags"]
    assert root["choices"]["--mode"] == run["choices"]["--mode"]
    assert "--profile" in root["takes_value"]


def test_an_arguments_fixed_values_are_offered():
    assert TREE["foundry login"]["values"] == ["status"]
    assert TREE["foundry completion"]["values"] == sorted(SHELLS)


def test_a_hidden_flag_is_not_offered():
    parser = argparse.ArgumentParser(prog="foundry")
    parser.add_argument("--secret-switch", help=argparse.SUPPRESS)
    parser.add_argument("--shown")
    assert "--secret-switch" not in command_tree(parser)["foundry"]["flags"]
    assert "--shown" in command_tree(parser)["foundry"]["flags"]


def test_an_unknown_shell_is_refused():
    with pytest.raises(ValueError, match="powershell, bash"):
        script("fish", app.build_parser())


@pytest.mark.parametrize("shell", SHELLS)
def test_the_script_is_ascii_and_runs_nothing(shell):
    text = script(shell, app.build_parser())
    assert all(ord(c) < 128 for c in text)
    for word in ("Invoke-WebRequest", "Start-Process", "curl ", "rm "):
        assert word not in text


# --- the command ---------------------------------------------------------------------------


def _foundry(*argv, cwd=None) -> bytes:
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.run([sys.executable, "-m", "foundry", *argv], capture_output=True,
                          env=env, cwd=cwd, timeout=60, check=True).stdout


def test_powershell_is_the_default():
    assert _foundry("completion") == _foundry("completion", "powershell")
    assert b"Register-ArgumentCompleter" in _foundry("completion")


def test_the_bash_script_has_no_carriage_return():
    out = _foundry("completion", "bash")
    assert b"complete -o default -F _foundry foundry" in out and b"\r" not in out


def test_another_shell_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as caught:
        app.main(["completion", "fish"])
    assert caught.value.code == 2


# --- PowerShell ----------------------------------------------------------------------------


POWERSHELL = shutil.which("powershell")


def _powershell_complete(line: str, cwd: Path) -> list[str]:
    """What Tab offers after ``line`` once the script is loaded as the
    profile line says to load it."""
    command = (
        f"& '{sys.executable}' -m foundry completion powershell | Out-String | Invoke-Expression; "
        f"(TabExpansion2 -inputScript '{line}' -cursorColumn {len(line)}).CompletionMatches"
        " | ForEach-Object { $_.CompletionText }")
    out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env={**os.environ, "PYTHONPATH": SRC}, cwd=cwd, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


CASES = [
    ("foundry ex", ["exec"]),
    ("foundry exec --mo", ["--mode", "--model"]),
    ("foundry exec --mode ", ["accept_edits", "dont_ask", "plan"]),
    ("foundry --mode a", ["accept_edits"]),
    ("foundry mcp ", ["add", "list", "remove"]),
    ("foundry mcp add --re", ["--read-only"]),
    ("foundry login ", ["status"]),
    ("foundry completion ", ["bash", "powershell"]),
    # The value of --profile is not the exec subcommand: still at the top.
    ("foundry --profile exec re", ["record", "report", "resume"]),
]
LEFT_TO_THE_SHELL = ["foundry exec -m ", "foundry mcp add docs -- ", "foundry exec --workspace ",
                     "foundry --profile ", "foundry mcp add docs -- python --e"]
# Every word the script could offer: what the shell offers instead is none.
OURS = {word for node in TREE.values()
        for word in (*node["subcommands"], *node["flags"], *node["values"],
                     *(value for values in node["choices"].values() for value in values))}


@pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell")
def test_powershell_completes(tmp_path):
    for line, offered in CASES:
        assert sorted(_powershell_complete(line, tmp_path)) == offered, line


@pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell")
def test_powershell_leaves_paths_and_names_to_the_shell(tmp_path):
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    for line in LEFT_TO_THE_SHELL:
        offered = _powershell_complete(line, tmp_path)
        assert not set(offered) & OURS, (line, offered)


@pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell")
def test_powershell_offers_every_subcommand_on_an_empty_word(tmp_path):
    assert sorted(_powershell_complete("foundry ", tmp_path)) == TREE["foundry"]["subcommands"]


# --- bash ----------------------------------------------------------------------------------


def _git_bash() -> str | None:
    found = shutil.which("bash")
    # System32's bash.exe starts WSL, which may not be installed.
    if found is None or "system32" in found.lower():
        return None
    return found


BASH = _git_bash()


def _bash_complete(script_path: Path, line: str) -> list[str]:
    words = line.split(" ")
    driver = ('source "$1"; shift; COMP_WORDS=("$@"); COMP_CWORD=$((${#COMP_WORDS[@]} - 1)); '
              '_foundry; printf "%s\\n" "${COMPREPLY[@]}"')
    out = subprocess.run([BASH, "-c", driver, "_", str(script_path).replace("\\", "/"), *words],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stderr == ""
    return out.stdout.split()


@pytest.fixture(scope="module")
def bash_script(tmp_path_factory):
    path = tmp_path_factory.mktemp("completion") / "foundry.bash"
    path.write_bytes(_foundry("completion", "bash"))
    return path


@pytest.mark.skipif(BASH is None, reason="no Git Bash")
@pytest.mark.parametrize("line, offered", CASES)
def test_bash_completes(bash_script, line, offered):
    assert sorted(_bash_complete(bash_script, line)) == offered


@pytest.mark.skipif(BASH is None, reason="no Git Bash")
@pytest.mark.parametrize("line", LEFT_TO_THE_SHELL)
def test_bash_leaves_paths_and_names_to_the_shell(bash_script, line):
    assert _bash_complete(bash_script, line) == []


@pytest.mark.skipif(BASH is None, reason="no Git Bash")
def test_bash_offers_every_subcommand_on_an_empty_word(bash_script):
    assert sorted(_bash_complete(bash_script, "foundry ")) == TREE["foundry"]["subcommands"]
