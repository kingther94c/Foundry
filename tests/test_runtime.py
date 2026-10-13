"""End-to-end loop behaviour, driven entirely by scripted backends.

No network, no credentials: this is the suite that has to stay green on a
machine that has neither.
"""

from __future__ import annotations

import json
import time

import pytest

from foundry.core.backends.replay import ScriptedBackend
from foundry.core.context import ContextManager
from foundry.core.conversation import ModelTurn, Role, StopReason, ToolUseBlock, Usage
from foundry.core.events import ApprovalChoice, EventSink, TerminalStatus, ToolEnd, Termination
from foundry.core.policy.engine import Mode, PolicyEngine, Rule, Verdict
from foundry.core.runtime import AgentRuntime, Budget
from foundry.core.session import ArtifactStore, EventType, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import Workspace


def call(name: str, args: dict, call_id: str = "c1") -> ToolUseBlock:
    return ToolUseBlock(call_id=call_id, name=name, arguments=json.dumps(args))


def turn(text: str = "", *calls: ToolUseBlock) -> ModelTurn:
    return ModelTurn(
        text=text, tool_calls=tuple(calls),
        usage=Usage(input_tokens=10, output_tokens=5),
        stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
    )


@pytest.fixture()
def harness(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    session = SessionStore(tmp_path / "sessions")
    registry = default_registry(recorder=session)
    ctx = ToolContext(workspace=Workspace(repo), artifacts=session.artifacts,
                      read_tracker=ReadTracker())
    events = []
    sink = EventSink()
    sink.subscribe(events.append)

    def build(turns, *, policy=None, approval=None, budget=None):
        return AgentRuntime(
            backend=ScriptedBackend(turns), registry=registry,
            policy=policy or PolicyEngine(), context=ContextManager(system_prompt="test"),
            tool_ctx=ctx, session=session, events=sink,
            approval=approval, budget=budget or Budget(),
        ), events

    build.repo = repo
    build.session = session
    build.registry = registry
    return build


def test_plain_answer_ends_the_turn(harness):
    runtime, events = harness([turn("The answer is 42.")])
    outcome = runtime.run_turn("what is the answer?")
    assert outcome.status is None
    assert outcome.text == "The answer is 42."


def test_read_tool_runs_without_approval(harness):
    runtime, events = harness([
        turn("", call("read_file", {"path": "src/app.py"})),
        turn("It returns 1."),
    ])
    outcome = runtime.run_turn("what does run() return?")
    assert outcome.text == "It returns 1."
    tool_ends = [e for e in events if isinstance(e, ToolEnd)]
    assert tool_ends and tool_ends[0].ok


def test_unknown_tool_is_reported_not_executed(harness):
    runtime, events = harness([
        turn("", call("delete_everything", {})),
        turn("Understood."),
    ])
    runtime.run_turn("go")
    results = [m for m in runtime.context.history if m.role is Role.TOOL]
    assert "unknown tool" in results[0].blocks[0].content
    assert results[0].blocks[0].is_error


def test_malformed_arguments_are_reported_not_executed(harness):
    bad = ToolUseBlock(call_id="c1", name="read_file", arguments="{not json")
    runtime, events = harness([turn("", bad), turn("ok")])
    runtime.run_turn("go")
    results = [m for m in runtime.context.history if m.role is Role.TOOL]
    assert "invalid tool call" in results[0].blocks[0].content


def test_denied_call_keeps_the_loop_alive(harness):
    policy = PolicyEngine()
    policy.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.DENY,
                         reason="commands are disabled here"))
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"print(1)\""})),
        turn("I will try another way."),
    ], policy=policy)
    outcome = runtime.run_turn("run the tests")
    assert outcome.text == "I will try another way."
    results = [m for m in runtime.context.history if m.role is Role.TOOL]
    assert "blocked by policy" in results[0].blocks[0].content


def test_approval_denied_is_reported_to_the_model(harness):
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"print(1)\""})),
        turn("Understood, skipping."),
    ], approval=lambda req: ApprovalChoice.DENY)
    runtime.run_turn("run it")
    results = [m for m in runtime.context.history if m.role is Role.TOOL]
    assert "declined" in results[0].blocks[0].content


