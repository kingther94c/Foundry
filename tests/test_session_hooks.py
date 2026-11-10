"""SessionStart, SessionEnd and Interrupt hooks, as codex's (D-117): what
their answers mean, and when the runtime runs them -- SessionStart once, at a
session's first prompt (``startup`` or ``resume``); Interrupt when a running
task is stopped; SessionEnd as the session closes, before its journal does."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry.cli import app
from foundry.cli.wiring import _close, build
from foundry.core.config import load_config
from foundry.core.conversation import TextBlock
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.hooks import (
    SHORT_MAX_TIMEOUT_S,
    SHORT_TIMEOUT_S,
    Hook,
    HookRun,
    HookSet,
    parse_hooks,
    read_interrupt,
    read_session_end,
    read_session_start,
)
from foundry.core.resume import is_locked
from foundry.core.session import EventType, SessionStore
from foundry.core.winapi import IS_WINDOWS
from test_hooks import (  # noqa: F401  (fixtures)
    OVERRIDES,
    Scripted,
    _group,
    _hooks_json,
    _notices,
    _records,
    _shown,
    _wire,
    call,
    home,
    machine,
    repo,
    turn,
)
from test_prompt_stop_hooks import _note, _user_texts
from test_resume import _Live

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

START = Hook(event="SessionStart", matcher="", command="start")
END = Hook(event="SessionEnd", matcher="", command="end")
INTR = Hook(event="Interrupt", matcher="", command="intr")


def _read(reader, hook: Hook, stdout="", **fields):
    return reader([HookRun(hook, exit_code=fields.pop("exit_code", 0),
                           stdout=json.dumps(stdout) if isinstance(stdout, dict) else stdout,
                           **fields)])


def _outcome(verdict) -> str:
    (record,) = verdict.records
    return record["outcome"]


def _start_note(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def _stdin(hook) -> list[dict]:
    return [c["stdin"] for c in hook.calls()]


class _CtrlC:
    """A model turn the user stops while it streams."""

    def __call__(self):
        raise KeyboardInterrupt


# --- configuration --------------------------------------------------------------


def test_session_end_and_interrupt_hooks_get_a_second_and_three_at_most():
    hooks = parse_hooks({
        "SessionEnd": [_group("a"), _group("b", timeout=2), _group("c", timeout=30)],
        "Interrupt": [_group("d"), _group("e", timeout=5)],
        "SessionStart": [_group("f")]}, "f")
    assert [h.timeout_s for h in hooks] == [SHORT_TIMEOUT_S, 2, SHORT_MAX_TIMEOUT_S,
                                            SHORT_TIMEOUT_S, SHORT_MAX_TIMEOUT_S, 600]
    assert (SHORT_TIMEOUT_S, SHORT_MAX_TIMEOUT_S) == (1, 3)


def test_a_short_timeout_is_still_checked():
    from foundry.core.errors import ConfigError

    with pytest.raises(ConfigError, match="timeout is in seconds"):
        parse_hooks({"SessionEnd": [_group("a", timeout=0)]}, "f")


def test_the_three_load_from_your_hooks_json(home):
    _hooks_json(home, {"SessionStart": [_group("s", "startup")],
                       "SessionEnd": [_group("e")], "Interrupt": [_group("i")]})
    assert [(h.event, h.matcher) for h in load_config(home=home).hooks] == [
        ("SessionStart", "startup"), ("SessionEnd", ""), ("Interrupt", "")]


def test_session_start_matches_its_source_and_session_end_its_reason():
    hooks = HookSet(parse_hooks({
        "SessionStart": [_group("any"), _group("fresh", "startup"), _group("again", "resume")],
        "SessionEnd": [_group("other", "other"), _group("never", "logout")],
        "Interrupt": [_group("i", "Bash")]}, "f"))
    assert [h.command for h in hooks.matching("SessionStart", ("startup",))] == ["any", "fresh"]
    assert [h.command for h in hooks.matching("SessionStart", ("resume",))] == ["any", "again"]
    assert [h.command for h in hooks.matching("SessionEnd", ("other",))] == ["other"]
    assert [h.command for h in hooks.matching("Interrupt", ())] == ["i"]


def test_slash_hooks_lists_them(repo, home):
    _hooks_json(home, {"SessionStart": [_group("s.cmd", "resume")],
                       "SessionEnd": [_group("e.cmd", timeout=9)],
                       "Interrupt": [_group("i.cmd", "ignored")]})
    where = home / "hooks.json"
    assert _shown(_wire(repo, home, [])).splitlines() == [
        f"SessionStart resume  s.cmd ({where}, 600s)",
        f"SessionEnd *  e.cmd ({where}, 3s)",
        f"Interrupt  i.cmd ({where}, 1s)"]


# --- what a SessionStart hook's answer means ------------------------------------


def test_a_start_hook_that_prints_nothing_says_nothing():
    verdict = _read(read_session_start, START, "")
    assert (verdict.stops, verdict.context, verdict.failures) == ([], [], [])


def test_plain_text_and_additional_context_are_notes():
    assert _read(read_session_start, START, " use uv \n").context == ["use uv"]
    verdict = _read(read_session_start, START, {"systemMessage": "m", **_start_note("n")})
    assert (verdict.context, verdict.messages) == (["n"], ["m"])


def test_a_start_note_is_cut_at_its_limit():
    hook = Hook(event="SessionStart", matcher="", command="start", context_tokens=1)
    (note,) = _read(read_session_start, hook, "x" * 9).context
    assert note.startswith("xxxx\n[... cut: the hook gave 9 characters")


def test_continue_false_stops_the_first_prompt():
    verdict = _read(read_session_start, START, {"continue": False, "stopReason": " no ",
                                                **_start_note("n")})
    assert (verdict.stops, verdict.context, _outcome(verdict)) == (["no"], ["n"], "stopped")
    assert _read(read_session_start, START, {"continue": False}).stops == [""]


def test_an_async_start_hook_never_stops():
    hook = Hook(event="SessionStart", matcher="", command="start", background=True)
    verdict = _read(read_session_start, hook, {"continue": False, **_start_note("n")})
    assert (verdict.stops, verdict.context) == ([], ["n"])


def test_a_start_hook_does_not_block_or_decide():
    verdict = _read(read_session_start, START, {"decision": "block", "reason": "x"})
    assert (verdict.block, verdict.stops, verdict.failures) == ([], [], [])
    verdict = _read(read_session_start, START, exit_code=2, stderr="stop")
    assert (verdict.block, verdict.stops) == ([], [])
    assert verdict.failures == ["`start` exited with code 2"]


@pytest.mark.parametrize("stdout", [{"hookSpecificOutput": "n"}, {"continue": "no"},
                                    {"systemMessage": 2},
                                    {"hookSpecificOutput": {"additionalContext": 1}},
                                    "[1]", "{broken"])
def test_a_start_answer_of_the_wrong_shape_is_a_failure(stdout):
    verdict = _read(read_session_start, START, stdout)
    assert verdict.failures == ["`start` returned invalid SessionStart JSON output"]
    assert not (verdict.context or verdict.stops)


def test_a_failed_start_hook_is_a_failure():
    assert _read(read_session_start, START, "n", exit_code=1).failures == [
        "`start` exited with code 1"]
    assert _read(read_session_start, START, exit_code=None, error="timed out after 1s"
                 ).failures == ["`start` timed out after 1s"]


# --- what a SessionEnd hook's run means -----------------------------------------


def test_what_a_session_end_hook_prints_is_not_read():
    for stdout in ("", "bye", {"systemMessage": "m", "continue": False}, "[1]"):
        verdict = _read(read_session_end, END, stdout)
        assert (verdict.messages, verdict.failures, verdict.stops, verdict.context) == (
            [], [], [], [])
        assert _outcome(verdict) == "completed"


def test_a_failed_session_end_hook_says_why():
    assert _read(read_session_end, END, exit_code=3, stderr=" disk full \n").failures == [
        "`end` failed: disk full"]
    assert _read(read_session_end, END, exit_code=2).failures == ["`end` exited with code 2"]
    assert _read(read_session_end, END, exit_code=None).failures == [
        "`end` exited without a status code"]
    assert _read(read_session_end, END, exit_code=None, error="timed out after 1s").failures == [
        "`end` timed out after 1s"]


# --- what an Interrupt hook's answer means ---------------------------------------


def test_an_interrupt_hook_may_say_something():
    assert _read(read_interrupt, INTR, "").messages == []
    verdict = _read(read_interrupt, INTR, {"systemMessage": "saved the log"})
    assert (verdict.messages, verdict.failures) == (["saved the log"], [])
    verdict = _read(read_interrupt, INTR, {"continue": False, "decision": "block"})
    assert (verdict.messages, verdict.failures, verdict.block, verdict.stops) == ([], [], [], [])


def test_plain_text_from_an_interrupt_hook_is_a_failure():
    assert _read(read_interrupt, INTR, "saved").failures == [
        "`intr` returned text that is not JSON"]


@pytest.mark.parametrize("stdout", ["[1]", "{broken", {"systemMessage": 1}])
def test_an_interrupt_answer_of_the_wrong_shape_is_a_failure(stdout):
    verdict = _read(read_interrupt, INTR, stdout)
    assert verdict.failures == ["`intr` returned invalid Interrupt JSON output"]
    assert verdict.messages == []


def test_an_interrupt_hooks_code_2_is_a_failure():
    verdict = _read(read_interrupt, INTR, exit_code=2, stderr="no")
    assert (verdict.block, verdict.failures) == ([], ["`intr` exited with code 2"])


# --- the runtime: SessionStart ---------------------------------------------------


def test_a_start_hook_runs_once_at_the_first_prompt(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_start_note("the tests run with uv"))
    _hooks_json(home, {"SessionStart": [_group(hook.command, "startup")]})
    wiring = _wire(repo, home, [turn(text="ok"), turn(text="ok")])
    assert hook.calls() == []                 # not as the session is built
    wiring.runtime.run_turn("fix it")
    wiring.runtime.run_turn("and this")
    (told,) = _stdin(hook)
    assert Path(told.pop("cwd")) == Path(wiring.workspace.root)
    assert told == {"session_id": wiring.session.session_id,
                    "transcript_path": str(wiring.session.path),
                    "hook_event_name": "SessionStart", "model": "fake",
                    "permission_mode": "default", "source": "startup"}
    first, second = wiring.runtime.backend.seen
    assert _user_texts(first)[-1] == "fix it\n\nFrom a SessionStart hook: the tests run with uv"
    assert _user_texts(second)[-1] == "and this"
    inputs = _records(wiring, EventType.USER_INPUT)
    assert inputs[0] == {"text": "fix it\n\nFrom a SessionStart hook: the tests run with uv",
                         "typed": "fix it"}
    (hooked,) = _records(wiring, EventType.HOOK)
    assert hooked["event"] == "SessionStart" and "call_id" not in hooked


def test_start_notes_come_before_prompt_notes(repo, home, tmp_path):
    start = Scripted(tmp_path / "s").answer("start note")
    prompt = Scripted(tmp_path / "p").answer(_note("prompt note"))
    _hooks_json(home, {"SessionStart": [_group(start.command)],
                       "UserPromptSubmit": [_group(prompt.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("go")
    (request,) = wiring.runtime.backend.seen
    assert _user_texts(request)[-1] == ("go\n\nFrom a SessionStart hook: start note"
                                        "\n\nFrom a UserPromptSubmit hook: prompt note")
    assert _stdin(prompt)[0]["prompt"] == "go"


def test_a_start_hook_may_stop_the_first_prompt(repo, home, tmp_path):
    start = Scripted(tmp_path / "s").answer({"continue": False, "stopReason": "VPN is down"})
    prompt = Scripted(tmp_path / "p")
    _hooks_json(home, {"SessionStart": [_group(start.command)],
                       "UserPromptSubmit": [_group(prompt.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    outcome = wiring.runtime.run_turn("go")
    assert (outcome.status, outcome.text) == (
        TerminalStatus.BLOCKED,
        "a SessionStart hook stopped this prompt, so it was not sent: VPN is down")
    assert wiring.runtime.backend.seen == [] and prompt.calls() == []
    assert _records(wiring, EventType.USER_INPUT) == []
    # Spent: the next prompt is sent, and the start hook is not run again.
    assert wiring.runtime.run_turn("go").text == "ok"
    assert len(start.calls()) == 1 and len(prompt.calls()) == 1


def test_a_start_hook_stopping_with_no_reason(repo, home, tmp_path):
    start = Scripted(tmp_path / "s").answer({"continue": False})
    _hooks_json(home, {"SessionStart": [_group(start.command)]})
    outcome = _wire(repo, home, [turn(text="ok")]).runtime.run_turn("go")
    assert outcome.text == "a SessionStart hook stopped this prompt, so it was not sent"


def test_a_failing_start_hook_is_a_warning(repo, home, tmp_path):
    start = Scripted(tmp_path / "s").answer(exit=1)
    _hooks_json(home, {"SessionStart": [_group(start.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    assert wiring.runtime.run_turn("go").text == "ok"
    assert any(n.startswith("SessionStart hook `") and n.endswith("exited with code 1")
               for n in _notices(wiring))
    (request,) = wiring.runtime.backend.seen
    assert _user_texts(request) == ["go"]


def test_a_start_hooks_code_2_is_a_failure_and_the_prompt_goes(repo, home, tmp_path):
    start = Scripted(tmp_path / "s").answer(stderr="not now", exit=2)
    _hooks_json(home, {"SessionStart": [_group(start.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    assert wiring.runtime.run_turn("go").text == "ok"
    assert any(n.startswith("SessionStart hook `") and n.endswith("exited with code 2")
               for n in _notices(wiring))
    (hooked,) = _records(wiring, EventType.HOOK)
    assert hooked["runs"][0]["outcome"] == "failed"


def test_a_start_hook_for_a_resume_runs_on_a_resume_only(repo, home, tmp_path):
    fresh = Scripted(tmp_path / "f")
    again = Scripted(tmp_path / "a").answer("resumed: re-read the diff")
    _hooks_json(home, {"SessionStart": [_group(fresh.command, "startup"),
                                        _group(again.command, "resume")]})
    first = _wire(repo, home, [turn(text="ok")])
    first.runtime.run_turn("go")
    _close(first.wiring, TerminalStatus.CANCELLED, "session ended")
    second = build(repo, home=home, overrides=OVERRIDES, resume=first.session.session_id)
    try:
        second.runtime.backend = _Live([turn(text="ok")])
        second.runtime.run_turn("go on")
        (request,) = second.runtime.backend.seen
        assert _user_texts(request)[-1].endswith(
            "go on\n\nFrom a SessionStart hook: resumed: re-read the diff")
    finally:
        _close(second, TerminalStatus.CANCELLED, "session ended")
    assert [c["source"] for c in _stdin(fresh)] == ["startup"]
    assert [c["source"] for c in _stdin(again)] == ["resume"]


# --- the runtime: Interrupt ------------------------------------------------------


def test_an_interrupt_hook_is_told_when_a_running_task_is_stopped(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"systemMessage": "saved the log"})
    _hooks_json(home, {"Interrupt": [_group(hook.command, "ignored")]})
    wiring = _wire(repo, home, [])
    wiring.runtime.backend = _Live([_CtrlC()])
    with pytest.raises(KeyboardInterrupt):
        wiring.runtime.run_turn("explain a.py")
    assert hook.calls() == []
    outcome = wiring.runtime.interrupted()        # as the REPL does (D-062)
    assert outcome.status is TerminalStatus.CANCELLED
    (told,) = _stdin(hook)
    assert Path(told.pop("cwd")) == Path(wiring.workspace.root)
    assert told == {"session_id": wiring.session.session_id,
                    "transcript_path": str(wiring.session.path),
                    "hook_event_name": "Interrupt", "model": "fake", "turn_id": "1",
                    "permission_mode": "default"}
    assert "Interrupt hook: saved the log" in _notices(wiring)
    kinds = [r.type for r in SessionStore.read_records(wiring.session.path)
             if r.type in (EventType.HOOK, EventType.TERMINATION)]
    assert kinds == [EventType.HOOK, EventType.TERMINATION]


def test_no_interrupt_hook_for_a_task_already_ended(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"Interrupt": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="done")])
    wiring.runtime.run_turn("go")
    wiring.runtime._terminate(TerminalStatus.PARTIAL, "over budget")
    wiring.runtime.interrupted()
    assert hook.calls() == []


def test_a_cancel_from_elsewhere_runs_them_to_their_end(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"systemMessage": "noted"}, sleep=0.5)
    _hooks_json(home, {"Interrupt": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="unused")])
    wiring.runtime.cancel()            # as foundry mcp-server's client does (D-103)
    outcome = wiring.runtime.run_turn("go")
    assert outcome.status is TerminalStatus.CANCELLED
    assert "Interrupt hook: noted" in _notices(wiring)
    (hooked,) = _records(wiring, EventType.HOOK)
    assert hooked["runs"][0]["outcome"] == "completed"


def test_no_hooks_means_nothing_at_a_stop(repo, home):
    wiring = _wire(repo, home, [])
    wiring.runtime.backend = _Live([_CtrlC()])
    with pytest.raises(KeyboardInterrupt):
        wiring.runtime.run_turn("go")
    assert wiring.runtime.interrupted().status is TerminalStatus.CANCELLED
    assert _records(wiring, EventType.HOOK) == []


# --- the runtime: SessionEnd -----------------------------------------------------


def test_a_session_end_hook_is_told_as_the_session_closes(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"systemMessage": "not read"})
    _hooks_json(home, {"SessionEnd": [_group(hook.command, "other")]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("go")
    assert hook.calls() == []
    _close(wiring.wiring, TerminalStatus.PARTIAL, "session ended")
    (told,) = _stdin(hook)
    assert Path(told.pop("cwd")) == Path(wiring.workspace.root)
    assert told == {"session_id": wiring.session.session_id,
                    "transcript_path": str(wiring.session.path),
                    "hook_event_name": "SessionEnd", "reason": "other"}
    assert not any("not read" in n for n in _notices(wiring))
    records = list(SessionStore.read_records(wiring.session.path))
    assert [r.type for r in records[-2:]] == [EventType.TERMINATION, EventType.HOOK]
    assert records[-1].payload["event"] == "SessionEnd"
    assert not is_locked(wiring.session.path.parent)


def test_a_session_end_hook_of_another_reason_is_not_run(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"SessionEnd": [_group(hook.command, "logout")]})
    wiring = _wire(repo, home, [])
    _close(wiring.wiring, TerminalStatus.CANCELLED, "session ended")
    assert hook.calls() == []


def test_a_failing_session_end_hook_is_a_warning(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(stderr="could not upload", exit=1)
    _hooks_json(home, {"SessionEnd": [_group(hook.command)]})
    wiring = _wire(repo, home, [])
    _close(wiring.wiring, TerminalStatus.CANCELLED, "session ended")
    assert any(n.startswith("SessionEnd hook `") and n.endswith("failed: could not upload")
               for n in _notices(wiring))


def test_a_session_end_hook_has_a_second(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(sleep=2.5)
    _hooks_json(home, {"SessionEnd": [_group(hook.command)]})
    wiring = _wire(repo, home, [])
    _close(wiring.wiring, TerminalStatus.CANCELLED, "session ended")
    assert any(n.endswith("timed out after 1s") for n in _notices(wiring))


def test_a_cancelled_session_still_tells_its_end_hook(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(sleep=0.5)
    _hooks_json(home, {"SessionEnd": [_group(hook.command)]})
    wiring = _wire(repo, home, [])
    wiring.runtime.cancel()
    _close(wiring.wiring, TerminalStatus.CANCELLED, "session ended")
    (hooked,) = _records(wiring, EventType.HOOK)
    assert (hooked["runs"][0]["outcome"], hooked["runs"][0]["exit_code"]) == ("completed", 0)


def test_a_ctrl_c_during_session_end_still_closes_the_session(repo, home, tmp_path,
                                                             monkeypatch):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"SessionEnd": [_group(hook.command)]})
    wiring = _wire(repo, home, [])

    def stopped(self, hooks, payload, *, cancelled=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(HookSet, "run", stopped)
    _close(wiring.wiring, TerminalStatus.CANCELLED, "session ended")
    assert "SessionEnd hooks stopped" in _notices(wiring)
    assert not is_locked(wiring.session.path.parent)


# --- foundry exec ----------------------------------------------------------------


def _exec_home(home, hooks: dict, monkeypatch) -> None:
    (home / "config.toml").write_text('[backend]\nbase_url = "http://127.0.0.1:9/v1"\n'
                                      'model = "fake"\nstream = false\n', encoding="utf-8")
    _hooks_json(home, hooks)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))


def test_exec_tells_the_hooks_of_a_ctrl_c_and_of_its_end(repo, home, tmp_path, monkeypatch,
                                                         capsys):
    # Each takes a while: a cancel already honoured must not cut them short.
    stop = Scripted(tmp_path / "i").answer(sleep=0.5)
    end = Scripted(tmp_path / "e").answer(sleep=0.5)
    _exec_home(home, {"Interrupt": [_group(stop.command)],
                      "SessionEnd": [_group(end.command)]}, monkeypatch)

    def ctrl_c(runtime, task, images=()):
        raise KeyboardInterrupt

    monkeypatch.setattr("foundry.cli.unattended._run_unattended", ctrl_c)
    code = app.main(["exec", "go", "--workspace", str(repo)])
    capsys.readouterr()
    assert code == EXIT_CODES[TerminalStatus.CANCELLED]
    assert [c["hook_event_name"] for c in _stdin(stop)] == ["Interrupt"]
    assert [c["hook_event_name"] for c in _stdin(end)] == ["SessionEnd"]


def test_exec_tells_no_interrupt_hook_after_its_task(repo, home, tmp_path, monkeypatch,
                                                     capsys):
    stop = Scripted(tmp_path / "i")
    _exec_home(home, {"Interrupt": [_group(stop.command)]}, monkeypatch)

    class Ended:
        status, text, summary, result_json = TerminalStatus.PARTIAL, "done", "done", None

    def ended(runtime, task, images=()):
        return Ended()

    def ctrl_c(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("foundry.cli.unattended._run_unattended", ended)
    monkeypatch.setattr("builtins.print", ctrl_c)    # as the answer is printed
    try:
        app.main(["exec", "go", "--workspace", str(repo)])
    finally:
        monkeypatch.undo()
    capsys.readouterr()
    assert stop.calls() == []


def test_a_message_from_a_start_hook_is_shown(repo, home, tmp_path):
    start = Scripted(tmp_path / "s").answer({"systemMessage": "on the release branch"})
    _hooks_json(home, {"SessionStart": [_group(start.command, statusMessage="Loading")]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("go")
    notices = _notices(wiring)
    assert "SessionStart hook: Loading" in notices
    assert "SessionStart hook: on the release branch" in notices
    assert all(not isinstance(b, TextBlock) or "release branch" not in b.text
               for m in wiring.runtime.context.history for b in m.blocks)
