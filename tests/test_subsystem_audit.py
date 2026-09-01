"""Findings from reviewing the subsystems six earlier rounds barely touched.

The prior rounds went at the segmenter, apply_patch, the journal, redaction and
the transport. These are the file tools, the evidence chain's ordering, and the
commands that read journals back.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from foundry.core.errors import ToolError
from foundry.core.session import ArtifactStore, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ListFiles, SearchText
from foundry.core.tools.finish import ValidationClaim, verify_claims
from foundry.core.workspace import Workspace


def _ctx(tmp_path, **kwargs):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return repo, ToolContext(workspace=Workspace(repo),
                             artifacts=ArtifactStore(tmp_path / "artifacts"),
                             read_tracker=ReadTracker(), **kwargs)


# --- evidence must post-date the change it vouches for --------------------


def _history(*pairs):
    return [{"event_ordinal": n, "command": cmd, "exit_code": 0} for n, cmd in pairs]


def test_a_command_that_ran_before_the_edit_cannot_validate_it():
    """The gate only asked "did this command exit 0". A model that runs the suite
    green, then edits the code and never re-runs it, cited the earlier ordinal --
    exit code matched, and a session that verified nothing reported completed."""
    result = verify_claims(
        [ValidationClaim(claim_text="the suite passes", command_event_id=4)],
        _history((4, "pytest -q")), "completed", last_mutation_ordinal=9)

    assert result.status.value == "partial"
    assert result.rejected
    assert "before the last change" in result.rejected[0]


def test_a_command_that_ran_after_the_edit_still_validates_it():
    result = verify_claims(
        [ValidationClaim(claim_text="the suite passes", command_event_id=12)],
        _history((12, "pytest -q")), "completed", last_mutation_ordinal=9)

    assert result.status.value == "completed"
    assert not result.rejected


def test_the_runtime_records_when_the_workspace_last_changed(tmp_path):
    """verify_claims can only order what the runtime tells it about, so this is
    the half that has to hold end to end: run the suite green, then edit, then
    cite the earlier run."""
    from foundry.core.backends.replay import ScriptedBackend
    from foundry.core.context import ContextManager
    from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock, Usage
    from foundry.core.events import EventSink
    from foundry.core.policy.engine import Mode, PolicyEngine, Rule, Verdict
    from foundry.core.runtime import AgentRuntime, Budget
    from foundry.core.tools.registry import default_registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("VALUE = 1\n", encoding="utf-8")

    def call(cid, name, args):
        return ToolUseBlock(call_id=cid, name=name, arguments=json.dumps(args))

    def turn(*calls, text=""):
        return ModelTurn(text=text, tool_calls=tuple(calls), usage=Usage(1, 1),
                         stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)

    patch = ("*** Begin Patch\n*** Update File: calc.py\n"
             "<<<<<<< SEARCH\nVALUE = 1\n=======\nVALUE = 2\n>>>>>>> REPLACE\n"
             "*** End Patch\n")

    script = [
        turn(call("c1", "run_command", {"command": "python --version"})),
        turn(call("c2", "read_file", {"path": "calc.py"})),
        turn(call("c3", "apply_patch", {"patch": patch})),
    ]

    class _CitesThePreEditRun(ScriptedBackend):
        """Reads the event id back the way a real model does, then cites the
        run from *before* the edit. Its exit code is 0, so only the ordering
        check can catch it."""

        def stream_turn(self, request):
            if len(self.seen) < len(script):
                return super().stream_turn(request)
            self.seen.append(request)
            from foundry.core.backends.base import (
                ToolCallComplete,
                TurnFinished,
                UsageUpdate,
            )
            # The ordinal of the run that happened before the edit -- the same
            # source _finalize consults.
            pre_edit = registry.tools["run_command"].history[0]["event_ordinal"]
            finish = turn(call("c4", "finish", {
                "status": "completed", "summary": "changed it",
                "claims": [{"claim_text": "verified", "command_event_id": pre_edit}]}))
            return iter([ToolCallComplete(finish.tool_calls[0]),
                         UsageUpdate(finish.usage), TurnFinished(finish)])

    session = SessionStore(tmp_path / "sessions")
    registry = default_registry(recorder=session)
    policy = PolicyEngine(mode=Mode.ACCEPT_EDITS)
    policy.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    runtime = AgentRuntime(
        backend=_CitesThePreEditRun(list(script)),
        registry=registry, policy=policy,
        context=ContextManager(system_prompt="t"),
        tool_ctx=ToolContext(workspace=Workspace(repo), artifacts=session.artifacts,
                             read_tracker=ReadTracker()),
        session=session, events=EventSink(), budget=Budget(),
    )
    outcome = runtime.run_turn("change it")
    session.close()

    assert runtime._last_mutation > 0, "the patch was never recorded as a mutation"
    assert outcome.status.value == "partial", outcome.text
    assert "before the last change" in outcome.text


def test_a_session_that_changed_nothing_needs_no_ordering():
    result = verify_claims(
        [ValidationClaim(claim_text="the suite passes", command_event_id=3)],
        _history((3, "pytest -q")), "completed", last_mutation_ordinal=0)
    assert result.status.value == "completed"


# --- a search must not hide what it did not read --------------------------


def test_a_file_that_is_not_utf8_is_named_not_skipped_silently(tmp_path):
    """"(no matches)" with skipped=0 read as "this string is nowhere in the
    workspace". A config written by PowerShell redirection is UTF-16."""
    repo, ctx = _ctx(tmp_path)
    (repo / "config.txt").write_bytes("SECRET_KEY = hunter2\n".encode("utf-16-le"))

    tool = SearchText()
    out = tool.execute(tool.validate({"query": "SECRET_KEY"}), ctx)

    assert out.metadata["undecodable"] == 1
    assert "not UTF-8" in out.content
    assert "config.txt" in out.content
    assert "does not cover them" in out.content


