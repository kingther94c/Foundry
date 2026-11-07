"""PreCompact and PostCompact hooks, and SessionStart's ``compact`` source, as
codex's (D-118): what their answers mean, and what a compaction does with
them -- by hand (``/compact``) or on its own in the middle of a task."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from foundry.cli.repl import _compact
from foundry.core.events import TerminalStatus
from foundry.core.hooks import (
    Hook,
    HookRun,
    HookSet,
    parse_hooks,
    read_post_compact,
    read_pre_compact,
)
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore
from foundry.core.winapi import IS_WINDOWS
from test_compaction import SUMMARY, _long_task, _summary_requests
from test_hooks import (  # noqa: F401  (fixtures)
    Scripted,
    _group,
    _hooks_json,
    _notices,
    _records,
    _shown,
    _wire,
    home,
    machine,
    repo,
    turn,
)
from test_prompt_stop_hooks import _user_texts

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

PRE = Hook(event="PreCompact", matcher="", command="pre")
POST = Hook(event="PostCompact", matcher="", command="post")
TASK = "read f0.txt to f7.txt"


def _read(reader, hook: Hook, stdout="", **fields):
    return reader([HookRun(hook, exit_code=fields.pop("exit_code", 0),
                           stdout=json.dumps(stdout) if isinstance(stdout, dict) else stdout,
                           **fields)])


def _outcome(verdict) -> str:
    (record,) = verdict.records
    return record["outcome"]


def _stdin(hook) -> list[dict]:
    return [c["stdin"] for c in hook.calls()]


def _types(path: Path) -> list[tuple[str, str]]:
    """The journal's hooks and compactions, in order."""
    return [(r.type, r.payload.get("event", "")) for r in SessionStore.read_records(path)
            if r.type in (EventType.HOOK, EventType.COMPACTED)]


def _hooked(runtime, repo, events: dict) -> None:
    runtime.hooks = HookSet(parse_hooks(events, "f"), cwd=repo)


def _manual(repo, home, *, summary="the work so far", turns=1):
    """A session with a turn behind it, ready to /compact."""
    wiring = _wire(repo, home, [*(turn(text="ok") for _ in range(turns)), turn(text=summary),
                                turn(text="after")])
    wiring.runtime.run_turn("explain a.py")
    return wiring


# --- what their answers mean ------------------------------------------------------


@pytest.mark.parametrize("reader, hook", [(read_pre_compact, PRE), (read_post_compact, POST)])
def test_nothing_or_plain_text_says_nothing(reader, hook):
    for stdout in ("", "  compacting now \n", '{"decision": "block", "reason": "x"}'):
        verdict = _read(reader, hook, stdout)
        assert (verdict.stops, verdict.messages, verdict.failures, verdict.context) == (
            [], [], [], [])
        assert _outcome(verdict) == "completed"


@pytest.mark.parametrize("reader, hook", [(read_pre_compact, PRE), (read_post_compact, POST)])
def test_continue_false_stops_with_its_reason(reader, hook):
    verdict = _read(reader, hook, {"continue": False, "stopReason": " not now ",
                                   "systemMessage": "m"})
    assert (verdict.stops, verdict.messages, _outcome(verdict)) == (["not now"], ["m"],
                                                                    "stopped")
    assert _read(reader, hook, {"continue": False}).stops == [""]


@pytest.mark.parametrize("reader, event", [(read_pre_compact, "PreCompact"),
                                           (read_post_compact, "PostCompact")])
def test_an_async_compact_hook_informs_and_never_stops(reader, event):
    hook = Hook(event=event, matcher="", command="h", background=True)
    verdict = _read(reader, hook, {"continue": False, "systemMessage": "m"})
    assert (verdict.stops, verdict.messages) == ([], ["m"])


@pytest.mark.parametrize("reader, hook, event", [(read_pre_compact, PRE, "PreCompact"),
                                                 (read_post_compact, POST, "PostCompact")])
@pytest.mark.parametrize("stdout", [{"continue": "no"}, {"systemMessage": 1},
                                    {"stopReason": False}, "[1]", "{broken"])
