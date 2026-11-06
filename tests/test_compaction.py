"""Summary compaction (D-040): a long session goes on instead of running out.

Once masking has done what it can and the context is still past the
threshold, the runtime asks the model for a handoff summary and the history
becomes the user's own messages plus that summary. The properties that matter:
it happens before the context is exhausted, a resume follows the history past
it byte for byte, a failure leaves the conversation as it was, the summary is
scrubbed like everything else, and nothing read before it vouches for a patch
after it.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app
from foundry.cli.report import analyse
from foundry.core.backends.replay import _emit
from foundry.core.commands import builtin_prompt
from foundry.core.config import load_config
from foundry.core.context import (
    COMPACT_USER_FRACTION,
    SUMMARY_PREFIX,
    ContextManager,
)
from foundry.core.conversation import (
    Capabilities,
    Message,
    ModelTurn,
    Role,
    StopReason,
    ToolResultBlock,
    Usage,
)
from foundry.core.errors import Cancelled, ConfigError, ProtocolError, TransientError
from foundry.core.events import (
    Compacted,
    MessageDelta,
    Notice,
    PlanStep,
    StepStatus,
    TerminalStatus,
)
from foundry.core.redaction import Redactor
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore
from test_resume import _core, _restored, _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

PROMPT = builtin_prompt("compact").strip()
SUMMARY = "Read f0 to f3; nothing changed yet. Next: read f4."


def _asks_for_summary(request) -> bool:
    last = request.messages[-1]
    return last.role is Role.USER and last.text_content.startswith(PROMPT)


class _Model:
    """Work turns in order, and a summary whenever the runtime asks for one."""

    def __init__(self, work, *, summary=SUMMARY, fail=None):
        self.work = list(work)
        self.summary = summary
        self.fail = fail
        self.seen = []

    def capabilities(self):
        return Capabilities()

    def stream_turn(self, request):
        self.seen.append(request)
        if _asks_for_summary(request):
            if self.fail is not None:
                raise self.fail
            return _emit(ModelTurn(text=self.summary, usage=Usage(7, 3),
                                   stop_reason=StopReason.END_TURN))
        if not self.work:
            raise ProtocolError("the test model has no more turns")
        return _emit(self.work.pop(0))


def _reads(count):
    return [turn(call(f"r{i}", "read_file", {"path": f"f{i}.txt"})) for i in range(count)]


def _long_task(tmp_path, *, files=8, model=None, max_context=3_000, redactor=None):
    """A task whose recent rounds alone outgrow the window: masking cannot help."""
    model = model or _Model([*_reads(files), turn(text="read them all")])
    runtime, session, repo = _core(tmp_path, [], max_context=max_context, reserve=0,
                                   redactor=redactor)
    runtime.backend = model
    for i in range(files):
        (repo / f"f{i}.txt").write_text("".join(f"line {i}.{n} " + "x" * 50 + "\n"
                                                for n in range(50)), encoding="utf-8")
    events = []
    runtime.events.subscribe(events.append)
    return runtime, session, repo, model, events


def _summary_requests(model):
    return [i for i, r in enumerate(model.seen) if _asks_for_summary(r)]


# --- the compacted history -----------------------------------------------------


def _context(*messages, max_context=10_000):
    context = ContextManager(system_prompt="t", max_context_tokens=max_context)
    for message in messages:
        context.append(message)
    return context


def user(text):
    return Message.text(Role.USER, text)


def assistant(text):
    return Message.text(Role.ASSISTANT, text)


def test_it_keeps_the_users_messages_oldest_first_and_then_the_summary():
    context = _context(user("fix the parser"), assistant("on it"),
                       user("also the docs"), assistant("done"))
    history = context.compacted_history(SUMMARY)
    assert history == [user("fix the parser"), user("also the docs"),
                       user(SUMMARY_PREFIX + SUMMARY)]


def test_the_newest_messages_win_when_they_do_not_all_fit():
    budget = int(10_000 * COMPACT_USER_FRACTION) * 4
    old, middle, new = "old task", "m" * (budget - 10), "the latest question"
    context = _context(user(old), assistant("a"), user(middle), assistant("b"), user(new))
    kept = [m.text_content for m in context.compacted_history(SUMMARY)[:-1]]
    # The middle one no longer fits whole and is cut; the oldest is dropped.
    assert kept[-1] == new and old not in kept
    assert len(kept) == 2 and len(kept[0]) <= budget - len(new) and "characters elided" in kept[0]


def test_a_single_huge_message_is_cut_rather_than_lost():
    budget = int(10_000 * COMPACT_USER_FRACTION) * 4
    context = _context(user("y" * (budget * 3)), assistant("a"))
    kept = context.compacted_history(SUMMARY)[0].text_content
    assert 0 < len(kept) <= budget


def test_an_earlier_summary_is_not_kept_as_a_user_message():
    context = _context(user("task"), user(SUMMARY_PREFIX + "old summary"), assistant("a"))
    history = context.compacted_history("new summary")
    assert [m.text_content for m in history] == ["task", SUMMARY_PREFIX + "new summary"]


def test_the_summary_is_capped_and_scrubbed():
    secret = "s3cr3t-token-0123456789"
    redactor = Redactor()
    redactor.register(secret)
    context = ContextManager(system_prompt="t", max_context_tokens=2_000, redactor=redactor)
    context.append(user("task"))
    text = context.compacted_history(f"the key is {secret} " + "z" * 50_000)[-1].text_content
    assert secret not in text
    assert len(text) - len(SUMMARY_PREFIX) <= int(2_000 * 0.15) * 4


def test_only_model_output_makes_a_history_worth_compacting():
    context = _context(user("x" * 40_000), max_context=1_000)
    assert not context.needs_compaction()          # nothing but the user's own text
    context.append(assistant("y"))
    assert context.needs_compaction()
    context.replace_history(context.compacted_history(SUMMARY))
    assert not context.compactable()
    assert context._turn_of_message == [context.turn] * len(context.history)


# --- automatically, before the context runs out --------------------------------


def test_a_long_task_compacts_instead_of_running_out(tmp_path):
    runtime, session, _, model, events = _long_task(tmp_path)
    outcome = runtime.run_turn("read f0.txt to f7.txt")
    session.close()

    assert outcome.status is None and outcome.text == "read them all"
    first = _summary_requests(model)[0]
    asked = model.seen[first]
    # The whole conversation goes to the summary request, with the same tools.
    assert asked.messages[:-1] == model.seen[first - 1].messages[:1] + asked.messages[1:-1]
    assert [t.name for t in asked.tools] == [t.name for t in model.seen[0].tools]
    after = model.seen[first + 1].messages
    assert after[1:] == (user("read f0.txt to f7.txt"), user(SUMMARY_PREFIX + SUMMARY))
    compacted = [e for e in events if isinstance(e, Compacted)]
    assert compacted and compacted[0].auto
    # At the threshold, while the window still had room -- not once it was full.
    assert 0.8 * 3_000 < compacted[0].tokens_before <= 3_000
    assert compacted[0].tokens_after < compacted[0].tokens_before
    # The summary is not the model's reply to the user.
    assert not any(isinstance(e, MessageDelta) and SUMMARY[:10] in e.text for e in events)


def test_the_journal_says_what_the_compaction_sent_and_left(tmp_path):
    runtime, session, _, model, _ = _long_task(tmp_path)
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    records = list(SessionStore.read_records(session.path))

    requests = [r for r in records if r.type == EventType.MODEL_REQUEST]
    purposes = [r.payload.get("purpose") for r in requests]
    assert purposes.count("compact") == len(_summary_requests(model)) >= 1
    responses = [r for r in records if r.type == EventType.MODEL_RESPONSE
                 and r.payload.get("purpose") == "compact"]
    assert responses and responses[0].payload["text"] == SUMMARY
    assert any(r.type == EventType.TOKEN_USAGE and r.payload.get("purpose") == "compact"
               for r in records)
    record = next(r for r in records if r.type == EventType.COMPACTED)
    assert record.payload["reason"] == "auto"
    assert record.payload["replaced"] > 2
    assert record.payload["tokens_after"] < record.payload["tokens_before"]
    assert record.payload["messages"][-1]["blocks"][0]["text"] == SUMMARY_PREFIX + SUMMARY


def test_a_resume_follows_the_history_past_a_compaction(tmp_path):
    runtime, session, _, _, _ = _long_task(tmp_path)
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    live = runtime.context

    state = read_journal(session.path)
    restored = _restored(state, max_context=3_000, reserve=0)
    assert state.repairs == ()
    assert restored.history == live.history
    assert restored._turn_of_message == live._turn_of_message
    assert restored._turn == live._turn
    assert restored.project() == live.project()
    assert restored.session_usage == live.session_usage


def test_a_resume_right_after_a_compaction_starts_from_what_it_left(tmp_path):
    """Stopped before the next request: only the compacted record knows."""
    model = _Model(_reads(3))          # no turn left after the compaction
    runtime, session, _, _, _ = _long_task(tmp_path, model=model)
    outcome = runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert outcome.status is TerminalStatus.FAILED
    assert runtime.context.history[-1] == user(SUMMARY_PREFIX + SUMMARY)

    state = read_journal(session.path)
    assert list(state.history) == runtime.context.history
    assert list(state.turns) == runtime.context._turn_of_message


def test_one_round_that_overflows_the_window_is_compacted_not_ended(tmp_path):
    model = _Model([turn(call("big", "read_file", {"path": "big.txt"})), turn(text="read it")])
    runtime, session, repo, _, events = _long_task(tmp_path, files=0, model=model)
    (repo / "big.txt").write_text("w" * 14_000, encoding="utf-8")
    outcome = runtime.run_turn("read big.txt")
    session.close()
    assert outcome.status is None and outcome.text == "read it"
    assert next(e for e in events if isinstance(e, Compacted)).tokens_before > 3_000


def test_a_crash_between_the_summary_and_its_record_resumes_from_before_it(tmp_path):
    runtime, session, _, _, _ = _long_task(tmp_path)
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    lines = session.path.read_text(encoding="utf-8").splitlines(keepends=True)
    cut = next(i for i, line in enumerate(lines) if json.loads(line)["type"] == "compacted")
    crashed = tmp_path / "crashed" / "events.jsonl"
    crashed.parent.mkdir()
    crashed.write_text("".join(lines[:cut]), encoding="utf-8")

    state = read_journal(crashed)
    assert state.history[-1].role is Role.TOOL      # the last read, answered
    assert not any(SUMMARY in m.text_content or PROMPT in m.text_content
                   for m in state.history)
    assert state.repairs == ()


def test_the_record_is_synced_like_the_evidence():
    from foundry.core.session import _FSYNC_TYPES
    assert EventType.COMPACTED in _FSYNC_TYPES


def test_a_summary_that_never_landed_is_not_part_of_the_history(tmp_path):
    model = _Model([*_reads(8), turn(text="done")], summary="")
    runtime, session, _, _, events = _long_task(tmp_path, model=model)
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()

    warnings = [e.text for e in events if isinstance(e, Notice) and e.level == "warning"]
    assert any("the model wrote no summary" in w for w in warnings)
    state = read_journal(session.path)
    assert list(state.history) == runtime.context.history
    assert not any(SUMMARY_PREFIX in m.text_content for m in state.history)


def test_a_failed_compaction_changes_nothing_and_is_not_retried_every_round(tmp_path):
    model = _Model([*_reads(8), turn(text="done")], fail=TransientError("gateway busy"))
    runtime, session, _, _, events = _long_task(tmp_path, model=model)

    outcome = runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert len(_summary_requests(model)) == 1
    warnings = [e.text for e in events if isinstance(e, Notice) and e.level == "warning"]
    assert any("compaction failed: gateway busy; carrying on without it" in w for w in warnings)
    assert not any(isinstance(e, Compacted) for e in events)
    # Without it, the task runs out of context as it did before compaction.
    assert outcome.status is TerminalStatus.PARTIAL
    assert not any(SUMMARY_PREFIX in m.text_content for m in runtime.context.history)
    records = list(SessionStore.read_records(session.path))
    assert not any(r.type == EventType.COMPACTED for r in records)


def test_the_next_task_may_try_again(tmp_path):
    model = _Model([*_reads(8), turn(text="done")], fail=TransientError("gateway busy"))
    runtime, session, _, _, events = _long_task(tmp_path, model=model)
    assert runtime.run_turn("read f0.txt to f7.txt").status is TerminalStatus.PARTIAL
    assert len(_summary_requests(model)) == 1
    model.fail = None
    assert runtime.run_turn("go on").text == "done"
    session.close()
    # The second task compacts at once: the context is as full as it was.
    second = _summary_requests(model)[1]
    assert model.seen[second].messages[-2] == user("go on")
    assert any(isinstance(e, Compacted) for e in events)


def test_off_means_never(tmp_path):
    runtime, session, _, model, _ = _long_task(tmp_path)
    runtime.auto_compact = False
    outcome = runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert _summary_requests(model) == []
    assert outcome.status is TerminalStatus.PARTIAL


def test_an_interrupted_compaction_cancels_the_task(tmp_path):
    model = _Model(_reads(8), fail=Cancelled("interrupted"))
    runtime, session, _, _, _ = _long_task(tmp_path, model=model)
    outcome = runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert outcome.status is TerminalStatus.CANCELLED


def test_files_read_before_it_are_read_again_before_a_patch(tmp_path):
    runtime, session, _, model, _ = _long_task(tmp_path)
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    first = _summary_requests(model)[0]
    # Read before the compaction, so forgotten; read after it, so remembered.
    assert not runtime.tool_ctx.read_tracker.has_read("f0.txt")
    assert runtime.tool_ctx.read_tracker.has_read("f7.txt")
    assert first > 1


def test_the_plan_survives_in_the_summary(tmp_path):
    runtime, session, _, _, _ = _long_task(tmp_path)
    runtime.plan = (PlanStep("read the files", StepStatus.IN_PROGRESS),
                    PlanStep("report", StepStatus.PENDING))
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    summary = next(m.text_content for m in runtime.context.history
                   if m.text_content.startswith(SUMMARY_PREFIX))
    assert summary.endswith("Your plan, as update_plan last set it:\n"
                            "- [in_progress] read the files\n- [pending] report")


def test_no_tool_output_survives_a_compaction(tmp_path):
    runtime, session, _, model, _ = _long_task(tmp_path)
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    after = model.seen[_summary_requests(model)[0] + 1].messages
    assert not any(isinstance(b, ToolResultBlock) for m in after for b in m.blocks)


# --- /compact, the report, the setting -----------------------------------------


def test_compact_on_request_with_a_focus(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = _Model([turn(call("c1", "read_file", {"path": "a.py"})),
                                     turn(text="A is 1"), turn(text="B is 1")])
    shown = _shown(_repl(wiring, "what is A?", "/compact the tests", "and B?"))
    seen = wiring.runtime.backend.seen

    asked = seen[_summary_requests(wiring.runtime.backend)[0]].messages[-1].text_content
    assert asked.endswith("give particular attention to: the tests")
    assert seen[-1].messages[1:] == (user("what is A?"), user(SUMMARY_PREFIX + SUMMARY),
                                     user("and B?"))
    assert "unchanged" not in shown
    assert "context compacted on request" in wiring.renderer.console.file.getvalue()
    records = list(SessionStore.read_records(wiring.session.path))
    assert next(r for r in records if r.type == EventType.COMPACTED).payload["reason"] == "user"


def test_compact_with_nothing_to_compact(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = _Model([])
    shown = _shown(_repl(wiring, "/compact"))
    assert "nothing to compact yet; the conversation is unchanged" in shown
    assert wiring.runtime.backend.seen == []


def test_compact_that_fails_says_so(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = _Model([turn(text="hello")], fail=TransientError("busy"))
    shown = _shown(_repl(wiring, "hi", "/compact"))
    assert "compaction failed: busy; the conversation is unchanged" in shown
    assert wiring.runtime.context.history[-1].text_content == "hello"


def test_compact_interrupted_says_so(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = _Model([turn(text="hello")], fail=Cancelled("ctrl-c"))
    shown = _shown(_repl(wiring, "hi", "/compact"))
    assert "compaction cancelled; the conversation is unchanged" in shown


def test_the_report_does_not_count_a_summary_as_a_round(tmp_path):
    runtime, session, _, model, _ = _long_task(tmp_path)
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    report = analyse(session.path)
    assert report.compactions == len(_summary_requests(model)) >= 1
    assert report.turns == len(model.seen) - report.compactions


def test_the_setting(tmp_path, repo):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[runtime]\nauto_compact = false\n", encoding="utf-8")
    assert load_config(home=home).auto_compact is False
    assert load_config(home=tmp_path / "empty").auto_compact is True
    assert _wire(repo, home, []).runtime.auto_compact is False

    (home / "config.toml").write_text('[runtime]\nauto_compact = "no"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="auto_compact must be true or false"):
        load_config(home=home)


def test_the_event_reaches_a_json_consumer():
    payload = app._event_payload(Compacted(tokens_before=9_000, tokens_after=800, auto=True))
    assert json.loads(json.dumps(payload)) == {"kind": "compacted", "tokens_before": 9_000,
                                               "tokens_after": 800, "auto": True}
