"""What a command printed, in view (D-068): its last lines under the command,
as codex shows them, and its exit code.

What carries the feature: a command prints anything -- control sequences, a
credential, brackets rich would read as markup -- and what reaches the screen
or `exec --json` is stripped, scrubbed and never interpreted. The model still
reads all of the output, as before.
"""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from foundry.cli.app import _event_payload
from foundry.cli.render import COMMAND_OUTPUT_LINE_CHARS, COMMAND_OUTPUT_LINES, Renderer
from foundry.core.events import EventSink, ToolEnd
from foundry.core.redaction import Redactor
from foundry.core.session import SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.command import (
    OUTPUT_TAIL_CHARS,
    OUTPUT_TAIL_LINES,
    RunCommand,
    output_tail,
)
from foundry.core.workspace import Workspace

from test_cli_e2e import _ModelHandler, model_server, repo, tool_turn, wire  # noqa: F401

CANARY = "sk-canaryLEAK0123456789abcdefXYZ"


# --- what is kept -------------------------------------------------------------------------


def test_the_last_lines_are_kept():
    text = "\n".join(str(i) for i in range(50))
    assert output_tail(text) == "\n".join(str(i) for i in range(50 - OUTPUT_TAIL_LINES, 50))


def test_the_blank_lines_it_ends_with_are_not():
    assert output_tail("one\ntwo\r\n\r\n  \n\n") == "one\ntwo"
    assert output_tail("") == "" and output_tail("\n\n") == ""


def test_a_long_tail_keeps_its_end():
    text = "x" * OUTPUT_TAIL_CHARS + "THE END"
    tail = output_tail(text)
    assert len(tail) == OUTPUT_TAIL_CHARS and tail.endswith("THE END")


def test_a_real_command_s_tail(tmp_path):
    tool = RunCommand()
    ctx = ToolContext(workspace=Workspace(tmp_path), artifacts=None, read_tracker=ReadTracker())
    out = tool.execute(tool.validate(
        {"command": 'python -c "for i in range(30): print(i)"'}), ctx)
    assert out.metadata["output_tail"] == "\n".join(str(i) for i in range(10, 30))
    assert "\n0\n1\n2\n" in out.content.replace("\r\n", "\n")   # the model reads all of it


# --- what is shown ------------------------------------------------------------------------


def _render(*events, verbose=False):
    console = Console(file=io.StringIO(), width=300, color_system=None)
    renderer = Renderer(console=console, verbose=verbose)
    for event in events:
        renderer.handle(event)
    return console.file.getvalue()


def _end(output, ok=True, summary="exit code 0", tool="run_command"):
    return ToolEnd("c1", tool, ok, summary, duration_ms=5, output=output)


def test_the_last_few_lines_are_shown_under_the_exit_code():
    shown = _render(_end("\n".join(f"line {i}" for i in range(12))))
    lines = shown.splitlines()
    assert lines[0].strip() == "ok exit code 0"
    assert lines[1].strip() == "..."
    assert [line.strip() for line in lines[2:]] == [
        f"line {i}" for i in range(12 - COMMAND_OUTPUT_LINES, 12)]


def test_verbose_shows_all_that_was_kept():
    shown = _render(_end("\n".join(f"line {i}" for i in range(12))), verbose=True)
    assert "line 0" in shown and "..." not in shown


def test_a_short_output_is_shown_whole():
    shown = _render(_end("3 passed"))
    assert shown.splitlines()[1].strip() == "3 passed" and "..." not in shown


def test_a_failure_says_its_exit_code():
    shown = _render(_end("AssertionError: 1 != 2", ok=False, summary="exit code 1"))
    assert "failed exit code 1" in shown and "AssertionError: 1 != 2" in shown


def test_nothing_printed_adds_nothing():
    assert _render(_end("")).strip() == "ok exit code 0"


def test_other_tools_are_shown_as_before():
    # (A read that worked says nothing at all since D-076: test_quiet_reads.py.)
    assert _render(_end("", tool="docs.lookup", summary="1: import os")).strip() == "ok"
    assert "1: import os" in _render(_end("", tool="docs.lookup", summary="1: import os"),
                                     verbose=True)


def test_what_a_command_prints_is_never_read_as_markup_or_control():
    shown = _render(_end("[red]fake[/red] [link=http://x]y[/link]\n"
                         "\x1b]52;c;aGVsbG8=\x07copied\x1b[2Jcleared\x1b[31mred"))
    assert "[red]fake[/red] [link=http://x]y[/link]" in shown
    assert "\x1b" not in shown and "\x07" not in shown
    assert "copiedclearedred" in shown


def test_a_trailing_backslash_does_not_eat_the_style():
    assert "C:\\path\\" in _render(_end("C:\\path\\"))


def test_a_long_line_is_cut():
    shown = _render(_end("y" * 1000))
    line = shown.splitlines()[1].strip()
    assert len(line) == COMMAND_OUTPUT_LINE_CHARS and line.endswith("...")


def test_a_credential_in_the_output_never_reaches_the_screen():
    redactor = Redactor()
    redactor.register(CANARY)
    sink = EventSink(redactor=redactor)
    console = Console(file=io.StringIO(), width=300, color_system=None)
    sink.subscribe(Renderer(console=console).handle)
    seen = []
    sink.subscribe(seen.append)
    sink.emit(_end(f"export TOKEN={CANARY}"))
    assert CANARY not in console.file.getvalue() and "[redacted]" in console.file.getvalue()
    assert CANARY not in seen[0].output


def test_exec_json_carries_it():
    payload = _event_payload(_end("3 passed"))
    assert payload["output"] == "3 passed" and payload["kind"] == "tool_end"
    json.dumps(payload)


# --- end to end ---------------------------------------------------------------------------


def test_a_session_shows_what_its_command_printed(repo, tmp_path, model_server):
    _ModelHandler.turns = [
        tool_turn("run_command", {"command": 'python -c "for i in range(9): print(\'row\', i)"'}),
        tool_turn("finish", {"status": "completed", "summary": "ran it", "claims": []}, "c2"),
    ]
    wiring = wire(repo, tmp_path, model_server)
    wiring.runtime.run_turn("run it")
    wiring.session.close()
    wiring.renderer.console.file.flush()
    shown = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "ok exit code 0" in shown
    assert "row 8" in shown and f"row {9 - COMMAND_OUTPUT_LINES}" in shown
    assert f"row {8 - COMMAND_OUTPUT_LINES}" not in shown
    # The model was sent all of it, and the journal keeps no second copy.
    sent = json.dumps(model_server.requests[-1])
    assert "row 0" in sent
    assert all("output_tail" not in json.dumps(r.payload)
               for r in SessionStore.read_records(wiring.session.path))