def test_an_answer_of_the_wrong_shape_is_a_failure(reader, hook, event, stdout):
    verdict = _read(reader, hook, stdout)
    assert verdict.failures == [f"`{hook.command}` returned invalid {event} JSON output"]
    assert not (verdict.stops or verdict.messages)


def test_a_failed_compact_hook_says_why():
    assert _read(read_pre_compact, PRE, exit_code=2, stderr=" quota \n").failures == [
        "`pre` failed: quota"]
    assert _read(read_pre_compact, PRE, {"continue": False}, exit_code=1).failures == [
        "`pre` exited with code 1"]
    assert _read(read_pre_compact, PRE, {"continue": False}, exit_code=1).stops == []
    assert _read(read_post_compact, POST, exit_code=None).failures == [
        "`post` exited without a status code"]
    assert _read(read_post_compact, POST, exit_code=None, error="timed out after 5s"
                 ).failures == ["`post` timed out after 5s"]


# --- where they are read and which run ------------------------------------------------


def test_they_are_matched_on_what_started_the_compaction():
    hooks = HookSet(parse_hooks({
        "PreCompact": [_group("any"), _group("hand", "manual"), _group("own", "auto")],
        "PostCompact": [_group("after", "manual|auto")],
        "SessionStart": [_group("start", "startup"), _group("again", "compact")]}, "f"))
    assert [h.command for h in hooks.matching("PreCompact", ("manual",))] == ["any", "hand"]
    assert [h.command for h in hooks.matching("PreCompact", ("auto",))] == ["any", "own"]
    assert [h.command for h in hooks.matching("PostCompact", ("auto",))] == ["after"]
    assert [h.command for h in hooks.matching("SessionStart", ("compact",))] == ["again"]


def test_slash_hooks_lists_them(repo, home):
    _hooks_json(home, {"PreCompact": [_group("p.cmd", "auto")],
                       "PostCompact": [_group("q.cmd")]})
    where = home / "hooks.json"
    assert _shown(_wire(repo, home, [])).splitlines() == [
        f"PreCompact auto  p.cmd ({where}, 600s)", f"PostCompact *  q.cmd ({where}, 600s)"]


# --- /compact -----------------------------------------------------------------------


def test_compact_tells_its_hooks_before_and_after(repo, home, tmp_path):
    pre, post = Scripted(tmp_path / "pre"), Scripted(tmp_path / "post")
    _hooks_json(home, {"PreCompact": [_group(pre.command, "manual")],
                       "PostCompact": [_group(post.command)]})
    wiring = _manual(repo, home)
    assert wiring.runtime.compact(auto=False) == ""
    (before,) = _stdin(pre)
    assert Path(before.pop("cwd")) == Path(wiring.workspace.root)
    assert before == {"session_id": wiring.session.session_id,
                      "transcript_path": str(wiring.session.path),
                      "hook_event_name": "PreCompact", "model": "fake", "turn_id": "1",
                      "trigger": "manual"}
    (after,) = _stdin(post)
    after.pop("cwd")
    assert after == {**before, "hook_event_name": "PostCompact"}
    assert _types(wiring.session.path) == [(EventType.HOOK, "PreCompact"),
                                           (EventType.COMPACTED, ""),
                                           (EventType.HOOK, "PostCompact")]


def test_a_precompact_hook_may_keep_the_conversation_as_it_is(repo, home, tmp_path):
    pre = Scripted(tmp_path / "pre").answer({"continue": False, "stopReason": "keep it all"})
    _hooks_json(home, {"PreCompact": [_group(pre.command)]})
    wiring = _manual(repo, home)
    history = list(wiring.runtime.context.history)
    asked = len(wiring.runtime.backend.seen)
    out = io.StringIO()
    _compact(wiring.wiring, Console(file=out, width=200), "")
    assert out.getvalue().strip() == ("a PreCompact hook stopped compaction: keep it all; "
                                      "the conversation is unchanged")
    assert wiring.runtime.context.history == history
    assert len(wiring.runtime.backend.seen) == asked          # no summary asked for
    assert _records(wiring, EventType.COMPACTED) == []
    # The next task is not the worse for it.
    assert wiring.runtime.run_turn("go on").text == "the work so far"