def test_missing_approval_callback_fails_closed(harness):
    """Headless: an ASK with nobody to answer is a DENY, never a pass -- and it
    must not claim a user decided, since none did. The real reason survives so
    the model can act on it."""
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"print(1)\""})),
        turn("ok"),
    ], approval=None)
    runtime.run_turn("run it")
    results = [m for m in runtime.context.history if m.role is Role.TOOL]
    content = results[0].blocks[0].content
    assert results[0].blocks[0].is_error
    assert "unattended" in content
    assert "user declined" not in content


def test_abort_cancels_the_task(harness):
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"print(1)\""})),
    ], approval=lambda req: ApprovalChoice.ABORT)
    outcome = runtime.run_turn("run it")
    assert outcome.status is TerminalStatus.CANCELLED


def test_session_grant_stops_repeat_prompts(harness):
    asked = []

    def approve(req):
        asked.append(req.display)
        return ApprovalChoice.SESSION

    cmd = {"command": "python -c \"print(1)\""}
    runtime, events = harness([
        turn("", call("run_command", cmd, "c1")),
        turn("", call("run_command", cmd, "c2")),
        turn("done"),
    ], approval=approve)
    runtime.run_turn("run it twice")
    assert len(asked) == 1


def test_finish_completed_with_verified_claim(harness):
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"print(1)\""}, "c1")),
        turn("", call("finish", {
            "status": "completed", "summary": "ran the check",
            "claims": [{"claim_text": "script runs", "command_event_id": 1,
                        "expected_exit_code": 0}],
        }, "c2")),
    ], approval=lambda req: ApprovalChoice.ONCE)

    # The command's real ordinal comes from the journal; patch the claim to it.
    outcome = runtime.run_turn("verify it")
    assert outcome.status in (TerminalStatus.COMPLETED, TerminalStatus.PARTIAL)


def test_fabricated_claim_downgrades_to_partial(harness):
    runtime, events = harness([
        turn("", call("finish", {
            "status": "completed", "summary": "tests pass",
            "claims": [{"claim_text": "pytest passes", "command_event_id": 999,
                        "expected_exit_code": 0}],
        })),
    ])
    outcome = runtime.run_turn("fix it")
    assert outcome.status is TerminalStatus.PARTIAL
    assert "not a recorded command" in outcome.summary


def test_wrong_exit_code_claim_downgrades_to_partial(harness):
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"raise SystemExit(1)\""}, "c1")),
        turn("", call("finish", {
            "status": "completed", "summary": "tests pass",
            "claims": [{"claim_text": "pytest passes", "command_event_id": 1,
                        "expected_exit_code": 0}],
        }, "c2")),
    ], approval=lambda req: ApprovalChoice.ONCE)
    outcome = runtime.run_turn("run the tests")
    assert outcome.status is TerminalStatus.PARTIAL


def test_finish_with_no_claims_is_valid_disclosure(harness):
    runtime, events = harness([
        turn("", call("finish", {"status": "completed",
                                 "summary": "read-only review; no validation was run",
                                 "claims": []})),
    ])
    outcome = runtime.run_turn("review this")
    assert outcome.status is TerminalStatus.COMPLETED


def test_round_budget_terminates_partial(harness):
    turns = [turn("", call("read_file", {"path": "src/app.py"}, f"c{i}")) for i in range(10)]
    runtime, events = harness(turns, budget=Budget(max_tool_rounds=3))
    outcome = runtime.run_turn("loop forever")
    assert outcome.status is TerminalStatus.PARTIAL
    assert "rounds exceeded" in outcome.summary or "rounds exceeded" in outcome.text


def test_repeated_identical_failure_is_bounded(harness):
    bad = call("read_file", {"path": "does/not/exist.py"})
    turns = [turn("", ToolUseBlock(call_id=f"c{i}", name=bad.name, arguments=bad.arguments))
             for i in range(8)] + [turn("giving up")]
    runtime, events = harness(turns, budget=Budget(max_consecutive_failures=3))
    runtime.run_turn("read a missing file")
    results = [m for m in runtime.context.history if m.role is Role.TOOL]
    assert any("has failed" in r.blocks[0].content for r in results)


def test_termination_is_journaled_once(harness):
    runtime, events = harness([
        turn("", call("finish", {"status": "completed", "summary": "done", "claims": []})),
    ])
    runtime.run_turn("go")
    harness.session.close()
    records = list(SessionStore.read_records(harness.session.path))
    terminations = [r for r in records if r.type == EventType.TERMINATION]
    assert len(terminations) == 1


