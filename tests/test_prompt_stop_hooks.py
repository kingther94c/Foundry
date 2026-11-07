"""UserPromptSubmit and Stop hooks, as codex's (D-116): what their answers
mean, and what the runtime does with them -- a prompt stopped before the
model sees it, or sent with a note; a task sent on when the model is done,
at a plain answer or at ``finish``."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from foundry.core.conversation import Role, TextBlock
from foundry.core.events import TerminalStatus
from foundry.core.hooks import (
    CHARS_PER_TOKEN,
    DEFAULT_CONTEXT_TOKENS,
    Hook,
    HookRun,
    HookSet,
    parse_hooks,
    read_post_tool_use,
    read_pre_tool_use,
    read_stop,
    read_user_prompt_submit,
)
from foundry.core.redaction import default_redactor
from foundry.core.resume import read_journal
from foundry.core.session import EventType
from foundry.core.winapi import IS_WINDOWS
from test_hooks import (  # noqa: F401  (fixtures)
    Scripted,
    _group,
    _hooks_json,
    _notices,
    _records,
    _result,
    _shown,
    _wire,
    call,
    home,
    machine,
    repo,
    turn,
)
from test_resume import finish

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

UPS = Hook(event="UserPromptSubmit", matcher="", command="ups")
STOP = Hook(event="Stop", matcher="", command="stop")
ASYNC_UPS = Hook(event="UserPromptSubmit", matcher="", command="ups", background=True)
ASYNC_STOP = Hook(event="Stop", matcher="", command="stop", background=True)
LIMIT = DEFAULT_CONTEXT_TOKENS * CHARS_PER_TOKEN

# codex's own example of a Stop hook: send the task on once, then let it end.
STOP_ONCE = textwrap.dedent("""\
    import json, sys
    data = json.loads(sys.stdin.read())
    with open(sys.argv[1], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(data) + "\\n")
    if not data["stop_hook_active"]:
        print(json.dumps({"decision": "block", "reason": sys.argv[2]}))