def test_a_postcompact_stop_after_compact_is_said(repo, home, tmp_path):
    post = Scripted(tmp_path / "post").answer({"continue": False, "stopReason": "check it"})
    _hooks_json(home, {"PostCompact": [_group(post.command)]})
    wiring = _manual(repo, home)
    assert wiring.runtime.compact(auto=False) == ""
    assert len(_records(wiring, EventType.COMPACTED)) == 1
    assert "a PostCompact hook stopped the task after compaction: check it" in _notices(wiring)
    # Nothing else to stop: the next task goes on.
    assert wiring.runtime.run_turn("go on").text == "after"


def test_after_compact_the_start_hooks_run_at_the_next_prompt(repo, home, tmp_path):
    start = Scripted(tmp_path / "s").answer("re-read the plan")
    fresh = Scripted(tmp_path / "f")
    _hooks_json(home, {"SessionStart": [_group(start.command, "compact"),
                                        _group(fresh.command, "startup")]})
    wiring = _manual(repo, home)
    assert [c["source"] for c in _stdin(fresh)] == ["startup"]
    assert start.calls() == []
    wiring.runtime.compact(auto=False)
    assert start.calls() == []                 # owed, not run, until a prompt
    wiring.runtime.run_turn("go on")
    (told,) = _stdin(start)
    assert told["source"] == "compact" and "turn_id" not in told
    request = wiring.runtime.backend.seen[-1]
    assert _user_texts(request)[-1] == "go on\n\nFrom a SessionStart hook: re-read the plan"
    assert len(fresh.calls()) == 1


def test_starts_owed_run_in_the_order_owed(repo, home, tmp_path):
    every, again = Scripted(tmp_path / "e"), Scripted(tmp_path / "a")
    _hooks_json(home, {"SessionStart": [_group(every.command, "startup|compact"),
                                        _group(again.command, "compact")]})
    wiring = _wire(repo, home, [turn(text="ok")])
    # A source no hook matches is spent, and the rest still run.
    wiring.runtime.start_sources = ["resume", "startup", "compact"]
    wiring.runtime.run_turn("go")
    assert [c["source"] for c in _stdin(every)] == ["startup", "compact"]
    assert [c["source"] for c in _stdin(again)] == ["compact"]
    assert wiring.runtime.start_sources == []


def test_no_hooks_owe_nothing(repo, home):
    wiring = _manual(repo, home)
    owed = list(wiring.runtime.start_sources)
    assert wiring.runtime.compact(auto=False) == ""
    assert wiring.runtime.start_sources == owed


# --- a compaction in the middle of a task -----------------------------------------------


def test_an_automatic_compaction_tells_its_hooks(tmp_path):
    runtime, session, repo, model, _ = _long_task(tmp_path)
    pre, post = Scripted(tmp_path / "pre"), Scripted(tmp_path / "post")
    _hooked(runtime, repo, {"PreCompact": [_group(pre.command, "auto")],
                            "PostCompact": [_group(post.command, "auto")]})
    outcome = runtime.run_turn(TASK)
    assert outcome.text == "read them all"
    assert _summary_requests(model)
    assert {c["trigger"] for c in _stdin(pre) + _stdin(post)} == {"auto"}
    assert len(_stdin(pre)) == len(_stdin(post)) == len(_summary_requests(model))


def test_a_precompact_stop_ends_the_task_before_compacting(tmp_path):
    runtime, session, repo, model, _ = _long_task(tmp_path)
    pre = Scripted(tmp_path / "pre").answer({"continue": False, "stopReason": "too costly"})
    _hooked(runtime, repo, {"PreCompact": [_group(pre.command)]})
    outcome = runtime.run_turn(TASK)
    assert (outcome.status, outcome.text) == (
        TerminalStatus.BLOCKED, "a PreCompact hook stopped compaction: too costly")
    assert _summary_requests(model) == []
    assert not any(r.type == EventType.COMPACTED for r in SessionStore.read_records(session.path))


