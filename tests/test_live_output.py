"""The newest line a running command printed, in the working line (D-074):
``running (2m 10s, Ctrl+C to stop): collected 340 items``.

What carries the feature: the line arrives while the command runs (a pipe
read that waits for 64 KB showed nothing for minutes), and only whole lines
leave -- the redactor recognizes a credential only whole, so half of one on
the screen is half of one leaked.
"""

from __future__ import annotations

import io
import time

import pytest
from rich.console import Console

from foundry.cli.render import Renderer, Working
from foundry.cli.repl import RunTask, run_task
from foundry.cli.wiring import _close
from foundry.core.events import EventSink, TerminalStatus, ToolBegin, ToolEnd, ToolOutputDelta
from foundry.core.redaction import Redactor
from foundry.core.tools import command
from foundry.core.tools.command import (
    OUTPUT_WINDOW_BYTES,
    PipeEnd,
    RunCommand,
    _drain,
    newest_line,
    run_process,
)

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)

CANARY = "sk-canaryLEAK0123456789abcdefXYZ"


# --- the line ----------------------------------------------------------------------------


@pytest.mark.parametrize("data, line", [
    (b"", ""),
    (b"no end yet", ""),                                  # a line still being written
    (b"one\ntwo\n", "two"),
    (b"one\ntwo\nthree, half", "two"),
    (b"one\r\ntwo\r\n", "two"),
    (b"done\n\n   \n", "done"),                            # blank lines are skipped
    (b"  10%\r  55%\r", "55%"),                            # a progress bar redraws
    (b"collected 340 items\r\n\r\n", "collected 340 items"),
])
def test_the_newest_complete_line(data, line):
    assert newest_line(data) == line


def test_a_full_window_drops_its_first_line():
    """It may begin partway through a line, a credential's included."""
    tail = b"\n" + CANARY.encode() + b"\nlast\n"
    data = (b"x" * OUTPUT_WINDOW_BYTES + tail)[-OUTPUT_WINDOW_BYTES:]
    assert newest_line(data) == "last"
    cut = (CANARY.encode() + b"\n")[5:]              # all a full window holds of a line
    assert newest_line(b"y" * (OUTPUT_WINDOW_BYTES - len(cut)) + cut) == ""


def test_utf16_text_comes_without_nuls():
    """A credential spelled with NULs between its letters is one the redactor
    cannot match."""
    assert newest_line(f"token {CANARY}\n".encode("utf-16-le")) == f"token {CANARY}"


# --- the reader --------------------------------------------------------------------------


