"""What a patch changed, in view (D-075): each file it touched, with its
added and removed lines, then its diff -- as codex shows an edit, whether or
not anyone was asked first.

What carries the feature: the diff is what landed on disk (not what was
proposed), every file's own, and a file's text -- anyone's, a credential's
included -- reaches the screen and `exec --json` scrubbed, stripped and never
read as markup. The model is told what it was told before; the journal keeps
no copy.
"""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from foundry.cli.render import PATCH_DIFF_LINES, Renderer
from foundry.cli.repl import RunTask, run_task
from foundry.cli.unattended import _event_payload
from foundry.cli.wiring import _close
from foundry.core.events import EventSink, TerminalStatus, ToolEnd
from foundry.core.redaction import Redactor
from foundry.core.session import ArtifactStore, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ReadFile
from foundry.core.tools.patch import (
    DIFF_MAX_LINE_CHARS,
    DIFF_MAX_LINES,
    ApplyPatch,
    bounded_diff,
    file_diff,
)
from foundry.core.workspace import Workspace

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)

CANARY = "sk-canaryLEAK0123456789abcdefXYZ"
APP = "def run():\n    a = 1\n    b = 2\n    return a + b\n"


@pytest.fixture()
def ctx(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(APP, encoding="utf-8")
    return ToolContext(workspace=Workspace(root), artifacts=ArtifactStore(tmp_path / "art"),
                       read_tracker=ReadTracker())


def _read(ctx, path):
    tool = ReadFile()
    tool.execute(tool.validate({"path": path}), ctx)


def _patch(ctx, body):
    tool = ApplyPatch()
    return tool.execute(tool.validate({"patch": f"*** Begin Patch\n{body}\n*** End Patch"}), ctx)


UPDATE = ("*** Update File: src/app.py\n<<<<<<< SEARCH\n    b = 2\n=======\n"
          "    b = 3\n    c = 4\n>>>>>>> REPLACE")


# --- the diff ----------------------------------------------------------------------------


def test_an_update_shows_its_lines(ctx):
    _read(ctx, "src/app.py")
    out = _patch(ctx, UPDATE)
    assert out.metadata["diff"].splitlines() == [
        "updated src/app.py (+2 -1)",
        "@@ -1,4 +1,5 @@",
        " def run():",
        "     a = 1",
        "-    b = 2",
        "+    b = 3",
        "+    c = 4",
        "     return a + b",
    ]
    assert "@@" not in out.content and "+    c = 4" not in out.content   # the model's copy


def test_crlf_files_diff_by_their_lines(ctx):
    (ctx.workspace.root / "src" / "app.py").write_bytes(APP.replace("\n", "\r\n").encode())
    _read(ctx, "src/app.py")
    out = _patch(ctx, UPDATE)
    assert out.metadata["diff"].splitlines()[0] == "updated src/app.py (+2 -1)"
    assert "\r" not in out.metadata["diff"]


def test_a_created_file_is_all_added(ctx):
    out = _patch(ctx, "*** Add File: src/new.py\n+x = 1\n+y = 2")
    lines = out.metadata["diff"].splitlines()
    assert lines[0] == "created src/new.py (+2 -0)" and lines[-2:] == ["+x = 1", "+y = 2"]


def test_a_deleted_file_is_all_removed(ctx):
    out = _patch(ctx, "*** Delete File: src/app.py")
    lines = out.metadata["diff"].splitlines()
    assert lines[0] == "deleted src/app.py (+0 -4)" and lines[-1] == "-    return a + b"


def test_a_deleted_file_that_is_not_text_is_named_only(ctx):
    (ctx.workspace.root / "blob.bin").write_bytes(b"\xff\xfe\x00binary")
    out = _patch(ctx, "*** Delete File: blob.bin")
    assert out.metadata["diff"] == "deleted blob.bin"


def test_a_move_says_where_to(ctx):
    _read(ctx, "src/app.py")
    out = _patch(ctx, UPDATE.replace("src/app.py\n", "src/app.py\n*** Move to: src/main.py\n", 1))
    assert out.metadata["diff"].splitlines()[0] == (
        "updated and moved src/app.py -> src/main.py (+2 -1)")


def test_each_file_its_own(ctx):
    (ctx.workspace.root / "b.py").write_text("B = 5\n", encoding="utf-8")
    _read(ctx, "src/app.py")
    _read(ctx, "b.py")
    out = _patch(ctx, UPDATE + "\n*** Update File: b.py\n<<<<<<< SEARCH\nB = 5\n=======\n"
                 "B = 6\n>>>>>>> REPLACE")
    headers = [line for line in out.metadata["diff"].splitlines() if line[:1].isalpha()]
    assert headers == ["updated src/app.py (+2 -1)", "updated b.py (+1 -1)"]


def test_only_what_landed(ctx):
    """A file the patch could not change is the model's to hear about, not a
    diff of something that did not happen."""
    _read(ctx, "src/app.py")
    out = _patch(ctx, UPDATE + "\n*** Update File: missing.py\n<<<<<<< SEARCH\nx\n=======\n"
                 "y\n>>>>>>> REPLACE")
    assert out.is_error and out.metadata["applied"] == 1
    assert "missing.py" not in out.metadata["diff"]


def test_nothing_landed_nothing_shown(ctx):
    out = _patch(ctx, UPDATE)                            # not read first: refused
    assert out.is_error and "diff" not in out.metadata


def test_a_long_line_is_never_cut():
    """Half a credential is one the redactor cannot recognize."""
    long_line = "k" * (DIFF_MAX_LINE_CHARS + 1)
    lines = file_diff("updated x", "", long_line + "\n")
    assert lines[-1] == f"+({DIFF_MAX_LINE_CHARS + 1} characters)"
    assert file_diff("updated x", "", "k" * DIFF_MAX_LINE_CHARS)[-1] == "+" + "k" * DIFF_MAX_LINE_CHARS


def test_the_whole_is_bounded():
    lines = [f"+{i}" for i in range(DIFF_MAX_LINES + 7)]
    shown = bounded_diff(lines).splitlines()
    assert len(shown) == DIFF_MAX_LINES + 1 and shown[-1] == "(7 more lines)"
    assert bounded_diff(lines[:DIFF_MAX_LINES]) == "\n".join(lines[:DIFF_MAX_LINES])


# --- on the screen -----------------------------------------------------------------------


def _render(*events, verbose=False):
    console = Console(file=io.StringIO(), width=300, color_system=None)
    renderer = Renderer(console=console, verbose=verbose)
    for event in events:
        renderer.handle(event)
    return console.file.getvalue()


def _end(diff, ok=True):
    return ToolEnd("c1", "apply_patch", ok, "Applied:", duration_ms=3, output=diff)


def test_it_is_shown_under_the_patch():
    shown = _render(_end("updated a.py (+1 -1)\n@@ -1 +1 @@\n-A = 1\n+A = 2"))
    assert shown.splitlines() == ["   ok", "     updated a.py (+1 -1)", "     @@ -1 +1 @@",
                                  "     -A = 1", "     +A = 2"]


def test_a_long_diff_is_cut_unless_verbose():
    diff = "\n".join(["updated a.py (+60 -0)", *(f"+{i}" for i in range(60))])
    shown = _render(_end(diff))
    assert f"+{PATCH_DIFF_LINES - 2}" in shown and f"+{PATCH_DIFF_LINES - 1}\n" not in shown
    assert f"... {61 - PATCH_DIFF_LINES} more lines (-v shows them)" in shown
    assert "+59" in _render(_end(diff), verbose=True)


def test_its_colours():
    console = Console(file=io.StringIO(), width=300, force_terminal=True, color_system="standard")
    Renderer(console=console).handle(_end("updated a.py (+1 -1)\n@@ -1 +1 @@\n-A = 1\n+A = 2"))
    shown = console.file.getvalue()
    assert "\x1b[32m     +A = 2" in shown and "\x1b[31m     -A = 1" in shown
    assert "\x1b[36m     @@" in shown and "\x1b[1m     updated a.py" in shown


def test_a_file_s_text_is_never_markup_or_control():
    shown = _render(_end("updated a.py (+1 -0)\n+[red]x[/red] \x1b]52;c;ZXZpbA==\x07list[int]"))
    assert "+[red]x[/red] list[int]" in shown and "\x1b" not in shown


def test_a_command_s_output_is_still_its_tail():
    end = ToolEnd("c1", "run_command", True, "exit code 0", output="\n".join(map(str, range(9))))
    assert _render(end).splitlines()[-1] == "     8"


def test_a_credential_in_the_diff_never_leaves():
    redactor = Redactor()
    redactor.register(CANARY)
    sink = EventSink(redactor=redactor)
    console = Console(file=io.StringIO(), width=300, color_system=None)
    sink.subscribe(Renderer(console=console).handle)
    seen = []
    sink.subscribe(seen.append)
    sink.emit(_end(f"updated .env (+1 -0)\n+TOKEN={CANARY}"))
    assert CANARY not in console.file.getvalue() and "+TOKEN=[redacted]" in console.file.getvalue()
    assert CANARY not in json.dumps(_event_payload(seen[0]))


# --- in a session ------------------------------------------------------------------------


PATCH_A = ("*** Begin Patch\n*** Update File: a.py\n"
           "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n*** End Patch\n")


def test_a_session_shows_what_its_patch_changed(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "read_file", {"path": "a.py"})),
        turn(call("c2", "apply_patch", {"patch": PATCH_A})),
        turn(text="Patched."),
    ])
    events = []
    wiring.runtime.events.subscribe(events.append)
    run_task(wiring, RunTask("change A"))
    shown = wiring.renderer.console.file.getvalue()
    assert "updated a.py (+1 -1)" in shown and "-A = 1" in shown and "+A = 2" in shown
    end = [e for e in events if isinstance(e, ToolEnd) and e.call_id == "c2"][0]
    assert end.output.startswith("updated a.py (+1 -1)\n@@")
    path = wiring.session.path
    _close(wiring, TerminalStatus.COMPLETED, "test")
    journal = "\n".join(json.dumps(r.payload) for r in SessionStore.read_records(path))
    assert "@@ -1 +1 @@" not in journal                  # shown, never journaled
