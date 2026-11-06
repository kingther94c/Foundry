"""A command the user runs at the prompt with `!` (D-055).

What carries the feature: the command is the user's, so it runs without a
question, but as the model's commands do -- in the workspace, with the minimal
environment and the time limit -- and what it printed reaches the model only
scrubbed, cut to size and with the next message. It may have changed any
file, so nothing run before it can vouch for the workspace afterwards.
"""

from __future__ import annotations

import sys

import pytest

from foundry.cli import app
from foundry.cli import repl as repl_module
from foundry.cli.repl import SHELL_USAGE, run_shell
from foundry.core.errors import ToolError
from foundry.core.events import TerminalStatus
from foundry.core.redaction import PLACEHOLDER, default_redactor
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.command import CommandResult, combined_output

from test_resume import _Live, _wire, call, finish, repo, turn  # noqa: F401 (fixture)
from test_slash_commands import _Recorder, _repl, _sent, _shown

windows = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell syntax")
SECRET = "shell-secret-0123456789abcdef"


def _records(wiring, kind=EventType.USER_COMMAND):
    return [r.payload for r in SessionStore.read_records(wiring.session.path) if r.type == kind]


# --- what it runs and what the model is told ---------------------------------------


def test_the_model_is_told_what_it_printed_with_the_next_message(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="it printed hello")])
    typist = _repl(wiring, "!echo hello", "what did it print?")
    shown = _shown(typist)
    assert "hello" in shown and "exit code 0" in shown
    sent = _sent(wiring)
    assert len(sent) == 1
    note, task = sent[0].rsplit("\n\n", 1)
    assert task == "what did it print?"
    assert note.startswith("The user ran this command in the workspace themselves (exit code 0")
    assert "cannot support a claim in finish" in note
    assert "\n$ echo hello\nhello" in note


def test_a_command_is_not_a_turn_and_is_journaled(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    _repl(wiring, "!echo hello")
    assert wiring.runtime.backend.seen == []
    [record] = _records(wiring)
    assert record["command"] == "echo hello" and record["exit_code"] == 0
    assert record["timed_out"] is False and record["stopped"] is False
    assert isinstance(record["duration_ms"], int)


def test_a_command_that_prints_nothing_says_so(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    _repl(wiring, "!cd .")
    assert wiring.note.endswith("\n$ cd .\n(no output)")


@pytest.mark.parametrize("line", ["!", "  !   "])
def test_a_bare_bang_says_how_to_use_it(repo, tmp_path, line):
    wiring = _wire(repo, tmp_path / "home", [])
    typist = _repl(wiring, line)
    assert SHELL_USAGE in _shown(typist)
    assert _records(wiring) == [] and wiring.note == ""
    assert wiring.runtime.backend.seen == []


def test_only_a_leading_bang_runs_a_command(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    _repl(wiring, "say !echo hi", "   !echo indented", "!   echo spaced  ")
    assert _sent(wiring) == ["say !echo hi"]
    assert [r["command"] for r in _records(wiring)] == ["echo indented", "echo spaced"]


@windows
def test_the_models_commands_still_show_their_errors(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "run_command", {"command": "[Console]::Error.WriteLine('boom')"})),
        turn(text="done")])
    try:
        wiring.runtime.run_turn("run it")
        result = wiring.runtime.context.history[2].blocks[0]
        assert result.call_id == "c1" and "\n\n[stderr]\nboom\n" in result.content
    finally:
        app._close(wiring, TerminalStatus.CANCELLED, "done")