def test_a_decodable_hit_is_still_found_alongside(tmp_path):
    repo, ctx = _ctx(tmp_path)
    (repo / "a.py").write_text("SECRET_KEY = 1\n", encoding="utf-8")
    (repo / "blob.bin").write_bytes(b"\xff\xfe\x00\x01SECRET_KEY")

    tool = SearchText()
    out = tool.execute(tool.validate({"query": "SECRET_KEY"}), ctx)

    assert "a.py:1" in out.content
    assert out.metadata["undecodable"] == 1


def test_searching_a_single_file_actually_searches_it(tmp_path):
    """os.walk over a file yields nothing, so narrowing a search to one file
    answered "(no matches)" with no error -- and the model concluded the symbol
    was gone."""
    repo, ctx = _ctx(tmp_path)
    (repo / "runtime.py").write_text("def run_turn(self):\n    pass\n", encoding="utf-8")

    tool = SearchText()
    out = tool.execute(tool.validate({"query": "def run_turn", "path": "runtime.py"}), ctx)

    assert out.metadata["count"] == 1
    assert "runtime.py:1" in out.content


def test_searching_a_path_that_does_not_exist_is_an_error(tmp_path):
    repo, ctx = _ctx(tmp_path)
    tool = SearchText()
    with pytest.raises(ToolError):
        tool.execute(tool.validate({"query": "x", "path": "nope"}), ctx)


@pytest.mark.parametrize("pattern", ["(a+)+b", "(a*)*", r"(\s*\w+)+$", "(x+)+y", "(ab+)+c"])
def test_a_catastrophic_pattern_is_refused_not_run(pattern):
    """`re` has no step limit and holds the GIL while backtracking, so one line
    of ~40 repeated characters never returns and cannot be interrupted -- the
    agent is gone for good. There is no checkpoint inside a single match, so the
    only place to stop this is before it starts."""
    from foundry.core.errors import InvalidToolCall

    with pytest.raises(InvalidToolCall, match="backtrack"):
        SearchText().validate({"query": pattern})


@pytest.mark.parametrize("pattern", [
    "def run_turn", r"^\s*class \w+", "a+b", r"(foo|bar)+", r"[a+]*",
    r"\d{3,}", "(abc)+", r"(\w+)", "TODO|FIXME",
])
def test_ordinary_patterns_are_not_refused(pattern):
    """The check must be conservative the other way too: refusing normal
    searches would be worse than the hang it prevents."""
    assert SearchText().validate({"query": pattern}).args["query"] == pattern