""")


class StopOnce:
    def __init__(self, folder: Path, reason: str = "run the tests") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.script = folder / "stop_once.py"
        self.script.write_text(STOP_ONCE, encoding="utf-8", newline="\n")
        self.log = folder / "stop.log"
        self.reason = reason

    @property
    def command(self) -> str:
        return f'"{sys.executable}" "{self.script}" "{self.log}" "{self.reason}"'

    def calls(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


def _read(reader, hook: Hook, stdout="", **fields):
    return reader([HookRun(hook, exit_code=fields.pop("exit_code", 0),
                           stdout=json.dumps(stdout) if isinstance(stdout, dict) else stdout,
                           **fields)])


def _ups(stdout="", *, hook=UPS, **fields):
    return _read(read_user_prompt_submit, hook, stdout, **fields)


def _stop(stdout="", *, hook=STOP, **fields):
    return _read(read_stop, hook, stdout, **fields)


def _outcome(verdict) -> str:
    (record,) = verdict.records
    return record["outcome"]


def _note(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}


def _user_texts(request) -> list[str]:
    return [block.text for message in request.messages if message.role is Role.USER
            for block in message.blocks if isinstance(block, TextBlock)]


def _told(hook) -> list[dict]:
    return [c["stdin"] if "stdin" in c else c for c in hook.calls()]


# --- matching and listing -----------------------------------------------------


def test_prompt_and_stop_hooks_run_whatever_their_matcher():
    hooks = HookSet(parse_hooks({
        "UserPromptSubmit": [_group("u", "Bash")], "Stop": [_group("s", "^nothing$")],
        "PreToolUse": [_group("p", "Bash")]}, "f"))
    assert [h.command for h in hooks.matching("UserPromptSubmit", ())] == ["u"]
    assert [h.command for h in hooks.matching("Stop", ())] == ["s"]
    assert hooks.matching("PreToolUse", ()) == []
    assert [h.command for h in hooks.matching("PreToolUse", ("Bash",))] == ["p"]


def test_slash_hooks_lists_them_without_a_matcher(repo, home):
    _hooks_json(home, {"UserPromptSubmit": [_group("u.cmd", "Bash")],
                       "Stop": [_group("s.cmd", **{"async": True})]})
    assert _shown(_wire(repo, home, [])).splitlines() == [
        f"UserPromptSubmit  u.cmd ({home / 'hooks.json'}, 600s)",
        f"Stop (async)  s.cmd ({home / 'hooks.json'}, 600s)"]


# --- what a UserPromptSubmit hook's answer means --------------------------------


def test_a_prompt_hook_that_prints_nothing_says_nothing():
    verdict = _ups("")
    assert (verdict.block, verdict.stops, verdict.context, verdict.failures) == ([], [], [], [])
    assert _outcome(verdict) == "completed"


def test_plain_text_from_a_prompt_hook_is_a_note():
    verdict = _ups("  the tests are in tests/ \n")
    assert verdict.context == ["the tests are in tests/"]
    assert not verdict.failures and _outcome(verdict) == "completed"


def test_a_prompt_hooks_note_is_cut_at_its_limit():
    hook = Hook(event="UserPromptSubmit", matcher="", command="ups", context_tokens=2)
    (note,) = _ups("x" * 20, hook=hook).context
    assert note == ("x" * 8 + "\n[... cut: the hook gave 20 characters, more than its "
                    "additionalContextLimit of about 2 tokens]")
    (note,) = _ups(_note("y" * 20), hook=hook).context
    assert note.startswith("y" * 8 + "\n[... cut")


def test_additional_context_and_a_system_message():
    verdict = _ups({"systemMessage": "heads up", **_note("check first")})
    assert (verdict.context, verdict.messages, verdict.block) == (["check first"],
                                                                  ["heads up"], [])


def test_block_with_a_reason_stops_the_prompt():
    verdict = _ups({"decision": "block", "reason": " not on main "})
    assert verdict.block == ["not on main"] and _outcome(verdict) == "blocked"


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_block_without_a_reason_is_a_failure_and_drops_its_note(reason):
    answer = {"decision": "block", **_note("note")}
    if reason is not None:
        answer["reason"] = reason
    verdict = _ups(answer)
    assert (verdict.block, verdict.context) == ([], [])
    assert verdict.failures == ["`ups` returned decision:block without a non-empty reason"]
    assert _outcome(verdict) == "failed"


def test_continue_false_stops_the_prompt_with_its_stop_reason():
    verdict = _ups({"continue": False, "stopReason": " paused "})
    assert (verdict.stops, verdict.block, _outcome(verdict)) == (["paused"], [], "stopped")
    assert _ups({"continue": False}).stops == [""]


def test_continue_false_comes_before_a_block_without_a_reason():
    verdict = _ups({"continue": False, "decision": "block"})
    assert verdict.stops == [""] and not verdict.failures


def test_a_note_comes_with_a_stop_or_a_block():
    assert _ups({"continue": False, **_note("n")}).context == ["n"]
    assert _ups({"decision": "block", "reason": "r", **_note("n")}).context == ["n"]


@pytest.mark.parametrize("answer", [
    {"decision": "approve"}, {"decision": "deny"}, {"reason": 3},
    {"decision": "block", "reason": ["x"]}, {"hookSpecificOutput": "note"},
    {"hookSpecificOutput": {"additionalContext": 1}}, {"continue": "no"},
    {"systemMessage": 1}, {"stopReason": False},
])
def test_an_answer_of_the_wrong_shape_is_a_failure(answer):
    verdict = _ups(answer)
    assert verdict.failures == ["`ups` returned invalid UserPromptSubmit JSON output"]
    assert not (verdict.block or verdict.stops or verdict.context or verdict.messages)
    assert _outcome(verdict) == "failed"


@pytest.mark.parametrize("stdout", ['["a"]', "{not json", "  [1, 2]"])
def test_json_that_is_not_an_object_is_a_failure_not_a_note(stdout):
    verdict = _ups(stdout)
    assert verdict.failures == ["`ups` returned invalid UserPromptSubmit JSON output"]
    assert verdict.context == []


def test_exit_code_2_with_a_reason_on_stderr_stops_the_prompt():
    assert _ups(exit_code=2, stderr=" no \n").block == ["no"]
    verdict = _ups(exit_code=2, stderr="  ")
    assert verdict.block == []
    assert verdict.failures == ["`ups` exited with code 2 but did not write a reason to stderr"]


def test_other_codes_and_errors_are_failures():
    assert _ups(exit_code=1).failures == ["`ups` exited with code 1"]
    assert _ups(exit_code=None).failures == ["`ups` exited without a status code"]
    assert _ups(exit_code=None, error="timed out after 5s").failures == [
        "`ups` timed out after 5s"]
    assert _ups("note", exit_code=1).context == []


def test_an_async_prompt_hook_informs_and_never_stops():
    verdict = _ups({"decision": "block", "reason": "no", **_note("n")}, hook=ASYNC_UPS)
    assert (verdict.block, verdict.context, _outcome(verdict)) == ([], ["n"], "completed")
    assert _ups({"continue": False}, hook=ASYNC_UPS).stops == []
    verdict = _ups({"decision": "block", **_note("n")}, hook=ASYNC_UPS)
    assert (verdict.context, verdict.failures) == (["n"], [])
    verdict = _ups(exit_code=2, stderr="no", hook=ASYNC_UPS)
    assert (verdict.block, verdict.failures) == ([], ["`ups` exited with code 2"])
    assert _ups("plain", hook=ASYNC_UPS).context == ["plain"]
    assert _ups({"systemMessage": "m"}, hook=ASYNC_UPS).messages == ["m"]


# --- what a Stop hook's answer means ---------------------------------------------


def test_a_stop_hook_that_prints_nothing_lets_the_task_end():
    verdict = _stop("  \n")
    assert (verdict.block, verdict.stops, verdict.failures) == ([], [], [])
    assert _outcome(verdict) == "completed"


def test_block_with_a_reason_sends_the_task_on():
    verdict = _stop({"decision": "block", "reason": " run the tests "})
    assert verdict.block == ["run the tests"] and _outcome(verdict) == "blocked"


def test_exit_code_2_sends_the_task_on_with_stderr():
    assert _stop(exit_code=2, stderr="again\n").block == ["again"]
    assert _stop(exit_code=2).failures == [
        "`stop` exited with code 2 but did not write a reason to stderr"]


def test_plain_text_from_a_stop_hook_is_a_failure():
    verdict = _stop("all good")
    assert verdict.failures == ["`stop` returned invalid Stop JSON output"]
    assert verdict.block == [] and _outcome(verdict) == "failed"


def test_plain_text_from_an_async_stop_hook_is_nothing():
    verdict = _stop("all good", hook=ASYNC_STOP)
    assert not verdict.failures and _outcome(verdict) == "completed"


def test_json_that_is_not_an_object_fails_even_from_an_async_hook():
    assert _stop("[1]", hook=ASYNC_STOP).failures == ["`stop` returned invalid Stop JSON output"]


@pytest.mark.parametrize("reason", [None, " "])
def test_a_stop_block_without_a_reason_is_a_failure(reason):
    answer = {"decision": "block"}
    if reason is not None:
        answer["reason"] = reason
    verdict = _stop(answer)
    assert verdict.block == []
    assert verdict.failures == ["`stop` returned decision:block without a non-empty reason"]


def test_continue_false_ends_the_task():
    verdict = _stop({"continue": False, "stopReason": "enough", "decision": "block",
                     "reason": "more"})
    assert (verdict.stops, verdict.block, _outcome(verdict)) == (["enough"], [], "stopped")
    assert _stop({"continue": False}).stops == [""]


def test_a_stop_hooks_system_message_is_a_warning_even_async():
    assert _stop({"systemMessage": "m"}).messages == ["m"]
    assert _stop({"systemMessage": "m"}, hook=ASYNC_STOP).messages == ["m"]


def test_a_stop_hook_gives_no_note():
    verdict = _stop({"hookSpecificOutput": {"hookEventName": "Stop",
                                            "additionalContext": "n"}})
    assert (verdict.context, verdict.failures) == ([], [])


@pytest.mark.parametrize("answer", [
    {"decision": "approve"}, {"reason": 1}, {"continue": 0}, {"systemMessage": ["m"]},
])
def test_a_stop_answer_of_the_wrong_shape_is_a_failure(answer):
    verdict = _stop(answer)
    assert verdict.failures == ["`stop` returned invalid Stop JSON output"]
    assert not (verdict.block or verdict.stops or verdict.messages)


def test_an_async_stop_hook_never_decides():
    assert _stop({"decision": "block", "reason": "more"}, hook=ASYNC_STOP).block == []
    assert _stop({"continue": False}, hook=ASYNC_STOP).stops == []
    assert _stop({"decision": "block"}, hook=ASYNC_STOP).failures == []
    verdict = _stop(exit_code=2, stderr="more", hook=ASYNC_STOP)
    assert (verdict.block, verdict.failures) == ([], ["`stop` exited with code 2"])


# --- feedback is cut at codex's default ---------------------------------------


def test_a_long_continuation_is_cut_at_codexs_default():
    (prompt,) = _stop({"decision": "block", "reason": "y" * (LIMIT + 5)}).block
    assert prompt == ("y" * LIMIT + f"\n[... cut: the hook gave {LIMIT + 5} characters, "
                      f"more than about {DEFAULT_CONTEXT_TOKENS} tokens]")
    assert _stop({"decision": "block", "reason": "y" * LIMIT}).block == ["y" * LIMIT]
    (prompt,) = _stop(exit_code=2, stderr="w" * (LIMIT + 1)).block
    assert prompt.startswith("w" * LIMIT + "\n[... cut")


def test_whatever_its_additional_context_limit():
    hook = Hook(event="Stop", matcher="", command="stop", context_tokens=0)
    (prompt,) = _stop({"decision": "block", "reason": "y" * (LIMIT + 1)}, hook=hook).block
    assert prompt.startswith("y" * LIMIT + "\n[... cut")


def test_tool_hooks_feedback_is_cut_as_well():
    post = Hook(event="PostToolUse", matcher="", command="post")
    pre = Hook(event="PreToolUse", matcher="", command="pre")
    long = "z" * (LIMIT + 1)
    for verdict in (_read(read_post_tool_use, post, {"decision": "block", "reason": long}),
                    _read(read_post_tool_use, post, {"continue": False, "reason": long}),
                    _read(read_post_tool_use, post, exit_code=2, stderr=long),
                    _read(read_pre_tool_use, pre, {"decision": "block", "reason": long})):
        (feedback,) = verdict.block
        assert feedback.startswith("z" * LIMIT + "\n[... cut")


# --- the runtime: a prompt ----------------------------------------------------


def test_a_prompt_hook_is_told_the_prompt(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command, "Bash")]})  # no matcher
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("fix the bug")
    (told,) = _told(hook)
    assert Path(told.pop("cwd")) == Path(wiring.workspace.root)
    assert told == {"session_id": wiring.session.session_id,
                    "transcript_path": str(wiring.session.path),
                    "hook_event_name": "UserPromptSubmit", "model": "fake", "turn_id": "1",
                    "permission_mode": "default", "prompt": "fix the bug"}


def test_a_prompt_hooks_notes_go_with_the_prompt(repo, home, tmp_path):
    first = Scripted(tmp_path / "a").answer(_note("the tests are in tests/"))
    second = Scripted(tmp_path / "b").answer("branch: main\n")
    _hooks_json(home, {"UserPromptSubmit": [_group(first.command), _group(second.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    outcome = wiring.runtime.run_turn("fix the bug")
    assert (outcome.status, outcome.text) == (None, "ok")
    sent = ("fix the bug\n\nFrom a UserPromptSubmit hook: the tests are in tests/"
            "\n\nFrom a UserPromptSubmit hook: branch: main")
    (request,) = wiring.runtime.backend.seen
    assert _user_texts(request)[-1] == sent
    assert _records(wiring, EventType.USER_INPUT) == [{"text": sent, "typed": "fix the bug"}]
    (hooked,) = _records(wiring, EventType.HOOK)
    assert hooked["event"] == "UserPromptSubmit" and "call_id" not in hooked
    assert [run["outcome"] for run in hooked["runs"]] == ["completed", "completed"]


def test_what_the_user_typed_is_kept_with_the_notes(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer("a note")
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("Review the diff.", typed="/review")
    assert _told(hook)[0]["prompt"] == "Review the diff."
    assert _records(wiring, EventType.USER_INPUT) == [
        {"text": "Review the diff.\n\nFrom a UserPromptSubmit hook: a note", "typed": "/review"}]


def test_a_prompt_with_no_note_is_recorded_as_before(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="ok"), turn(text="ok")])
    wiring.runtime.run_turn("go")
    wiring.runtime.run_turn("Review the diff.", typed="/review")
    assert _records(wiring, EventType.USER_INPUT) == [
        {"text": "go"}, {"text": "Review the diff.", "typed": "/review"}]


def test_a_stopped_prompt_is_never_sent(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"decision": "block", "reason": "not on a Friday",
                                            **_note("unused")})
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    outcome = wiring.runtime.run_turn("deploy")
    reason = "a UserPromptSubmit hook stopped this prompt, so it was not sent: not on a Friday"
    assert (outcome.status, outcome.text) == (TerminalStatus.BLOCKED, reason)
    assert wiring.runtime.backend.seen == []
    assert wiring.runtime.context.history == [] or all(
        "deploy" not in text for m in wiring.runtime.context.history
        for text in (b.text for b in m.blocks if isinstance(b, TextBlock)))
    assert _records(wiring, EventType.USER_INPUT) == []
    (ended,) = _records(wiring, EventType.TERMINATION)
    assert (ended["status"], ended["reason"]) == ("blocked", reason)
    (hooked,) = _records(wiring, EventType.HOOK)
    assert hooked["runs"][0]["outcome"] == "blocked"
    # The session goes on: the next prompt is the hook's to judge again.
    hook.answer("")
    assert wiring.runtime.run_turn("hello").text == "ok"
    (request,) = wiring.runtime.backend.seen
    assert _user_texts(request) == ["hello"]


@pytest.mark.parametrize("answer, said", [
    ({"continue": False, "stopReason": "paused by policy"}, ": paused by policy"),
    ({"continue": False}, ""),
])
def test_continue_false_stops_the_prompt_too(repo, home, tmp_path, answer, said):
    hook = Scripted(tmp_path / "h").answer(answer)
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    outcome = wiring.runtime.run_turn("go")
    assert outcome.status is TerminalStatus.BLOCKED
    assert outcome.text == "a UserPromptSubmit hook stopped this prompt, so it was not sent" + said
    assert wiring.runtime.backend.seen == []


def test_each_hooks_reason_is_said(repo, home, tmp_path):
    first = Scripted(tmp_path / "a").answer({"decision": "block", "reason": "one"})
    second = Scripted(tmp_path / "b").answer(stderr="two", exit=2)
    _hooks_json(home, {"UserPromptSubmit": [_group(first.command), _group(second.command)]})
    outcome = _wire(repo, home, [turn(text="ok")]).runtime.run_turn("go")
    assert outcome.text.endswith("so it was not sent: one; two")


def test_a_failing_prompt_hook_is_a_warning_and_the_prompt_goes(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer('{"decision": "approve"}')
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    assert wiring.runtime.run_turn("go").text == "ok"
    assert (f"UserPromptSubmit hook `{hook.command[:57]}...` returned invalid "
            "UserPromptSubmit JSON output") in _notices(wiring)
    (request,) = wiring.runtime.backend.seen
    assert _user_texts(request) == ["go"]
    assert _records(wiring, EventType.USER_INPUT) == [{"text": "go"}]


def test_a_prompt_hooks_message_and_status_are_shown(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"systemMessage": "remember the freeze"})
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command,
                                                   statusMessage="Checking the prompt")]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("go")
    notices = _notices(wiring)
    assert "UserPromptSubmit hook: Checking the prompt" in notices
    assert "UserPromptSubmit hook: remember the freeze" in notices


def test_a_prompt_hook_is_told_and_tells_nothing_secret(repo, home, tmp_path):
    secret = "sk-upstest-0123456789abcdef"
    default_redactor().register(secret)
    hook = Scripted(tmp_path / "h").answer({"systemMessage": f"key {secret}",
                                            **_note(f"saw {secret}")})
    _hooks_json(home, {"UserPromptSubmit": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn(f"use {secret}")
    assert secret not in json.dumps(_told(hook))
    (request,) = wiring.runtime.backend.seen
    note = _user_texts(request)[-1].split("From a UserPromptSubmit hook: ")[1]
    assert note.startswith("saw ") and secret not in note
    assert all(secret not in text for text in _notices(wiring))
    assert secret not in wiring.session.path.read_text(encoding="utf-8")


def test_no_prompt_hook_means_no_hook_record(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"PreToolUse": [_group(hook.command, "Bash")]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("go")
    assert _records(wiring, EventType.HOOK) == [] and hook.calls() == []


# --- the runtime: when the model is done ----------------------------------------


def test_a_stop_hook_sends_the_task_on_once(repo, home, tmp_path):
    hook = StopOnce(tmp_path / "s")
    _hooks_json(home, {"Stop": [_group(hook.command, "nothing-matches-this")]})
    wiring = _wire(repo, home, [turn(text="done, I think"), turn(text="tests pass")])
    outcome = wiring.runtime.run_turn("fix it")
    assert (outcome.status, outcome.text) == (None, "tests pass")
    first, second = hook.calls()
    assert Path(first.pop("cwd")) == Path(wiring.workspace.root)
    assert first == {"session_id": wiring.session.session_id,
                     "transcript_path": str(wiring.session.path),
                     "hook_event_name": "Stop", "model": "fake", "turn_id": "1",
                     "permission_mode": "default", "stop_hook_active": False,
                     "last_assistant_message": "done, I think"}
    assert (second["stop_hook_active"], second["last_assistant_message"],
            second["turn_id"]) == (True, "tests pass", "1")
    _, request = wiring.runtime.backend.seen
    assert _user_texts(request)[-1] == "From a Stop hook: run the tests"
    assert request.messages[-2].role is Role.ASSISTANT
    assert _records(wiring, EventType.USER_INPUT) == [
        {"text": "fix it"}, {"text": "From a Stop hook: run the tests", "hook": "Stop"}]
    assert "a Stop hook sent the task on: run the tests" in _notices(wiring)
    hooked = _records(wiring, EventType.HOOK)
    assert [(r["event"], [run["outcome"] for run in r["runs"]]) for r in hooked] == [
        ("Stop", ["blocked"]), ("Stop", ["completed"])]
    assert all("call_id" not in r for r in hooked)


def test_continue_false_ends_the_task_whatever_another_hook_asked(repo, home, tmp_path):
    blocker = Scripted(tmp_path / "b").answer({"decision": "block", "reason": "more"})
    stopper = Scripted(tmp_path / "s").answer({"continue": False,
                                               "stopReason": "enough for today"})
    _hooks_json(home, {"Stop": [_group(blocker.command), _group(stopper.command)]})
    wiring = _wire(repo, home, [turn(text="done"), turn(text="unused")])
    outcome = wiring.runtime.run_turn("go")
    assert (outcome.status, outcome.text) == (None, "done")
    assert len(wiring.runtime.backend.seen) == 1
    assert "Stop hook: enough for today" in _notices(wiring)
    assert len(_records(wiring, EventType.USER_INPUT)) == 1


def test_two_hooks_send_the_task_on_with_both_reasons(repo, home, tmp_path):
    first = Scripted(tmp_path / "a").answer({"decision": "block", "reason": "lint"})
    second = Scripted(tmp_path / "b").answer(stderr="test", exit=2)
    _hooks_json(home, {"Stop": [_group(first.command), _group(second.command)]})
    wiring = _wire(repo, home, [turn(text="done"), turn(text="ok")],
                   overrides={"max_tool_rounds": 1})
    wiring.runtime.run_turn("go")
    assert _records(wiring, EventType.USER_INPUT)[-1] == {
        "text": "From a Stop hook: lint\n\ntest", "hook": "Stop"}


def test_a_stop_hook_at_finish_answers_the_call_and_the_task_goes_on(repo, home, tmp_path):
    hook = StopOnce(tmp_path / "s", "check the diff")
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    wiring = _wire(repo, home, [finish("f1", status="partial"),
                                finish("f2", status="partial")])
    outcome = wiring.runtime.run_turn("fix it")
    assert outcome.status is TerminalStatus.PARTIAL
    refused = _result(wiring, "f1")
    assert refused.is_error
    assert refused.content == "finish was not accepted: a Stop hook sent the task on:\ncheck the diff"
    first, second = hook.calls()
    assert (first["stop_hook_active"], first["last_assistant_message"]) == (False, "done")
    assert second["stop_hook_active"] is True
    assert len(wiring.runtime.backend.seen) == 2
    assert len(_records(wiring, EventType.TERMINATION)) == 1
    assert [r["call_id"] for r in _records(wiring, EventType.TOOL_REPLY)] == ["f1"]


def test_a_call_after_a_refused_finish_still_runs(repo, home, tmp_path):
    hook = StopOnce(tmp_path / "s")
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    done = finish("f1", status="partial").tool_calls[0]
    wiring = _wire(repo, home, [turn(done, call("r1", "read_file", {"path": "a.py"})),
                                finish("f2", status="partial")])
    wiring.runtime.run_turn("go")
    assert "A = 1" in _result(wiring, "r1").content
    assert _result(wiring, "f1").is_error
    # The refused finish is spent: the task ends at the next one.
    assert len(wiring.runtime.backend.seen) == 2
    assert [t["stop_hook_active"] for t in hook.calls()] == [False, True]


def test_the_budget_still_bounds_a_stop_hook_that_never_lets_go(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"decision": "block", "reason": "again"})
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text=t) for t in "abcd"],
                   overrides={"max_tool_rounds": 2})
    outcome = wiring.runtime.run_turn("go")
    assert outcome.status is TerminalStatus.PARTIAL
    assert "rounds exceeded (2)" in outcome.text
    assert len(wiring.runtime.backend.seen) == 2


def test_a_failing_stop_hook_lets_the_task_end(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer("not json")
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="done"), turn(text="unused")])
    assert wiring.runtime.run_turn("go").text == "done"
    assert len(wiring.runtime.backend.seen) == 1
    assert any(n.endswith("returned invalid Stop JSON output") for n in _notices(wiring))


def test_a_stop_hook_is_told_null_for_no_message(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    _wire(repo, home, [turn(text="")]).runtime.run_turn("go")
    assert _told(hook)[0]["last_assistant_message"] is None


def test_a_stop_hook_does_not_run_while_calls_are_made(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    _wire(repo, home, [turn(call("r1", "read_file", {"path": "a.py"})),
                       turn(text="ok")]).runtime.run_turn("go")
    (told,) = _told(hook)
    assert told["last_assistant_message"] == "ok"


def test_each_hook_of_a_task_is_told_its_turn_id(repo, home, tmp_path):
    prompt, pre, stop = (Scripted(tmp_path / n) for n in ("u", "p", "s"))
    _hooks_json(home, {"UserPromptSubmit": [_group(prompt.command)],
                       "PreToolUse": [_group(pre.command)], "Stop": [_group(stop.command)]})
    wiring = _wire(repo, home, [turn(call("r1", "read_file", {"path": "a.py"})),
                                turn(call("r2", "read_file", {"path": "a.py"})),
                                turn(text="ok"), turn(text="again")])
    wiring.runtime.run_turn("one")
    wiring.runtime.run_turn("two")
    assert [t["turn_id"] for t in _told(prompt)] == ["1", "4"]
    assert [t["turn_id"] for t in _told(pre)] == ["1", "1"]
    assert [t["turn_id"] for t in _told(stop)] == ["1", "4"]


def test_a_stop_hooks_prompt_never_sent_is_kept_for_a_resume(repo, home, tmp_path):
    hook = StopOnce(tmp_path / "s")
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="first")], overrides={"max_tool_rounds": 1})
    outcome = wiring.runtime.run_turn("go")
    assert outcome.status is TerminalStatus.PARTIAL
    wiring.session.close()
    last = read_journal(wiring.session.path).history[-1]
    assert last.role is Role.USER
    assert last.blocks[-1].text == "From a Stop hook: run the tests"


def test_a_stop_hooks_message_and_status_are_shown(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"systemMessage": "3 files changed"})
    _hooks_json(home, {"Stop": [_group(hook.command, statusMessage="Checking the work")]})
    wiring = _wire(repo, home, [turn(text="ok")])
    wiring.runtime.run_turn("go")
    notices = _notices(wiring)
    assert "Stop hook: Checking the work" in notices
    assert "Stop hook: 3 files changed" in notices


def test_a_stop_hooks_prompt_is_scrubbed(repo, home, tmp_path):
    secret = "sk-stoptest-0123456789abcdef"
    default_redactor().register(secret)
    hook = StopOnce(tmp_path / "s", f"use {secret}")
    _hooks_json(home, {"Stop": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(text="done"), turn(text="ok")])
    wiring.runtime.run_turn("go")
    _, request = wiring.runtime.backend.seen
    assert _user_texts(request)[-1].startswith("From a Stop hook: use ")
    assert secret not in _user_texts(request)[-1]
    assert all(secret not in text for text in _notices(wiring))
    assert secret not in wiring.session.path.read_text(encoding="utf-8")
