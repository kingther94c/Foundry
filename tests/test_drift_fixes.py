"""Places where one layer learned something the layer beside it never did.

Each of these is a seam: the tool layer resolves a path the policy layer spells
differently, a bound inverts below a threshold nobody tested, a guard switches
itself off in the state it exists for.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from foundry.core.errors import ConfigError, ToolError
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import ReadTracker, ToolContext, truncate_middle
from foundry.core.tools.files import ReadArtifact
from foundry.core.workspace import Workspace


# --- the output bound never exceeds itself -------------------------------


@pytest.mark.parametrize("limit", [1, 10, 40, 79, 80, 81, 82, 100, 500, 4000])
def test_truncation_never_returns_more_than_the_limit(limit):
    """`limit // 2 - 40` reaches zero at 80 and goes negative below it, and
    text[-0:] is the whole string -- so a small cap returned the entire input,
    and a negative one returned the input with its middle duplicated."""
    body, truncated = truncate_middle("x" * 5000, limit)
    assert truncated
    assert len(body) <= limit, f"limit {limit} produced {len(body)} characters"


@pytest.mark.parametrize("limit", [1, 40, 79, 80])
def test_a_limit_too_small_for_a_banner_becomes_a_head_cut(limit):
    body, truncated = truncate_middle("y" * 1000, limit)
    assert truncated
    assert body == "y" * limit


def test_a_workable_limit_still_keeps_head_and_tail():
    body, truncated = truncate_middle("HEAD" + ("m" * 5000) + "TAIL", 1000)
    assert truncated
    assert body.startswith("HEAD") and body.endswith("TAIL")
    assert "elided" in body


def test_config_rejects_an_output_cap_too_small_to_be_useful(tmp_path):
    from foundry.core.config import load_config

    (tmp_path / "config.toml").write_text(
        "[runtime]\nmax_output_bytes = 80\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="at least 1024"):
        load_config(home=tmp_path)


# --- read_artifact says when there is more --------------------------------


def _artifact_ctx(tmp_path, text: str, *, cap: int = 4000):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.put_text(text)
    ctx = ToolContext(workspace=Workspace(repo), artifacts=store,
                      read_tracker=ReadTracker(), max_output_bytes=cap)
    return ref, ctx


def test_read_artifact_reports_that_more_remains(tmp_path):
    """It capped at read_text and then handed the already-capped text to
    truncate_middle, which therefore could never fire: the one tool the others
    point at for the full data was the only one that cut silently."""
    ref, ctx = _artifact_ctx(tmp_path, "\n".join(f"line {i:06d}" for i in range(20000)))
    tool = ReadArtifact()

    out = tool.execute(tool.validate({"artifact_id": ref.artifact_id}), ctx)

    assert out.truncated
    assert "offset=4000" in out.content, "the model needs to know how to continue"


def test_read_artifact_paging_reaches_a_clean_end(tmp_path):
    ref, ctx = _artifact_ctx(tmp_path, "A" * 9000)
    tool = ReadArtifact()

    first = tool.execute(tool.validate({"artifact_id": ref.artifact_id}), ctx)
    assert first.truncated
    last = tool.execute(
        tool.validate({"artifact_id": ref.artifact_id, "offset": 8000}), ctx)
    assert not last.truncated
    assert last.content == "A" * 1000


def test_read_artifact_that_fits_exactly_is_not_called_truncated(tmp_path):
    ref, ctx = _artifact_ctx(tmp_path, "B" * 4000)
    tool = ReadArtifact()

    out = tool.execute(tool.validate({"artifact_id": ref.artifact_id}), ctx)

    assert not out.truncated
    assert out.content == "B" * 4000


def test_read_artifact_past_the_end_is_an_error_not_silence(tmp_path):
    ref, ctx = _artifact_ctx(tmp_path, "short")
    tool = ReadArtifact()

    with pytest.raises(ToolError, match="past the end"):
        tool.execute(
            tool.validate({"artifact_id": ref.artifact_id, "offset": 9999}), ctx)


# --- HEAD movement is detected in a repo with no commits ------------------


def _bare_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, capture_output=True)
    return repo


def test_a_first_commit_during_the_session_is_detected(tmp_path):
    """A repo with no commits records head='', and the guard tested that string
    for truthiness -- switching itself off for the whole session, in exactly
    the case it exists to catch."""
    from foundry.core.tools.git import capture_baseline, collect_evidence

    repo = _bare_repo(tmp_path)
    baseline = capture_baseline(repo)
    assert baseline.head == "", "the fixture is meant to start with no commits"

    (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=repo, capture_output=True)

    assert collect_evidence(repo, baseline).head_moved


def test_a_repo_that_stays_empty_is_not_a_false_positive(tmp_path):
    from foundry.core.tools.git import capture_baseline, collect_evidence

    repo = _bare_repo(tmp_path)
    baseline = capture_baseline(repo)
    assert not collect_evidence(repo, baseline).head_moved


# --- policy sees the spelling the filesystem agrees with ------------------


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 aliases are a Windows feature")
def test_an_8_3_alias_does_not_slip_past_the_dirty_file_guard(tmp_path):
    """patch.py resolves a short name to the file it aliases; the policy layer
    compared raw strings, so the alias missed the dirty-file rule and fell
    through to accept_edits, rewriting an uncommitted file with no prompt."""
    from foundry.core.backends.replay import ScriptedBackend
    from foundry.core.context import ContextManager
    from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock, Usage
    from foundry.core.events import ApprovalChoice, EventSink
    from foundry.core.policy.engine import Mode, PolicyEngine
    from foundry.core.runtime import AgentRuntime, Budget
    from foundry.core.tools.registry import default_registry

    repo = tmp_path / "repo"
    repo.mkdir()
    long_name = "averylongfilename.py"
    (repo / long_name).write_text("VALUE = 1\n", encoding="utf-8")

    probe = subprocess.run(["cmd", "/c", "dir", "/x", str(repo)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    alias = next((tok for tok in probe.stdout.split() if "~1" in tok), None)
    if alias is None:
        pytest.skip("8.3 alias generation is disabled on this volume")

    def block(name, args, call_id):
        return ToolUseBlock(call_id=call_id, name=name, arguments=json.dumps(args))

    def turn(*calls, text=""):
        return ModelTurn(text=text, tool_calls=tuple(calls), usage=Usage(10, 5),
                         stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)

    patch = ("*** Begin Patch\n"
             f"*** Update File: {alias}\n"
             "<<<<<<< SEARCH\nVALUE = 1\n=======\nVALUE = 2\n>>>>>>> REPLACE\n"
             "*** End Patch\n")
    turns = [
        turn(block("read_file", {"path": long_name}, "c0")),
        turn(block("apply_patch", {"patch": patch}, "c1")),
        turn(text="done"),
    ]

    asked: list[str] = []
    store = ArtifactStore(tmp_path / "artifacts")
    runtime = AgentRuntime(
        backend=ScriptedBackend(turns), registry=default_registry(),
        policy=PolicyEngine(mode=Mode.ACCEPT_EDITS, dirty_files={long_name}),
        context=ContextManager(system_prompt="test"),
        tool_ctx=ToolContext(workspace=Workspace(repo), artifacts=store,
                             read_tracker=ReadTracker()),
        events=EventSink(), budget=Budget(),
        approval=lambda req: (asked.append(req.display), ApprovalChoice.DENY)[1],
    )
    runtime.run_turn("edit it")

    assert asked, "an alias for a dirty file must still reach the approval prompt"
    assert (repo / long_name).read_text(encoding="utf-8") == "VALUE = 1\n"