@windows
def test_its_exit_code_and_its_errors_are_told(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    _repl(wiring, "![Console]::Error.WriteLine('boom'); exit 3")
    assert "(exit code 3 in " in wiring.note
    assert wiring.note.endswith("[stderr]\nboom")
    assert _records(wiring)[0]["exit_code"] == 3


def test_what_it_printed_is_scrubbed_everywhere(repo, tmp_path):
    default_redactor().register(SECRET)
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    typist = _repl(wiring, f"!echo {SECRET}", "and?")
    assert SECRET not in _shown(typist).replace(f"echo {SECRET}", "")
    assert SECRET not in "".join(_sent(wiring))
    assert SECRET not in wiring.session.path.read_text(encoding="utf-8")
    assert f"$ echo {PLACEHOLDER}\n{PLACEHOLDER}" in _sent(wiring)[0]


@windows
def test_what_it_printed_is_cut_to_a_tool_results_size(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.tool_ctx.max_output_bytes = 400
    _repl(wiring, '!1..300 | % { "line $_" }')
    output = wiring.note.split("\n", 2)[2]
    assert len(output) <= 400
    assert output.startswith("line 1\n") and output.endswith("line 300")


@windows
def test_it_runs_in_the_workspace_with_the_minimal_environment(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROBE", "visible")
    wiring = _wire(repo, tmp_path / "home", [])
    _repl(wiring, '!"[$env:FOUNDRY_PROBE]"', "!(Get-Location).Path")
    first, second = wiring.note.split("\n\n")
    assert first.endswith("\n[]")
    assert second.rsplit("\n", 1)[1].lower() == str(wiring.workspace.root).lower()


@windows
def test_it_stops_at_the_commands_time_limit(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.tool_ctx.max_command_timeout_s = 1
    typist = _repl(wiring, "!Start-Sleep -Seconds 20")
    assert "timed out after 1s" in _shown(typist)
    assert "(timed out after 1s" in wiring.note
    assert _records(wiring)[0]["timed_out"] is True


def test_ctrl_c_stops_it_and_the_model_is_not_told(repo, tmp_path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(repl_module, "run_process", interrupted)
    wiring = _wire(repo, tmp_path / "home", [])
    typist = _repl(wiring, "!python -m pytest")
    assert "stopped; the model is not told about it" in _shown(typist)
    assert wiring.note == ""
    [record] = _records(wiring)
    assert record["stopped"] is True and record["exit_code"] is None
    assert wiring.runtime._last_mutation > 0


def test_a_command_that_cannot_start_is_said_and_not_journaled(repo, tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise ToolError("powershell.exe was not found on PATH")

    monkeypatch.setattr(repl_module, "run_process", missing)
    wiring = _wire(repo, tmp_path / "home", [])
    typist = _repl(wiring, "!echo hi")
    assert "powershell.exe was not found on PATH" in _shown(typist)
    assert _records(wiring) == [] and wiring.note == ""
    assert wiring.runtime._last_mutation == 0


# --- what it may not weaken ----------------------------------------------------------


def test_no_command_run_before_it_can_vouch_for_the_workspace(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    history = wiring.runtime.registry.tools["run_command"].history

    def cite_latest():
        return finish("f1", claims=[{"claim_text": "python runs",
                                     "command_event_id": history[-1]["event_ordinal"]}])

    wiring.runtime.backend = _Live([turn(call("c1", "run_command", {"command": "python --version"})),
                                    cite_latest, cite_latest])
    try:
        assert wiring.runtime.run_turn("check python").status is TerminalStatus.COMPLETED
        run_shell(wiring, _Recorder([]), "echo changed nothing, perhaps")
        [record] = _records(wiring)
        assert wiring.runtime._last_mutation > history[-1]["event_ordinal"]
        outcome = wiring.runtime.run_turn(app._with_note(wiring, "is it still fine?"))
        assert outcome.status is TerminalStatus.PARTIAL
        assert "cannot vouch for it" in outcome.text
    finally:
        app._close(wiring, TerminalStatus.CANCELLED, "done")


def test_a_session_with_a_command_in_it_resumes(repo, tmp_path):
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(text="seen")])
    _repl(first, "!echo hello", "look")
    second = _wire(repo, home, [], resume="last")
    try:
        texts = [m.text_content for m in second.runtime.context.history]
        assert any("\n$ echo hello\nhello" in t for t in texts)
    finally:
        app._close(second, TerminalStatus.CANCELLED, "done")


def test_help_says_how_to_run_one(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "!COMMAND runs COMMAND in the workspace" in _shown(_repl(wiring, "/help"))


# --- the output, as the model reads it ------------------------------------------------


@pytest.mark.parametrize("stdout, stderr, expected", [
    (b"out\n", b"", "out\n"),
    (b"", b"err\n", "[stderr]\nerr\n"),
    (b"out\n", b"err\n", "out\n\n[stderr]\nerr\n"),
    (b"out\n", b"  \n", "out\n"),
])
def test_stdout_then_stderr(stdout, stderr, expected):
    result = CommandResult(exit_code=0, stdout=stdout, stderr=stderr, duration_ms=1)
    assert combined_output(result) == expected
