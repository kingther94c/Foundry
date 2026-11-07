"""Reads that worked say nothing more; anything that failed says why (D-076).

Exploring used to cost two lines a call -- ``-> read_file: src/app.py`` and
``ok`` -- and a failure said only ``failed``. codex lists what it read and
says when something went wrong. What carries the feature: only Foundry's own
read-only tools go quiet (an MCP server's tool may do anything), `-v` still
says ``ok``, and the reason a call failed is shown stripped and escaped, as
every tool result is.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.render import QUIET_WHEN_OK, Renderer
from foundry.cli.repl import RunTask, run_task
from foundry.core.events import TerminalStatus, ToolBegin, ToolEnd
from foundry.core.runtime import _tool_end_summary
from foundry.core.tools.base import Operation, ToolKind, ToolOutput
from foundry.core.tools.registry import default_registry

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)


def _render(*events, verbose=False):
    console = Console(file=io.StringIO(), width=300, color_system=None)
    renderer = Renderer(console=console, verbose=verbose)
    for event in events:
        renderer.handle(event)
    return console.file.getvalue()


def _read(tool="read_file", ok=True, summary="1: import os"):
    return (ToolBegin("c1", tool, f"{tool}: src/app.py"),
            ToolEnd("c1", tool, ok, summary, duration_ms=2))


# --- quiet reads -------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(QUIET_WHEN_OK))
def test_a_read_that_worked_is_its_line_alone(tool):
    assert _render(*_read(tool)).splitlines() == [f"-> {tool}: src/app.py"]


def test_with_v_it_says_ok():
    assert _render(*_read(), verbose=True).splitlines() == [
        "-> read_file: src/app.py", "   ok 1: import os"]


def test_the_quiet_ones_are_foundry_s_own_reading_tools():
    """Every built-in tool that only reads is quiet -- a new one is not
    forgotten -- and nothing else is: not a tool that changes the workspace,
    nor finish or update_plan, which only claim to read nothing."""
    registry = default_registry()
    reading = {name for name in registry.names()
               if registry.get(name).kind is ToolKind.READ_ONLY} - {"finish", "update_plan"}
    assert QUIET_WHEN_OK == reading


@pytest.mark.parametrize("tool", ["run_command", "apply_patch", "docs.lookup"])
def test_other_tools_still_say_ok(tool):
    assert "   ok" in _render(*_read(tool))


# --- failures say why --------------------------------------------------------------------


def test_a_failed_read_says_why():
    shown = _render(*_read(ok=False, summary="file not found: src/app.py"))
    assert shown.splitlines()[-1] == "   failed file not found: src/app.py"


def test_any_failure_says_why():
    shown = _render(*_read("docs.lookup", ok=False, summary="server went away"))
    assert shown.splitlines()[-1] == "   failed server went away"


def test_a_failure_with_nothing_to_say_says_failed():
    assert _render(*_read(ok=False, summary="")).splitlines()[-1] == "   failed"


def test_the_reason_is_never_markup_or_control():
    shown = _render(*_read(ok=False, summary="[red]no[/red] \x1b]52;c;ZXZpbA==\x07such file"))
    assert shown.splitlines()[-1] == "   failed [red]no[/red] such file"


# --- a patch that was refused in part ----------------------------------------------------


def _op():
    return Operation(tool="apply_patch", kind=ToolKind.MUTATOR, args={"patch": "", "paths": []},
                     display="apply_patch: a.py", target="a.py", digest="0")


def test_a_refused_patch_says_what_was_refused():
    output = ToolOutput(content="Applied:\n  updated a.py\n\nRejected (left untouched):\n"
                                "  b.py: file does not exist",
                        is_error=True,
                        metadata={"applied": 1, "rejected": 1,
                                  "rejections": ["b.py: file does not exist"]})
    assert _tool_end_summary(_op(), output, output.content) == "b.py: file does not exist"


def test_and_how_many_more():
    output = ToolOutput(content="Rejected (left untouched):\n  b.py: x\n  c.py: y\n  d.py: z",
                        is_error=True,
                        metadata={"applied": 0, "rejected": 3,
                                  "rejections": ["b.py: x", "c.py: y", "d.py: z"]})
    assert _tool_end_summary(_op(), output, output.content) == "b.py: x (and 2 more)"


def test_a_patch_that_landed_is_summed_up_as_before():
    output = ToolOutput(content="Applied:\n  updated a.py", metadata={"applied": 1, "rejected": 0})
    assert _tool_end_summary(_op(), output, output.content) == "Applied:"


# --- in a session ------------------------------------------------------------------------


PATCH_AB = ("*** Begin Patch\n*** Update File: a.py\n"
            "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n"
            "*** Update File: missing.py\n<<<<<<< SEARCH\nX\n=======\nY\n>>>>>>> REPLACE\n"
            "*** End Patch\n")


def test_a_session_reads_quietly_and_says_what_failed(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "read_file", {"path": "a.py"})),
        turn(call("c2", "read_file", {"path": "nope.py"})),
        turn(call("c3", "apply_patch", {"patch": PATCH_AB})),
        turn(text="Done."),
    ])
    run_task(wiring, RunTask("change A"))
    lines = wiring.renderer.console.file.getvalue().splitlines()
    first = lines.index("-> read a.py")
    assert lines[first + 1] == "-> read nope.py"                   # no "ok" between
    assert lines[first + 2].startswith("   failed ") and "nope.py" in lines[first + 2]
    patch_end = [line for line in lines if line.startswith("   failed missing.py")]
    assert patch_end and "file does not exist" in patch_end[0]
    assert "updated a.py (+1 -1)" in "\n".join(lines)            # what landed, as its diff
    app._close(wiring, TerminalStatus.COMPLETED, "test")