def test_a_postcompact_stop_ends_the_task_after_compacting(tmp_path):
    runtime, session, repo, model, _ = _long_task(tmp_path)
    post = Scripted(tmp_path / "post").answer({"continue": False})
    _hooked(runtime, repo, {"PostCompact": [_group(post.command)]})
    outcome = runtime.run_turn(TASK)
    assert (outcome.status, outcome.text) == (
        TerminalStatus.BLOCKED, "a PostCompact hook stopped the task after compaction")
    assert len(_summary_requests(model)) == 1
    assert model.seen[-1] is model.seen[_summary_requests(model)[0]]   # nothing asked after
    assert sum(r.type == EventType.COMPACTED
               for r in SessionStore.read_records(session.path)) == 1


def test_a_stop_ends_only_its_own_task(tmp_path):
    runtime, session, repo, model, _ = _long_task(tmp_path)
    post = Scripted(tmp_path / "post").answer({"continue": False, "stopReason": "look first"})
    _hooked(runtime, repo, {"PostCompact": [_group(post.command)]})
    assert runtime.run_turn(TASK).status is TerminalStatus.BLOCKED
    post.answer("")
    outcome = runtime.run_turn("go on")
    assert outcome.text == "read them all"
    assert len(_summary_requests(model)) > 1


def test_a_start_hooks_note_goes_to_the_work_in_hand(tmp_path):
    runtime, session, repo, model, _ = _long_task(tmp_path)
    start = Scripted(tmp_path / "s").answer({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": "the plan is in PLAN.md"}})
    _hooked(runtime, repo, {"SessionStart": [_group(start.command, "compact")]})
    outcome = runtime.run_turn(TASK)
    assert outcome.text == "read them all"
    summary_at = _summary_requests(model)[0]
    following = model.seen[summary_at + 1]
    note = "From a SessionStart hook: the plan is in PLAN.md"
    assert _user_texts(following)[-1] == note
    assert SUMMARY in _user_texts(following)[-2]
    assert [c["source"] for c in _stdin(start)] == ["compact"] * len(_summary_requests(model))
    inputs = [r.payload for r in SessionStore.read_records(session.path)
              if r.type == EventType.USER_INPUT and r.payload.get("hook")]
    assert inputs[0] == {"text": note, "hook": "SessionStart"}
    session.close()
    assert any(note in m.text_content for m in read_journal(session.path).history)


def test_a_start_hook_may_end_the_task_after_compacting(tmp_path):
    runtime, session, repo, model, _ = _long_task(tmp_path)
    start = Scripted(tmp_path / "s").answer({"continue": False, "stopReason": "start over"})
    _hooked(runtime, repo, {"SessionStart": [_group(start.command)]})
    outcome = runtime.run_turn(TASK)
    assert (outcome.status, outcome.text) == (
        TerminalStatus.BLOCKED, "a SessionStart hook stopped the task after compaction: start over")
    assert model.seen[-1] is model.seen[_summary_requests(model)[0]]
    assert runtime.start_sources == []


def test_owed_starts_run_in_order_each_once(tmp_path):
    runtime, session, repo, model, _ = _long_task(tmp_path)
    start = Scripted(tmp_path / "s")
    _hooked(runtime, repo, {"SessionStart": [_group(start.command)]})
    runtime.start_sources = ["resume"]
    runtime.run_turn(TASK)
    sources = [c["source"] for c in _stdin(start)]
    assert sources[0] == "resume"
    assert sources[1:] == ["compact"] * len(_summary_requests(model))
    assert runtime.start_sources == []


def test_a_failing_compact_hook_is_a_warning_and_compaction_goes_on(tmp_path):
    runtime, session, repo, model, events = _long_task(tmp_path)
    pre = Scripted(tmp_path / "pre").answer(stderr="no quota left", exit=1)
    _hooked(runtime, repo, {"PreCompact": [_group(pre.command)]})
    assert runtime.run_turn(TASK).text == "read them all"
    assert _summary_requests(model)
    assert any(getattr(e, "text", "").endswith("failed: no quota left") for e in events)
