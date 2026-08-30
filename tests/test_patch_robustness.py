"""Failure paths in the patch tool and the journal.

All four came from a review round that looked past the command segmenter. Each
one lost information the model or the user needed: a deleted file with nothing
saying so, a silently rewritten file, a whole-file diff from one edited line, a
traceback instead of an exit code.
"""

from __future__ import annotations

import os
import stat

import pytest

from foundry.core.session import ArtifactStore, EventType, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ReadFile
from foundry.core.tools.patch import TMP_SUFFIX, ApplyPatch, _detect_line_ending
from foundry.core.workspace import Workspace


@pytest.fixture()
def ctx(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return ToolContext(workspace=Workspace(repo),
                       artifacts=ArtifactStore(tmp_path / "artifacts"),
                       read_tracker=ReadTracker())


def envelope(body: str) -> str:
    return f"*** Begin Patch\n{body}\n*** End Patch"


def read(ctx, path: str) -> None:
    tool = ReadFile()
    tool.execute(tool.validate({"path": path}), ctx)


# --- a failure partway through an envelope -------------------------------


@pytest.mark.skipif(os.name != "nt", reason="uses a read-only file to force the failure")
def test_a_write_failure_does_not_hide_what_already_happened(ctx):
    """A read-only file mid-envelope used to raise out of execute(), discarding
    the record of what had already landed: an earlier delete was gone with
    nothing saying so, and the model saw only 'the tool failed unexpectedly'."""
    root = ctx.workspace.root
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "b.py").write_text("B = 1\n", encoding="utf-8")
    read(ctx, "b.py")
    os.chmod(root / "b.py", stat.S_IREAD)

    tool = ApplyPatch()
    try:
        out = tool.execute(tool.validate({"patch": envelope(
            "*** Delete File: a.py\n"
            "*** Update File: b.py\n<<<<<<< SEARCH\nB = 1\n=======\nB = 2\n>>>>>>> REPLACE"
        )}), ctx)
    finally:
        os.chmod(root / "b.py", stat.S_IWRITE)

    assert out.is_error
    assert "deleted a.py" in out.content, "the delete that succeeded must be reported"
    assert "b.py" in out.content
    assert not (root / "a.py").exists()


@pytest.mark.skipif(os.name != "nt", reason="uses a read-only file to force the failure")
def test_a_failed_write_leaves_no_temp_file(ctx):
    """A leftover .foundry-tmp shows up in git as an untracked file the session
    created."""
    root = ctx.workspace.root
    (root / "b.py").write_text("B = 1\n", encoding="utf-8")
    read(ctx, "b.py")
    os.chmod(root / "b.py", stat.S_IREAD)

    tool = ApplyPatch()
    try:
        tool.execute(tool.validate({"patch": envelope(
            "*** Update File: b.py\n<<<<<<< SEARCH\nB = 1\n=======\nB = 2\n>>>>>>> REPLACE"
        )}), ctx)
    finally:
        os.chmod(root / "b.py", stat.S_IWRITE)

    leftovers = [p.name for p in root.iterdir() if p.name.endswith(TMP_SUFFIX)]
    assert not leftovers, f"temp files left behind: {leftovers}"


# --- move destination validated before anything is written ---------------


def test_a_move_into_a_path_blocked_by_a_file_is_refused_before_writing(ctx):
    """mkdir(parents=True) raises when a component is an existing file, which
    surfaced only after the source had been rewritten -- and the model's retry
    then failed to locate, because its SEARCH text no longer matched."""
    root = ctx.workspace.root
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "d").write_text("this is a file, not a directory\n", encoding="utf-8")
    read(ctx, "a.py")

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: a.py\n*** Move to: d/b.py\n"
        "<<<<<<< SEARCH\nVALUE = 1\n=======\nVALUE = 2\n>>>>>>> REPLACE"
    )}), ctx)

    assert out.is_error
    assert "not a directory" in out.content
    assert (root / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n", \
        "the source must be untouched so a retry can still locate its anchor"


def test_a_move_into_a_new_subdirectory_still_works(ctx):
    root = ctx.workspace.root
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    read(ctx, "a.py")

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: a.py\n*** Move to: pkg/sub/b.py\n"
        "<<<<<<< SEARCH\nVALUE = 1\n=======\nVALUE = 2\n>>>>>>> REPLACE"
    )}), ctx)

    assert not out.is_error
    assert (root / "pkg" / "sub" / "b.py").read_text(encoding="utf-8") == "VALUE = 2\n"


