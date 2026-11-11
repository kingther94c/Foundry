"""Session resume (D-035): a session continued from its journal alone.

Two properties carry the feature. The rebuilt conversation is the one the
model was having -- byte for byte, masking included -- and nothing about the
resume weakens what a fresh session guarantees: claims need evidence from this
run, files changed outside the session are guarded, secrets stay scrubbed, and
a moved HEAD is refused.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.repl import repl, with_note
from foundry.cli.wiring import _announce_found, _close, build
from foundry.core.backends.anthropic import build_body
from foundry.core.backends.replay import ScriptedBackend
from foundry.core.context import MASK_NOTICE, ContextManager
from foundry.core.conversation import (
    Message,
    ModelTurn,
    OpaqueBlock,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    TurnRequest,
    Usage,
)
from foundry.core.errors import ResumeError, SessionLocked, TransientError
from foundry.core.events import ApprovalChoice, EventSink, Notice, TerminalStatus
from foundry.core.policy.engine import Mode, PolicyEngine, Rule, Verdict
from foundry.core.redaction import Redactor
from foundry.core.resume import (
    CONTINUE_PROMPT,
    decode_block,
    read_journal,
    resolve_session,
)
from foundry.core.runtime import AgentRuntime, Budget
from foundry.core.session import EventType, SessionStore, is_locked
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.git import GitBaseline, run_git
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import Workspace

THINKING = OpaqueBlock(provider="anthropic", data=json.dumps(
    [{"type": "thinking", "thinking": "considering", "signature": "c2ln"}]))


def call(cid: str, name: str, args: dict) -> ToolUseBlock:
    return ToolUseBlock(call_id=cid, name=name, arguments=json.dumps(args))


def turn(*calls: ToolUseBlock, text: str = "", opaque: OpaqueBlock | None = None) -> ModelTurn:
    return ModelTurn(text=text, tool_calls=tuple(calls), usage=Usage(10, 5),
                     stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
                     opaque=opaque)


def finish(cid: str = "f1", status: str = "completed", claims: list | None = None) -> ModelTurn:
    return turn(call(cid, "finish", {"status": status, "summary": "done",
                                     "claims": claims or []}))


class _Live(ScriptedBackend):
    """Turns may be callables, built when the request arrives -- the way a
    model reads an event id back before citing it."""

    def stream_turn(self, request):
        index = len(self.seen)
        if index < len(self.turns) and callable(self.turns[index]):
            self.turns[index] = self.turns[index]()
        return ScriptedBackend.stream_turn(self, request)


# --- a runtime without the CLI ----------------------------------------------


def _core(tmp_path, turns, *, redactor=None, budget=None, max_context=128_000, reserve=8_000):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    session = SessionStore(tmp_path / "sessions", redactor=redactor)
    session.write_header(workspace=str(repo), profile="p", model="m", foundry_version="0")
    session.append(EventType.GIT_BASELINE, GitBaseline(head="a" * 40, branch="main").as_payload())
    registry = default_registry(recorder=session)
    policy = PolicyEngine(mode=Mode.ACCEPT_EDITS)
    policy.add_rule(Rule(tool="run_command", pattern="*", verdict=Verdict.ALLOW))
    extra = {"redactor": redactor} if redactor is not None else {}
    context = ContextManager(system_prompt="t", max_context_tokens=max_context,
                             reserve_output_tokens=reserve, **extra)
    runtime = AgentRuntime(
        backend=ScriptedBackend(list(turns)), registry=registry, policy=policy, context=context,
        tool_ctx=ToolContext(workspace=Workspace(repo), artifacts=session.artifacts,
                             read_tracker=ReadTracker()),
        session=session, events=EventSink(), budget=budget or Budget(),
    )
    return runtime, session, repo


def _restored(state, *, max_context=128_000, reserve=8_000, redactor=None) -> ContextManager:
    extra = {"redactor": redactor} if redactor is not None else {}
    context = ContextManager(system_prompt="t", max_context_tokens=max_context,
                             reserve_output_tokens=reserve, **extra)
    context.restore(list(state.history), list(state.turns), state.turn, state.usage,
                    sent=state.last_sent)
    return context


def _pairs_every_call(messages) -> None:
    """What both providers require: each tool call answered before any user text."""
    pending: set[str] = set()
    for message in messages:
        if message.role is Role.ASSISTANT:
            assert not pending, f"unanswered calls {pending}"
            pending = {b.call_id for b in message.blocks if isinstance(b, ToolUseBlock)}
        elif message.role is Role.TOOL:
            for block in message.blocks:
                pending.discard(block.call_id)
        else:
            assert not pending, f"user text before the results of {pending}"
    assert not pending, f"unanswered calls {pending}"


# --- through build(), as the CLI runs it -------------------------------------


@pytest.fixture()
def repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "a.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "b.py").write_text("B = 1\n", encoding="utf-8")
    run_git(["init"], repo)
    run_git(["config", "user.email", "t@example.com"], repo)
    run_git(["config", "user.name", "T"], repo)
    run_git(["add", "."], repo)
    run_git(["commit", "-m", "initial"], repo)
    return repo


OVERRIDES = {"base_url": "http://127.0.0.1:9/v1", "model": "fake", "stream": False}


def _wire(repo, home, turns, *, resume=None, overrides=None, approve=ApprovalChoice.ONCE):
    wiring = build(repo, home=home, overrides={**OVERRIDES, **(overrides or {})},
                   console=Console(file=io.StringIO(), width=100), resume=resume)
    wiring.runtime.backend = _Live(list(turns))
    wiring.runtime.approval = (lambda request: approve) if approve else None
    return wiring


PATCH_A = ("*** Begin Patch\n*** Update File: a.py\n"
           "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n*** End Patch\n")
PATCH_B = ("*** Begin Patch\n*** Update File: b.py\n"
           "<<<<<<< SEARCH\nB = 5\n=======\nB = 6\n>>>>>>> REPLACE\n*** End Patch\n")


# --- decoding ------------------------------------------------------------------


@pytest.mark.parametrize("block", [
    TextBlock("hello"),
    ToolUseBlock(call_id="c1", name="read_file", arguments='{"path": "x"}'),
    ToolResultBlock(call_id="c1", content="out", is_error=True),
    THINKING,
])
def test_every_recorded_block_decodes_to_itself(block):
    assert decode_block(AgentRuntime._block_payload(block)) == block


def test_the_rebuilt_conversation_is_the_one_the_model_was_having(tmp_path):
    """requirements.md: the journal rebuilds every model call byte for byte.
    Masked outputs, provider blocks and round numbers included, or the next
    request differs from the one an uninterrupted session would send."""
    reads = [turn(call(f"r{i}", "read_file", {"path": f"f{i}.txt"}), opaque=THINKING)
             for i in range(9)]
    script = [*reads, turn(text="read them all", opaque=THINKING),
              turn(call("r9", "read_file", {"path": "f0.txt"}), opaque=THINKING),
              turn(text="again", opaque=THINKING)]  # the last reply's block is the one at risk
    runtime, session, repo = _core(tmp_path, script, max_context=8_000, reserve=0)
    for i in range(9):
        (repo / f"f{i}.txt").write_text("".join(f"line {i}.{n} " + "x" * 50 + "\n"
                                                for n in range(40)), encoding="utf-8")

    assert runtime.run_turn("read the files").status is None
    assert runtime.run_turn("read the first one again").status is None
    session.close()
    live = runtime.context
    assert any(isinstance(b, ToolResultBlock) and b.content == MASK_NOTICE
               for m in live.project() for b in m.blocks), "masking never happened"

    state = read_journal(session.path)
    restored = _restored(state, max_context=8_000, reserve=0)

    assert state.repairs == () and not state.legacy
    assert restored.history == live.history
    assert restored._turn_of_message == live._turn_of_message
    assert restored._turn == live._turn
    assert restored.project() == live.project()
    assert restored.session_usage == live.session_usage
    types = {r.type for r in SessionStore.read_records(session.path)}
    assert {EventType.USER_INPUT, EventType.TOOL_REPLY} <= types


def test_the_next_request_is_the_one_an_uninterrupted_session_sends(repo, tmp_path):
    first = [turn(call("c1", "read_file", {"path": "a.py"})), turn(text="A is 1")]

    uninterrupted = _wire(repo, tmp_path / "home-a", [*first, turn(text="B is 1")])
    uninterrupted.runtime.run_turn("what is A?")
    uninterrupted.runtime.run_turn("and B?")
    _close(uninterrupted, TerminalStatus.CANCELLED, "session ended")

    before = _wire(repo, tmp_path / "home-b", first)
    before.runtime.run_turn("what is A?")
    _close(before, TerminalStatus.CANCELLED, "session ended")
    after = _wire(repo, tmp_path / "home-b", [turn(text="B is 1")], resume="last")
    after.runtime.run_turn(with_note(after, "and B?"))
    _close(after, TerminalStatus.CANCELLED, "session ended")

    expected = uninterrupted.runtime.backend.seen[-1].messages
    sent = after.runtime.backend.seen[0].messages
    assert sent[:-1] == expected[:-1]
    assert expected[-1] == Message.text(Role.USER, "and B?")
    note = sent[-1].blocks[0].text
    assert note.endswith("\n\nand B?")
    assert "its status was cancelled (session ended)" in note
    assert "Commands run before this resume cannot be cited" in note
    assert after.resumed.repairs == ()


def test_rounds_survive_a_retried_request(tmp_path):
    """A refreshed credential sends the same request twice in one round."""
    from foundry.core.errors import AuthError

    class _ExpiresOnce(ScriptedBackend):
        api_key = "old"
        failed = False

        def stream_turn(self, request):
            if not type(self).failed:
                type(self).failed = True
                raise AuthError("token expired")
            return ScriptedBackend.stream_turn(self, request)

    class _Fresh:
        def acquire(self):
            class _Handle:
                @staticmethod
                def reveal():
                    return "new"
            return _Handle()

    runtime, session, _ = _core(tmp_path, [])
    runtime.backend = _ExpiresOnce([turn(text="one"), turn(text="two")])
    runtime.credentials = _Fresh()
    runtime.run_turn("first")
    runtime.run_turn("second")
    session.close()

    requests = [r for r in SessionStore.read_records(session.path)
                if r.type == EventType.MODEL_REQUEST]
    assert [r.payload["turn"] for r in requests] == [1, 1, 2]
    state = read_journal(session.path)
    assert restored_equals(state, runtime.context)


def restored_equals(state, live) -> bool:
    restored = _restored(state)
    return (restored.history == live.history
            and restored._turn_of_message == live._turn_of_message
            and restored._turn == live._turn)


# --- calls the session never answered ------------------------------------------


def test_an_interrupted_call_and_the_ones_after_it_are_answered(tmp_path):
    script = [turn(call("c1", "read_file", {"path": "x.txt"}),
                   call("c2", "read_file", {"path": "y.txt"}),
                   call("c3", "read_file", {"path": "z.txt"}))]
    runtime, session, repo = _core(tmp_path, script)
    for name in ("x.txt", "y.txt", "z.txt"):
        (repo / name).write_text("data\n", encoding="utf-8")
    tool = runtime.registry.tools["read_file"]
    original = type(tool).execute
    calls = []

    def execute(self, op, ctx):
        calls.append(op.target)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return original(self, op, ctx)

    type(tool).execute = execute
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.run_turn("read three")
    finally:
        type(tool).execute = original
    session.close()

    state = read_journal(session.path)
    assert state.status is TerminalStatus.INTERRUPTED
    assert [(r.call_id, r.outcome) for r in state.repairs] == [("c2", "unknown"),
                                                               ("c3", "not_run")]
    results = [b for m in state.history if m.role is Role.TOOL for b in m.blocks]
    assert [b.call_id for b in results] == ["c1", "c2", "c3"]
    assert "data" in results[0].content and not results[0].is_error
    assert results[1].is_error and "whether it ran" in results[1].content
    assert results[2].is_error and "it was interrupted" in results[2].content

    messages = (Message.text(Role.SYSTEM, "t"), *state.history,
                Message.text(Role.USER, "carry on"))
    _pairs_every_call(messages)
    body = build_body(TurnRequest(messages=messages, tools=(), model="m"), stream=False)
    ids = [b["id"] for m in body["messages"] for b in m["content"]
           if isinstance(m["content"], list) and b.get("type") == "tool_use"]
    answered = [b["tool_use_id"] for m in body["messages"] for b in m["content"]
                if isinstance(m["content"], list) and b.get("type") == "tool_result"]
    assert ids == answered == ["c1", "c2", "c3"]


def test_calls_a_budget_skipped_say_why(tmp_path):
    script = [turn(call("c1", "read_file", {"path": "x.txt"}),
                   call("c2", "read_file", {"path": "x.txt"}))]
    runtime, session, repo = _core(tmp_path, script, budget=Budget(max_tool_calls=1))
    (repo / "x.txt").write_text("data\n", encoding="utf-8")
    assert runtime.run_turn("read twice").status is TerminalStatus.PARTIAL
    session.close()

    state = read_journal(session.path)
    assert [(r.call_id, r.outcome) for r in state.repairs] == [("c2", "not_run")]
    last = state.history[-1].blocks[0]
    assert "tool calls exceeded (1)" in last.content and "Nothing was changed" in last.content


# --- evidence starts over ---------------------------------------------------------


def test_after_finish_a_resumed_session_needs_evidence_of_its_own(repo, tmp_path):
    home = tmp_path / "home"

    def cite_latest(wiring):
        return lambda: finish("f9", claims=[{
            "claim_text": "python runs",
            "command_event_id": wiring.runtime.registry.tools["run_command"].history[-1][
                "event_ordinal"]}])

    first = _wire(repo, home, [])
    first.runtime.backend = _Live([turn(call("c1", "run_command",
                                             {"command": "python --version"})),
                                   cite_latest(first)])
    assert first.runtime.run_turn("check python").status is TerminalStatus.COMPLETED
    old = first.runtime.registry.tools["run_command"].history[-1]["event_ordinal"]
    _close(first, TerminalStatus.CANCELLED, "session ended")

    second = _wire(repo, home, [finish("f2", claims=[{"claim_text": "python runs",
                                                      "command_event_id": old}])],
                   resume="last")
    accepted = second.runtime.context.history[-1].blocks[0]
    assert accepted.call_id == "f9" and not accepted.is_error
    assert accepted.content == "finish was accepted: the session ended with status completed."
    outcome = second.runtime.run_turn(with_note(second, "is python there?"))
    _close(second, TerminalStatus.CANCELLED, "session ended")
    assert outcome.status is TerminalStatus.PARTIAL
    assert "not a recorded command" in outcome.text

    third = _wire(repo, home, [], resume="last")
    third.runtime.backend = _Live([turn(call("c3", "run_command",
                                             {"command": "python --version"})),
                                   cite_latest(third)])
    outcome = third.runtime.run_turn(with_note(third, "check it properly"))
    _close(third, TerminalStatus.CANCELLED, "session ended")
    assert outcome.status is TerminalStatus.COMPLETED, outcome.text

    ordinals = [r.ordinal for r in SessionStore.read_records(third.session.path)]
    assert ordinals == list(range(1, len(ordinals) + 1))
    assert SessionStore.terminal_status(third.session.path) is TerminalStatus.COMPLETED


def test_files_changed_while_stopped_are_guarded(repo, tmp_path):
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                               turn(call("c2", "apply_patch", {"patch": PATCH_A})),
                               turn(text="patched")])
    first.runtime.run_turn("change A")
    _close(first, TerminalStatus.CANCELLED, "session ended")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"

    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")  # the user, between runs
    second = _wire(repo, home, [], resume="last")
    _close(second, TerminalStatus.CANCELLED, "session ended")

    assert second.outside == frozenset({"b.py"})
    assert "patching them asks first: b.py." in second.note
    # Not "before this session": b.py was written while it was stopped (D-177).
    system = second.runtime.context.system_prompt
    assert ("These files have uncommitted changes this session did not make -- from before "
            "it started, or made outside it since -- and need explicit approval to edit: "
            "b.py") in system
    assert "before this session" not in system
    patch = second.runtime.registry.tools["apply_patch"]
    decision, _ = second.runtime.policy.evaluate(patch.validate({"patch": PATCH_B}))
    assert decision.rule_id == "builtin.dirty_file"
    assert "this session did not make" in decision.reason
    decision, _ = second.runtime.policy.evaluate(patch.validate({"patch": PATCH_A}))
    assert decision.rule_id != "builtin.dirty_file"
    # Read again before patching: what was read before the resume may be stale.
    assert not second.runtime.tool_ctx.read_tracker.has_read(
        str((repo / "a.py").resolve())) and not second.runtime.tool_ctx.read_tracker._read


def test_what_changed_while_stopped_is_not_claimed(repo, tmp_path):
    # b.py, written by the user between runs, was listed as changed by the
    # session when the resumed session finished (D-177).
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(text="hello")])
    first.runtime.run_turn("hi")
    _close(first, TerminalStatus.CANCELLED, "session ended")
    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")  # the user, between runs
    second = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(call("c2", "apply_patch", {"patch": PATCH_A})),
                                finish("f2")], resume="last")
    outcome = second.runtime.run_turn("change A")
    _close(second, TerminalStatus.CANCELLED, "session ended")
    assert outcome.summary.endswith(
        "Files changed by this session:\n  - a.py\n"
        "Changed outside this session since it started (not claimed):\n  - b.py")
    [final] = [r.payload for r in SessionStore.read_records(second.session.path)
               if r.type == EventType.GIT_BASELINE and r.payload.get("phase") == "final"]
    assert final["session_changed"] == ["a.py"] and final["outside_changed"] == ["b.py"]


def test_slash_diff_and_status_say_whose_a_change_is_after_a_resume(repo, tmp_path):
    from test_slash_commands import _repl, _shown

    home = tmp_path / "home"
    first = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                               turn(call("c2", "apply_patch", {"patch": PATCH_A})),
                               turn(text="patched")])
    first.runtime.run_turn("change A")
    _close(first, TerminalStatus.CANCELLED, "session ended")
    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")  # the user, between runs
    shown = _shown(_repl(_wire(repo, home, [], resume="last"), "/diff", "/status"))
    assert "a.py [dim](this session)" in shown
    assert "b.py [dim](outside this session)" in shown
    assert "1 file(s) since the session started, 0 already changed before it, 1 changed " \
           "outside it since" in shown


def test_accept_edits_mode_says_whose_changes_still_ask():
    from foundry.core.prompts import mode_line

    said = mode_line(Mode.ACCEPT_EDITS)
    assert "files with uncommitted changes this session did not make" in said
    assert "when this session started" not in said


def test_the_evidence_tells_the_three_apart():
    from foundry.core.tools.git import GitEvidence

    baseline = GitBaseline(head="h", branch="main", dirty_paths=frozenset({"old.py"}),
                           outside=frozenset({"theirs.py", "old.py"}))
    evidence = GitEvidence(baseline=baseline, head_now="h",
                           changed=frozenset({"mine.py", "theirs.py", "old.py"}))
    assert evidence.session_changed == {"mine.py"}
    assert evidence.outside_changed == {"theirs.py"}
    assert evidence.preexisting_changed == {"old.py"}


def test_a_moved_head_is_refused_and_nothing_is_written(repo, tmp_path):
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(text="hello")])
    first.runtime.run_turn("hi")
    _close(first, TerminalStatus.CANCELLED, "session ended")
    before = first.session.path.read_bytes()
    start = first.runtime.git_baseline.head

    (repo / "c.py").write_text("C = 1\n", encoding="utf-8")
    run_git(["add", "."], repo)
    run_git(["commit", "-m", "elsewhere"], repo)
    _, head, _ = run_git(["rev-parse", "HEAD"], repo)

    with pytest.raises(ResumeError) as refused:
        _wire(repo, home, [], resume="last")
    assert start[:12] in str(refused.value) and head.strip()[:12] in str(refused.value)
    assert first.session.path.read_bytes() == before
    assert not is_locked(first.session.dir)


def test_a_secret_stays_scrubbed(tmp_path):
    secret = "s3cr3t-token-0123456789"
    redactor = Redactor()
    redactor.register(secret)
    runtime, session, repo = _core(tmp_path, [turn(call("c1", "read_file", {"path": "k.txt"})),
                                              turn(text="found it")], redactor=redactor)
    (repo / "k.txt").write_text(f"key={secret}\n", encoding="utf-8")
    runtime.run_turn(f"is {secret} in k.txt?")
    session.close()

    state = read_journal(session.path)
    restored = _restored(state, redactor=redactor)
    backend = ScriptedBackend([turn(text="ok")])
    runtime.backend, runtime.context = backend, restored
    runtime.run_turn("thanks")
    sent = json.dumps([AgentRuntime._block_payload(b) for m in backend.seen[0].messages
                       for b in m.blocks])
    assert secret not in sent
    assert secret not in repr(state.history)


# --- journals written before resume existed ------------------------------------


def _legacy(path: Path, out: Path) -> Path:
    """The journal as an older Foundry would have written it."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["type"] in (EventType.USER_INPUT, EventType.TOOL_REPLY):
            continue
        record["payload"].pop("turn", None)
        record["payload"].pop("opaque", None)
        record["payload"].pop("paths", None)
        lines.append(json.dumps(record))
    out.mkdir(parents=True)
    (out / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out / "events.jsonl"


def test_an_older_journal_still_resumes(tmp_path):
    script = [turn(call("c1", "read_file", {"path": "x.txt"})),
              turn(call("c2", "read_file", {"path": "x.txt"}), call("f1", "finish", {
                  "status": "completed", "summary": "read", "claims": []}))]
    runtime, session, repo = _core(tmp_path, script)
    (repo / "x.txt").write_text("data\n", encoding="utf-8")
    assert runtime.run_turn("read it").status is TerminalStatus.COMPLETED
    session.close()

    current = read_journal(session.path)
    older = read_journal(_legacy(session.path, tmp_path / "old"))

    assert older.legacy and not current.legacy
    assert [(r.call_id, r.outcome) for r in current.repairs] == [("f1", "finish_accepted")]
    assert [(r.call_id, r.outcome) for r in older.repairs] == [("c2", "output_lost"),
                                                              ("f1", "finish_accepted")]
    assert older.turns == current.turns and older.turn == current.turn
    assert older.history[:-2] == current.history[:-2]
    assert "output was not kept" in older.history[-2].blocks[0].content
    _pairs_every_call(older.history)


# --- the journal file itself ------------------------------------------------------


def test_ordinals_continue_and_artifacts_resolve_after_a_reopen(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    store.append(EventType.NOTICE, {"text": "one"})
    artifact = store.artifacts.put_text("kept output").artifact_id
    store.close()
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "x", "ordinal": 2, "type": "no')  # killed mid-write

    reopened = SessionStore(tmp_path / "sessions", session_id=store.session_id, resume=True)
    assert reopened.append(EventType.NOTICE, {"text": "two"}) == 2
    assert reopened.artifacts.read_text(artifact) == "kept output"
    reopened.close()
    records = list(SessionStore.read_records(store.path))
    assert [(r.ordinal, r.payload.get("text")) for r in records] == [(1, "one"), (2, "two")]


def test_a_refused_resume_leaves_a_half_written_line_alone(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    store.append(EventType.NOTICE, {"text": "one"})
    store.close()
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write('{"half')
    before = store.path.read_bytes()
    SessionStore(tmp_path / "sessions", session_id=store.session_id, resume=True).close()
    assert store.path.read_bytes() == before


def test_one_writer_per_session(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    assert is_locked(store.dir)
    with pytest.raises(SessionLocked):
        SessionStore(tmp_path / "sessions", session_id=store.session_id, resume=True)
    store.close()
    assert not is_locked(store.dir)
    SessionStore(tmp_path / "sessions", session_id=store.session_id, resume=True).close()


def test_a_resumed_run_that_crashed_reads_as_interrupted(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    store.record_termination(TerminalStatus.COMPLETED, "reported by the agent")
    store.close()
    assert SessionStore.terminal_status(store.path) is TerminalStatus.COMPLETED

    reopened = SessionStore(tmp_path / "sessions", session_id=store.session_id, resume=True)
    reopened.append(EventType.RESUMED, {})
    reopened.close()
    assert SessionStore.terminal_status(store.path) is TerminalStatus.INTERRUPTED


def test_a_session_that_goes_on_after_a_blocked_turn_records_how_it_ended(repo, tmp_path):
    class _DropsOnce(_Live):
        def stream_turn(self, request):
            if not self.seen:
                self.seen.append(request)
                raise TransientError("connection reset")
            return _Live.stream_turn(self, request)

    class _Typist:
        def __init__(self, lines):
            self.lines = list(lines)

        def input(self, prompt=""):
            return self.lines.pop(0)

        def print(self, *args, **kwargs):
            pass

    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = _DropsOnce([None, turn(text="here now")])
    status = repl(wiring, _Typist(["hello?", "hello again", "/exit"]))
    _close(wiring, status, "session ended")

    endings = [r.payload["status"] for r in SessionStore.read_records(wiring.session.path)
               if r.type == EventType.TERMINATION]
    assert endings == ["blocked", "cancelled"]
    assert SessionStore.terminal_status(wiring.session.path) is TerminalStatus.CANCELLED


# --- refusals ------------------------------------------------------------------------


def _one_session(repo, home):
    wiring = _wire(repo, home, [turn(text="hello")])
    wiring.runtime.run_turn("hi")
    _close(wiring, TerminalStatus.CANCELLED, "session ended")
    return wiring.session


def test_a_full_context_is_refused(repo, tmp_path, monkeypatch):
    session = _one_session(repo, tmp_path / "home")
    before = session.path.read_bytes()
    monkeypatch.setattr(ContextManager, "context_exhausted", lambda self: True)
    with pytest.raises(ResumeError, match="context is full"):
        _wire(repo, tmp_path / "home", [], resume=session.session_id)
    assert session.path.read_bytes() == before
    assert not is_locked(session.dir)


def test_another_workspace_is_refused(repo, tmp_path):
    session = _one_session(repo, tmp_path / "home")
    other = tmp_path / "other"
    other.mkdir()
    run_git(["init"], other)
    with pytest.raises(ResumeError, match="worked in"):
        _wire(other, tmp_path / "home", [], resume=session.session_id)
    with pytest.raises(ResumeError, match="no recorded session"):
        _wire(other, tmp_path / "home", [], resume="last")


def test_a_session_id_is_a_name_not_a_path(repo, tmp_path):
    session = _one_session(repo, tmp_path / "home")
    sessions = session.dir.parent
    (sessions.parent / "elsewhere").mkdir()
    (sessions.parent / "elsewhere" / "events.jsonl").write_text("{}\n", encoding="utf-8")
    for name in ("../elsewhere", "..", ".", f"{session.session_id}/.."):
        with pytest.raises(ResumeError, match="no such session"):
            resolve_session(sessions, name, repo)


def test_a_newer_journal_is_refused(repo, tmp_path):
    session = _one_session(repo, tmp_path / "home")
    lines = session.path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["payload"]["schema_version"] = 2
    session.path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n",
                            encoding="utf-8")
    with pytest.raises(ResumeError, match="newer Foundry"):
        read_journal(session.path)


def test_a_request_that_does_not_continue_the_conversation_is_refused(tmp_path):
    runtime, session, _ = _core(tmp_path, [turn(text="one"), turn(text="two")])
    runtime.run_turn("first")
    runtime.run_turn("second")
    session.close()
    lines = session.path.read_text(encoding="utf-8").splitlines()
    requests = [i for i, line in enumerate(lines) if '"model_request"' in line]
    record = json.loads(lines[requests[-1]])
    record["payload"]["messages"] = record["payload"]["messages"][:2]
    lines[requests[-1]] = json.dumps(record)
    session.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ResumeError, match="inconsistent journal"):
        read_journal(session.path)


def test_a_damaged_journal_is_refused_never_crashed_on(tmp_path):
    """The journal is the user's file and is read as untrusted input: whatever
    a field holds, reading it either works or says why it cannot."""
    script = [turn(call("c1", "read_file", {"path": "x.txt"}), opaque=THINKING),
              turn(call("c2", "read_file", {"path": "x.txt"}), call("c3", "finish", {}))]
    runtime, session, repo = _core(tmp_path, script)
    (repo / "x.txt").write_text("data\n", encoding="utf-8")
    runtime.run_turn("read")
    session.close()
    lines = session.path.read_text(encoding="utf-8").splitlines()
    damaged = tmp_path / "damaged" / "events.jsonl"
    damaged.parent.mkdir()
    checked = 0
    for index, line in enumerate(lines):
        record = json.loads(line)
        for key in list(record["payload"]) + [None]:
            for value in (None, 7, "x", [], {}, [None], {"kind": "zzz"}):
                copy = json.loads(line)
                if key is None:
                    copy["payload"] = value
                else:
                    copy["payload"][key] = value
                damaged.write_text("\n".join([*lines[:index], json.dumps(copy),
                                              *lines[index + 1:]]) + "\n", encoding="utf-8")
                try:
                    read_journal(damaged)
                except ResumeError:
                    pass
                checked += 1
    assert checked > 200


def test_a_change_of_model_is_announced(repo, tmp_path):
    session = _one_session(repo, tmp_path / "home")
    wiring = _wire(repo, tmp_path / "home", [], resume=session.session_id,
                   overrides={"model": "another"})
    heard = []
    wiring.runtime.events.subscribe(heard.append)
    _announce_found(wiring)
    _close(wiring, TerminalStatus.CANCELLED, "session ended")
    texts = [e.text for e in heard if isinstance(e, Notice)]
    assert any(t.startswith(f"resumed session {session.session_id}") for t in texts)
    assert any("continues on" in t and "another" in t for t in texts)


# --- the commands ---------------------------------------------------------------------


@pytest.fixture()
def fake_model():
    from test_cli_e2e import _ModelHandler
    import threading
    from http.server import HTTPServer

    httpd = HTTPServer(("127.0.0.1", 0), _ModelHandler)
    httpd.requests = []
    _ModelHandler.index = 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, _ModelHandler
    httpd.shutdown()


def test_exec_resume_through_main(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import text_turn, tool_turn

    httpd, handler = fake_model
    host, port = httpd.server_address
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    workspace = ["--workspace", str(repo)]

    assert app.main(["exec", *workspace]) == 2
    assert "needs a task" in capsys.readouterr().err

    handler.turns, handler.index = [tool_turn("read_file", {"path": "a.py"}),
                                    text_turn("A is 1")], 0
    assert app.main(["exec", "what is A?", *workspace]) == 10  # partial: never called finish
    capsys.readouterr()

    handler.turns, handler.index = [tool_turn("finish", {"status": "completed",
                                                         "summary": "A is 1",
                                                         "claims": []}, "f1")], 0
    httpd.requests.clear()
    assert app.main(["exec", "--resume", "last", "--json", *workspace]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert any(e.get("kind") == "notice" and "resumed session" in e.get("text", "")
               for e in events)
    user = [m for m in httpd.requests[0]["messages"] if m["role"] == "user"][-1]["content"]
    assert user.startswith("This session was resumed.") and user.endswith(CONTINUE_PROMPT)

    assert app.main(["exec", "--resume", "last", *workspace]) == 2
    assert "give the task" in capsys.readouterr().err