class _Pipe:
    """A pipe whose ``read`` would wait for 64 KB: only ``read1`` may be used."""

    def __init__(self, *chunks):
        self.chunks = list(chunks)

    def read1(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def read(self, size):
        raise AssertionError("read() waits for a full buffer on a pipe")

    def close(self):
        pass


def test_the_reader_takes_what_has_arrived():
    sink, dropped, end = [], [0], PipeEnd()
    _drain(_Pipe(b"one\n", b"two\n"), 1000, sink, dropped, end)
    assert b"".join(sink) == b"one\ntwo\n" and newest_line(end.data) == "two"


def test_the_end_is_kept_past_the_capture_budget():
    sink, dropped, end = [], [0], PipeEnd()
    _drain(_Pipe(b"head\n", b"x" * 10_000 + b"\n", b"the end\n"), 4, sink, dropped, end)
    assert b"".join(sink) == b"head" and dropped[0] > 0
    assert len(end.data) == OUTPUT_WINDOW_BYTES and newest_line(end.data) == "the end"


def test_a_line_ends_only_with_a_line_end():
    end = PipeEnd()
    _drain(_Pipe(b"half a line"), 1000, [], [0], end)
    assert end.line_ended == 0.0
    _drain(_Pipe(b"\r"), 1000, [], [0], end)
    assert end.line_ended > 0.0


# --- while a command runs ----------------------------------------------------------------


def _script(tmp_path, body):
    (tmp_path / "slow.py").write_text(body, encoding="utf-8")
    return "python slow.py"


def _watch(tmp_path, body, sink=None):
    seen = []

    def told(line, stream):
        seen.append((time.monotonic(), line, stream))
        if sink is not None:
            sink.emit(ToolOutputDelta("c1", line, stream))

    result = run_process(_script(tmp_path, body), cwd=str(tmp_path), timeout_s=60,
                         on_output=told)
    return result, seen


def test_lines_arrive_while_it_runs(tmp_path):
    result, seen = _watch(tmp_path, (
        "import sys, time\n"
        "print('first', flush=True); time.sleep(1.5)\n"
        "print('second', flush=True); time.sleep(1.5)\n"))
    assert result.exit_code == 0
    assert [(line, stream) for _, line, stream in seen] == [
        ("first", "stdout"), ("second", "stdout")]      # each once, though seen often


def test_at_most_every_half_second(tmp_path):
    result, seen = _watch(tmp_path, (
        "import time\n"
        "for i in range(60):\n"
        "    print('line', i, flush=True); time.sleep(0.05)\n"))
    assert result.exit_code == 0 and len(seen) >= 2
    gaps = [later[0] - earlier[0] for earlier, later in zip(seen, seen[1:])]
    assert min(gaps) >= command.OUTPUT_EVERY_S - 0.05


def test_the_pipe_a_line_last_ended_on(tmp_path):
    _, seen = _watch(tmp_path, (
        "import sys, time\n"
        "print('out', flush=True); time.sleep(0.2)\n"
        "print('err', file=sys.stderr, flush=True); time.sleep(1.5)\n"))
    assert seen[-1][1:] == ("err", "stderr")


def test_a_line_not_yet_ended_says_nothing(tmp_path):
    _, seen = _watch(tmp_path, (
        "import sys, time\n"
        "sys.stdout.write('3/10 '); sys.stdout.flush(); time.sleep(1.5)\n"))
    assert seen == []


def test_half_a_credential_never_leaves(tmp_path):
    redactor = Redactor()
    redactor.register(CANARY)
    sink = EventSink(redactor=redactor)
    events = []
    sink.subscribe(events.append)
    _, seen = _watch(tmp_path, (
        "import sys, time\n"
        f"sys.stdout.write('token {CANARY[:12]}'); sys.stdout.flush(); time.sleep(1.5)\n"
        f"print('{CANARY[12:]}', flush=True); time.sleep(1.5)\n"), sink)
    assert [line for _, line, _ in seen] == [f"token {CANARY}"]     # whole, once
    assert [event.text for event in events] == ["token [redacted]"]


def test_the_tool_passes_it_on(tmp_path):
    from foundry.core.tools.base import ReadTracker, ToolContext
    from foundry.core.workspace import Workspace

    seen = []
    ctx = ToolContext(workspace=Workspace(tmp_path), artifacts=None,
                      read_tracker=ReadTracker(),
                      on_output=lambda line, stream: seen.append(line))
    tool = RunCommand()
    tool.execute(tool.validate({"command": _script(tmp_path, (
        "import time\nprint('step 1', flush=True); time.sleep(1.5)\n"))}), ctx)
    assert seen == ["step 1"]


# --- in a session ------------------------------------------------------------------------


def test_a_session_emits_it_for_the_call(repo, tmp_path):
    (repo / "slow.py").write_text(
        "import time\nprint('collected 340 items', flush=True); time.sleep(1.5)\n",
        encoding="utf-8")
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "run_command", {"command": "python slow.py"})),
        turn(text="Done."),
    ])
    events = []
    wiring.runtime.events.subscribe(events.append)
    run_task(wiring, RunTask("run it"))
    kinds = [type(event) for event in events]
    delta = events[kinds.index(ToolOutputDelta)]
    assert (delta.call_id, delta.text, delta.stream) == ("c1", "collected 340 items", "stdout")
    assert kinds.index(ToolBegin) < kinds.index(ToolOutputDelta) < kinds.index(ToolEnd)
    assert wiring.runtime.tool_ctx.on_output is None
    _close(wiring, TerminalStatus.COMPLETED, "test")


def test_a_call_that_stops_leaves_nothing_to_tell(repo, tmp_path, monkeypatch):
    def stopped(self, op, ctx):
        assert ctx.on_output is not None
        raise KeyboardInterrupt

    monkeypatch.setattr(RunCommand, "execute", stopped)
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "run_command", {"command": "python slow.py"})),
    ])
    outcome = run_task(wiring, RunTask("run it"))
    assert outcome.status is TerminalStatus.CANCELLED
    assert wiring.runtime.tool_ctx.on_output is None
    _close(wiring, outcome.status, "test")


# --- on the screen -----------------------------------------------------------------------


def _terminal(width=100):
    return Console(file=io.StringIO(), force_terminal=True, width=width, color_system=None)


@pytest.fixture()
def renderer():
    renderer = Renderer(console=_terminal())
    yield renderer
    renderer.idle()


def test_it_joins_the_working_line(renderer):
    renderer.handle(ToolBegin("c1", "run_command", "pytest"))
    live = renderer._live
    renderer.handle(ToolOutputDelta("c1", "collected 340 items"))
    assert renderer._live is live                       # still up, not redrawn anew
    assert renderer._working.text().startswith("running (")
    assert renderer._working.text().endswith(", Ctrl+C to stop): collected 340 items")


def test_without_a_line_up_it_is_nothing():
    console = Console(file=io.StringIO(), width=100)     # a pipe: no working line
    renderer = Renderer(console=console)
    renderer.handle(ToolBegin("c1", "run_command", "pytest"))
    renderer.handle(ToolOutputDelta("c1", "collected 340 items"))
    assert "collected" not in console.file.getvalue()


def test_control_sequences_are_stripped(renderer):
    renderer.handle(ToolBegin("c1", "run_command", "x"))
    renderer.handle(ToolOutputDelta("c1", "\x1b[2J\x1b]0;title\x07plain"))
    assert renderer._working.latest == "plain"


def test_a_long_line_is_cut_to_one(renderer):
    working = Working("running", time.monotonic())
    working.latest = "word " * 40
    console = _terminal(width=40)
    console.print(working)
    drawn = console.file.getvalue().rstrip("\n")
    assert "\n" not in drawn and drawn.endswith("…")