# --- mixed line endings ---------------------------------------------------


def test_detect_line_ending_reports_mixed():
    assert _detect_line_ending("a\nb\nc\n") == ("\n", False)
    assert _detect_line_ending("a\r\nb\r\n") == ("\r\n", False)
    ending, mixed = _detect_line_ending("a\nb\nc\r\nd\n")
    assert mixed and ending == "\n", "the majority ending wins"
    ending, mixed = _detect_line_ending("a\r\nb\r\nc\nd\r\n")
    assert mixed and ending == "\r\n"


def test_a_mixed_ending_file_says_it_was_normalized(ctx):
    """The whole file is normalized to LF to match hunks against, so the
    original per-line endings cannot be restored. Silently converting them made
    a one-line edit look like a whole-file rewrite in git."""
    root = ctx.workspace.root
    (root / "f.py").write_bytes(b"A = 1\nB = 1\nC = 1\r\nD = 1\n")
    read(ctx, "f.py")

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: f.py\n<<<<<<< SEARCH\nB = 1\n=======\nB = 2\n>>>>>>> REPLACE"
    )}), ctx)

    assert not out.is_error
    assert "mixed line endings" in out.content


def test_a_uniform_file_reports_nothing_extra(ctx):
    root = ctx.workspace.root
    (root / "f.py").write_bytes(b"A = 1\r\nB = 1\r\n")
    read(ctx, "f.py")

    tool = ApplyPatch()
    out = tool.execute(tool.validate({"patch": envelope(
        "*** Update File: f.py\n<<<<<<< SEARCH\nB = 1\n=======\nB = 2\n>>>>>>> REPLACE"
    )}), ctx)

    assert not out.is_error
    assert "mixed line endings" not in out.content
    assert (root / "f.py").read_bytes() == b"A = 1\r\nB = 2\r\n"


# --- an unwritable journal degrades rather than crashing -----------------


class _FailingHandle:
    """Writes fine until it doesn't, like a volume filling up."""

    def __init__(self, real, fail_after: int):
        self._real = real
        self._left = fail_after
        self.closed = False

    def write(self, data):
        if self._left <= 0:
            raise OSError(28, "No space left on device")
        self._left -= 1
        return self._real.write(data)

    def flush(self):
        self._real.flush()

    def fileno(self):
        return self._real.fileno()

    def close(self):
        self.closed = True
        self._real.close()


def test_a_failing_journal_does_not_raise(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    store._fh = _FailingHandle(store._fh, fail_after=2)

    store.append(EventType.TOOL_CALL, {"n": 1})
    store.append(EventType.TOOL_CALL, {"n": 2})
    ordinal = store.append(EventType.TOOL_CALL, {"n": 3})  # this one fails

    assert ordinal == 3, "ordinals keep advancing so nothing silently reuses one"
    assert store.degraded
    assert "No space left" in store.degraded
    store.close()


def test_a_degraded_journal_is_reported_in_the_termination(tmp_path):
    """A run whose evidence is incomplete must say so; reading as a clean record
    of what happened would be worse than the lost lines."""
    from foundry.core.backends.replay import ScriptedBackend
    from foundry.core.context import ContextManager
    from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock, Usage
    from foundry.core.events import EventSink
    from foundry.core.policy.engine import PolicyEngine
    from foundry.core.runtime import AgentRuntime
    from foundry.core.tools.registry import default_registry
    import json

    repo = tmp_path / "repo"
    repo.mkdir()
    store = SessionStore(tmp_path / "sessions")
    store._fh = _FailingHandle(store._fh, fail_after=1)

    finish = ToolUseBlock(call_id="c1", name="finish", arguments=json.dumps(
        {"status": "completed", "summary": "done", "claims": []}))
    runtime = AgentRuntime(
        backend=ScriptedBackend([ModelTurn(text="", tool_calls=(finish,),
                                           usage=Usage(1, 1),
                                           stop_reason=StopReason.TOOL_USE)]),
        registry=default_registry(), policy=PolicyEngine(),
        context=ContextManager(),
        tool_ctx=ToolContext(workspace=Workspace(repo), artifacts=store.artifacts,
                             read_tracker=ReadTracker()),
        session=store, events=EventSink(),
    )
    outcome = runtime.run_turn("go")
    store.close()

    assert "journal could not be written" in outcome.summary