def test_a_cancel_stops_a_long_search(tmp_path):
    repo, ctx = _ctx(tmp_path)
    for i in range(50):
        (repo / f"f{i}.txt").write_text("x\n" * 100, encoding="utf-8")
    ctx.cancelled = lambda: True

    tool = SearchText()
    assert tool.execute(tool.validate({"query": "x"}), ctx).metadata["incomplete"] is True


def test_list_files_respects_the_output_cap(tmp_path):
    """It returned whatever the model asked for; max_entries=100000 on a large
    tree produced a multi-megabyte tool result reported as truncated=False."""
    repo, ctx = _ctx(tmp_path, max_output_bytes=2000)
    for i in range(400):
        (repo / f"file_{i:04d}_with_a_fairly_long_name.py").write_text("x", encoding="utf-8")

    tool = ListFiles()
    out = tool.execute(tool.validate({"pattern": "*.py", "max_entries": 100000}), ctx)

    assert len(out.content) <= 2000
    assert out.truncated is True


# --- the commands that read journals back ---------------------------------


def _journal(tmp_path, *records) -> "object":
    path = tmp_path / "sessions" / "s1" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_a_journal_line_that_is_not_an_object_does_not_crash_the_reader(tmp_path):
    """Valid JSON, wrong shape: an array or a bare string parsed fine and then
    raised AttributeError on .get -- not a FoundryError, so the CLI died."""
    path = tmp_path / "j.jsonl"
    path.write_text(
        json.dumps({"ts": "t", "ordinal": 1, "type": "task", "payload": {}}) + "\n"
        + "[1, 2, 3]\n" + '"just a string"\n' + "null\n"
        + json.dumps({"ts": "t", "ordinal": 2, "type": "task", "payload": "not a dict"}) + "\n",
        encoding="utf-8")

    records = list(SessionStore.read_records(path))

    assert [r.ordinal for r in records] == [1, 2]
    assert records[1].payload == {}, "a non-dict payload must not reach a .get caller"


def test_a_timed_out_command_is_not_counted_as_a_success(tmp_path):
    """run_command records exit_code None for a process killed at the timeout.
    Folding None in with 0 scored every timed-out test run as passing."""
    from foundry.cli.report import analyse

    path = _journal(
        tmp_path,
        {"ts": "t", "ordinal": 1, "type": "command_exec",
         "payload": {"command": "pytest", "exit_code": None}},
        {"ts": "t", "ordinal": 2, "type": "command_exec",
         "payload": {"command": "pytest", "exit_code": 0}},
        {"ts": "t", "ordinal": 3, "type": "command_exec",
         "payload": {"command": "pytest", "exit_code": 1}},
    )
    report = analyse(path)

    assert report.commands == 3
    assert report.commands_failed == 2, "a killed run is not a passing run"


def test_a_patch_the_parser_refused_counts_against_the_first_try_rate(tmp_path):
    """A rejected envelope never reaches an executor, so it journals no
    TOOL_RESULT -- and the metric that exists to measure exactly that failure
    could not see it."""
    from foundry.cli.report import analyse

    path = _journal(
        tmp_path,
        {"ts": "t", "ordinal": 1, "type": "tool_call",
         "payload": {"name": "apply_patch", "rejected": "unified diff, not anchored"}},
        {"ts": "t", "ordinal": 2, "type": "tool_call", "payload": {"name": "apply_patch"}},
        {"ts": "t", "ordinal": 3, "type": "tool_result",
         "payload": {"tool": "apply_patch", "is_error": False}},
    )
    report = analyse(path)

    assert report.patches_applied == 1
    assert report.patches_rejected == 1


@pytest.mark.skipif(sys.platform != "win32", reason="the CLI is Windows-only")
def test_doctor_fails_on_a_protocol_that_would_kill_every_session(tmp_path):
    """It printed a healthy-looking config line and exited 0 for a config that
    makes build() raise on the first command."""
    import os

    (tmp_path / "config.toml").write_text(
        '[backend]\nprotocol = "anthropic"\n', encoding="utf-8")
    env = {**os.environ, "FOUNDRY_HOME": str(tmp_path), "PYTHONPATH": "src",
           "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run(
        [sys.executable, "-c",
         "import sys; from foundry.cli.app import main; sys.exit(main(['doctor']))"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120)

    assert done.returncode == 1, done.stdout
    assert "anthropic" in done.stdout