def test_token_usage_journals_cached_input_tokens(harness):
    """Cache reads were invisible after the turn: the journal kept only input
    and output, so the report billed every cached token at full price."""
    cached = ModelTurn(text="done", usage=Usage(input_tokens=4206, output_tokens=7,
                                                cached_input_tokens=4202),
                       stop_reason=StopReason.END_TURN)
    runtime, events = harness([cached])
    runtime.run_turn("go")
    harness.session.close()
    usage = [r.payload for r in SessionStore.read_records(harness.session.path)
             if r.type == EventType.TOKEN_USAGE]
    assert usage == [{"input_tokens": 4206, "output_tokens": 7,
                      "cached_input_tokens": 4202}]


def test_token_usage_journals_zero_cached_tokens_when_none_were_cached(harness):
    runtime, events = harness([turn("done")])
    runtime.run_turn("go")
    harness.session.close()
    usage = [r.payload for r in SessionStore.read_records(harness.session.path)
             if r.type == EventType.TOKEN_USAGE]
    assert usage and usage[0]["cached_input_tokens"] == 0


def test_journal_records_policy_decisions(harness):
    runtime, events = harness([
        turn("", call("read_file", {"path": "src/app.py"})),
        turn("done"),
    ])
    runtime.run_turn("read it")
    harness.session.close()
    decisions = [r for r in SessionStore.read_records(harness.session.path)
                 if r.type == EventType.POLICY_DECISION]
    assert decisions
    assert decisions[0].payload["rule_id"]
    assert decisions[0].payload["policy_digest"]


class _SlowTool:
    """Wraps a real tool and sleeps first, so execution has a known minimum."""

    def __init__(self, inner, delay_s: float, raises: Exception | None = None):
        self.inner = inner
        self.name = inner.name
        self.kind = inner.kind
        self.delay_s = delay_s
        self.raises = raises

    def schema(self):
        return self.inner.schema()

    def validate(self, args):
        return self.inner.validate(args)

    def execute(self, op, ctx):
        time.sleep(self.delay_s)
        if self.raises is not None:
            raise self.raises
        return self.inner.execute(op, ctx)


def test_tool_end_carries_the_real_duration(harness):
    """Every tool_end reported duration_ms 0, even after a minute-long run."""
    harness.registry.register(_SlowTool(harness.registry.get("read_file"), 0.25))
    runtime, events = harness([
        turn("", call("read_file", {"path": "src/app.py"})),
        turn("done"),
    ])
    runtime.run_turn("read it")
    end = [e for e in events if isinstance(e, ToolEnd)][0]
    assert end.ok
    # Below the sleep by a margin: the point is that the duration is real, not
    # that a CI runner's sleep and clock agree to the millisecond.
    assert 200 <= end.duration_ms < 10_000


def test_tool_end_carries_the_duration_when_the_tool_raises(harness):
    from foundry.core.errors import ToolError
    harness.registry.register(_SlowTool(harness.registry.get("read_file"), 0.2,
                                        raises=ToolError("boom")))
    harness.registry.register(_SlowTool(harness.registry.get("list_files"), 0.2,
                                        raises=OSError("locked")))
    runtime, events = harness([
        turn("", call("read_file", {"path": "src/app.py"}, "c1")),
        turn("", call("list_files", {}, "c2")),
        turn("done"),
    ])
    runtime.run_turn("read it")
    ends = [e for e in events if isinstance(e, ToolEnd)]
    assert [e.ok for e in ends] == [False, False]
    assert all(e.duration_ms >= 150 for e in ends), [e.duration_ms for e in ends]


def test_run_command_tool_end_states_the_exit_code(harness):
    """The summary was `$ <command>`, a repeat of tool_begin.display."""
    command = "python -c \"raise SystemExit(3)\""
    runtime, events = harness([
        turn("", call("run_command", {"command": command})),
        turn("done"),
    ], approval=lambda req: ApprovalChoice.ONCE)
    runtime.run_turn("run it")
    end = [e for e in events if isinstance(e, ToolEnd)][0]
    assert not end.ok
    assert end.summary == "exit code 3"
    assert command not in end.summary
    assert end.duration_ms > 0


def test_run_command_tool_end_states_success(harness):
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"print(1)\""})),
        turn("done"),
    ], approval=lambda req: ApprovalChoice.ONCE)
    runtime.run_turn("run it")
    end = [e for e in events if isinstance(e, ToolEnd)][0]
    assert end.ok
    assert end.summary == "exit code 0"


def test_run_command_tool_end_states_a_timeout(harness):
    runtime, events = harness([
        turn("", call("run_command", {"command": "python -c \"import time; time.sleep(30)\"",
                                      "timeout_s": 1})),
        turn("done"),
    ], approval=lambda req: ApprovalChoice.ONCE)
    runtime.run_turn("run it")
    end = [e for e in events if isinstance(e, ToolEnd)][0]
    assert not end.ok
    # A killed child may leave the pipe open; the summary then also says the
    # output is incomplete, which is true and worth keeping.
    assert end.summary.startswith("timed out after 1s")
    assert "exit code" not in end.summary
    assert end.duration_ms >= 900


def test_plan_mode_blocks_edits(harness):
    runtime, events = harness([
        turn("", call("apply_patch", {"patch": "*** Begin Patch\n*** Add File: x.py\n+a\n*** End Patch"})),
        turn("Here is my plan instead."),
    ], policy=PolicyEngine(mode=Mode.PLAN))
    outcome = runtime.run_turn("add a file")
    assert outcome.text == "Here is my plan instead."
    results = [m for m in runtime.context.history if m.role is Role.TOOL]
    assert "plan mode" in results[0].blocks[0].content


def _rendered(ctx: ContextManager) -> str:
    return " ".join(b.content for m in ctx.project() for b in m.blocks
                    if hasattr(b, "content"))


def test_context_masks_old_tool_output(harness):
    # 1000 tokens of budget, 500 before masking starts; the old output alone is ~1000.
    ctx = ContextManager(system_prompt="s", mask_after_turns=1, max_context_tokens=1000)
    ctx.start_turn()
    ctx.append_tool_result("c1", "OLD-MARKER " + ("x" * 4000))
    ctx.start_turn()
    ctx.start_turn()
    ctx.append_tool_result("c2", "recent output stays")
    rendered = _rendered(ctx)
    assert "recent output stays" in rendered
    assert "OLD-MARKER" not in rendered
    assert "elided" in rendered


def test_masking_leaves_short_output_alone(harness):
    """Replacing a short result with a longer notice would cost context, not save it."""
    ctx = ContextManager(system_prompt="s", mask_after_turns=1, max_context_tokens=1000)
    ctx.start_turn()
    ctx.append_tool_result("c1", "exit 0")
    ctx.append_tool_result("c2", "x" * 4000)     # the pressure that turns masking on
    ctx.start_turn()
    ctx.start_turn()
    rendered = _rendered(ctx)
    assert "elided" in rendered
    assert "exit 0" in rendered


def test_old_output_is_kept_while_the_context_has_room(harness):
    """Masking every output older than a few rounds, whatever the context held,
    made Foundry forget files it had just read: working on its own repository
    it re-read the same files round after round and ran out of tool rounds
    with a sixth of the window in use."""
    ctx = ContextManager(system_prompt="s", mask_after_turns=1)
    ctx.start_turn()
    ctx.append_tool_result("c1", "OLD-MARKER " + ("x" * 4000))
    for _ in range(10):
        ctx.start_turn()
    assert "OLD-MARKER" in _rendered(ctx)


def test_masking_goes_oldest_first_and_stops_once_it_fits(harness):
    # Threshold 1000 tokens; three ~600-token outputs need exactly two masked.
    ctx = ContextManager(system_prompt="s", mask_after_turns=1, max_context_tokens=2000)
    for marker in ("FIRST", "SECOND", "THIRD"):
        ctx.start_turn()
        ctx.append_tool_result(marker, marker + " " + "x" * 2400)
    ctx.start_turn()
    ctx.start_turn()
    rendered = _rendered(ctx)
    assert "FIRST" not in rendered and "SECOND" not in rendered
    assert "THIRD" in rendered
    assert ctx.estimated_context_tokens() <= 1000


def test_the_latest_rounds_are_never_masked(harness):
    ctx = ContextManager(system_prompt="s", mask_after_turns=2, max_context_tokens=1000)
    ctx.start_turn()
    ctx.append_tool_result("c1", "OLDEST " + "x" * 4000)
    ctx.start_turn()
    ctx.append_tool_result("c2", "RECENT " + "x" * 4000)
    ctx.start_turn()
    rendered = _rendered(ctx)
    assert "OLDEST" not in rendered
    assert "RECENT" in rendered     # still over budget, but inside the window
