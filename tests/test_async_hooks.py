"""Background (``async``) hooks, as codex's (D-120): started and not waited
for, heard from at the next safe point -- before the task's next model
request, or with the next prompt -- never deciding, and ended when the
session closes."""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

from foundry.core.events import ApprovalChoice
from foundry.core.hooks import HookSet, parse_hooks
from foundry.core.resume import read_journal
from foundry.core.session import EventType
from foundry.core.winapi import IS_WINDOWS
from test_hooks import (  # noqa: F401  (fixtures)
    MAKE,
    Scripted,
    _group,
    _hooks_json,
    _made,
    _notices,
    _records,
    _result,
    _wire,
    call,
    home,
    machine,
    repo,
    turn,
)
from test_prompt_stop_hooks import _user_texts

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

# A hook that waits until its gate is opened, then logs what it was given
# and answers as its spec file says: a test decides when it ends.
GATED_SCRIPT = textwrap.dedent("""\
    import json, os, sys, time
    log, spec, gate = sys.argv[1], sys.argv[2], sys.argv[3]
    data = sys.stdin.read()
    deadline = time.monotonic() + 60
    while not os.path.exists(gate) and time.monotonic() < deadline:
        time.sleep(0.05)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"stdin": json.loads(data)}) + "\\n")
    answer = json.load(open(spec, encoding="utf-8"))
    sys.stdout.write(answer.get("stdout", ""))
    sys.stderr.write(answer.get("stderr", ""))
    sys.exit(answer.get("exit", 0))
""")


class Gated(Scripted):
    def __init__(self, folder: Path, name: str = "gated") -> None:
        super().__init__(folder, name)
        self.script.write_text(GATED_SCRIPT, encoding="utf-8", newline="\n")
        self.gate = folder / f"{name}.gate"

    @property
    def command(self) -> str:
        return f'"{sys.executable}" "{self.script}" "{self.log}" "{self.spec}" "{self.gate}"'

    def open(self) -> None:
        self.gate.write_text("", encoding="utf-8")


def _note(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def _async(command: str, matcher: str | None = None, **handler) -> dict:
    return _group(command, matcher, **{"async": True}, **handler)


class _Settling:
    """The scripted model, which opens a gated hook and waits for the
    background hooks to end before it answers request ``at``."""

    def __init__(self, inner, runtime, hook: Gated, at: int) -> None:
        self.inner, self.runtime, self.hook, self.at = inner, runtime, hook, at

    @property
    def seen(self):
        return self.inner.seen

    def capabilities(self):
        return self.inner.capabilities()

    def stream_turn(self, request):
        if len(self.inner.seen) == self.at:
            self.hook.open()
            assert self.runtime.hooks.settle(30)
        return self.inner.stream_turn(request)


def _async_records(wiring) -> list[dict]:
    return [r for r in _records(wiring, EventType.HOOK) if r.get("async")]


# --- what is read ---------------------------------------------------------------


def test_a_sessionend_hook_runs_in_step_whatever_it_says():
    (end, other) = parse_hooks({"SessionEnd": [_async("e")], "Stop": [_async("s")]}, "f")
    assert (end.background, other.background) == (False, True)


# --- not waited for, heard from later -------------------------------------------------


def test_the_task_does_not_wait_and_the_note_goes_with_the_next_prompt(repo, home, tmp_path):
    hook = Gated(tmp_path / "h").answer(_note("PostToolUse", "the build log is in out.txt"))
    _hooks_json(home, {"PostToolUse": [_async(hook.command, "Bash")]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done"),
                                turn(text="ok")])
    assert wiring.runtime.run_turn("make it").text == "done"     # the gate is shut
    assert hook.calls() == [] and _made(repo).exists()
    assert _records(wiring, EventType.HOOK) == []                 # nothing to say yet
    assert "From a PostToolUse hook" not in _result(wiring, "c1").content
    hook.open()
    assert wiring.runtime.hooks.settle(30)
    wiring.runtime.run_turn("next")
    assert _user_texts(wiring.runtime.backend.seen[-1])[-1] == (
        "next\n\nFrom a PostToolUse hook: the build log is in out.txt")
    (heard,) = _async_records(wiring)
    assert heard["event"] == "PostToolUse" and "call_id" not in heard
    assert heard["runs"][0]["outcome"] == "completed"
    (told,) = [c["stdin"] for c in hook.calls()]
    assert told["tool_use_id"] == "c1" and told["tool_name"] == "Bash"


def test_in_a_task_the_note_goes_to_its_next_request(repo, home, tmp_path):
    hook = Gated(tmp_path / "h").answer(_note("PostToolUse", "lint found 2 warnings"))
    _hooks_json(home, {"PostToolUse": [_async(hook.command, "Bash")]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)),
                                turn(call("c2", "read_file", {"path": "a.py"})),
                                turn(text="done")])
    backend = _Settling(wiring.runtime.backend, wiring.runtime, hook, at=1)
    wiring.runtime.backend = backend
    wiring.runtime.run_turn("make it")
    note = "From a PostToolUse hook: lint found 2 warnings"
    assert note not in _user_texts(backend.seen[1])               # it had not ended
    assert _user_texts(backend.seen[2])[-1] == note               # after c2's result
    inputs = [r for r in _records(wiring, EventType.USER_INPUT) if r.get("hook")]
    assert inputs == [{"text": note, "hook": "PostToolUse"}]
    wiring.session.close()
    assert any(note in m.text_content for m in read_journal(wiring.session.path).history)


def test_what_it_says_for_the_user_is_said_when_it_is_heard(repo, home, tmp_path):
    talks = Gated(tmp_path / "t", "t").answer({"systemMessage": "coverage fell 2%"})
    fails = Gated(tmp_path / "f", "f").answer(stderr="no quota", exit=1)
    _hooks_json(home, {"PostToolUse": [_async(talks.command), _async(fails.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done"),
                                turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert _notices(wiring) == []
    talks.open(), fails.open()
    assert wiring.runtime.hooks.settle(30)
    assert _notices(wiring) == []          # not until a safe point
    wiring.runtime.run_turn("next")
    notices = _notices(wiring)
    assert "PostToolUse hook: coverage fell 2%" in notices
    assert any(text.endswith("exited with code 1") for text in notices)
    (heard,) = _async_records(wiring)
    assert sorted(r["outcome"] for r in heard["runs"]) == ["completed", "failed"]


def test_an_async_hook_never_decides(repo, home, tmp_path):
    deny = Scripted(tmp_path / "d", "d").answer({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "no"}})
    allow = Scripted(tmp_path / "a", "a").answer({"hookSpecificOutput": {
        "hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})
    _hooks_json(home, {"PreToolUse": [_async(deny.command)],
                       "PermissionRequest": [_async(allow.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done")])
    wiring.runtime.run_turn("make it")
    assert _made(repo).exists()
    assert len(wiring.asked) == 1          # the user was asked, not the hook
    assert wiring.runtime.hooks.settle(30)


def test_a_waited_hook_and_a_background_one_of_one_event(repo, home, tmp_path):
    now = Scripted(tmp_path / "n", "n").answer(_note("PostToolUse", "now"))
    later = Gated(tmp_path / "l", "l").answer(_note("PostToolUse", "later"))
    _hooks_json(home, {"PostToolUse": [_group(now.command), _async(later.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done"),
                                turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert _result(wiring, "c1").content.endswith("From a PostToolUse hook: now")
    (record,) = _records(wiring, EventType.HOOK)
    assert len(record["runs"]) == 1 and not record.get("async")
    later.open()
    assert wiring.runtime.hooks.settle(30)
    wiring.runtime.run_turn("next")
    assert _user_texts(wiring.runtime.backend.seen[-1])[-1].endswith(
        "From a PostToolUse hook: later")


def test_a_refused_prompt_leaves_what_was_said_for_the_next(repo, home, tmp_path):
    hook = Gated(tmp_path / "h").answer(_note("PostToolUse", "kept"))
    gate = Scripted(tmp_path / "u", "u").answer({"decision": "block", "reason": "not yet"})
    _hooks_json(home, {"PostToolUse": [_async(hook.command)],
                       "UserPromptSubmit": [_group(gate.command)]})
    gate.answer("")
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done"),
                                turn(text="ok")])
    wiring.runtime.run_turn("make it")
    hook.open()
    assert wiring.runtime.hooks.settle(30)
    gate.answer({"decision": "block", "reason": "not yet"})
    assert wiring.runtime.run_turn("refused").text == (
        "a UserPromptSubmit hook stopped this prompt, so it was not sent: not yet")
    assert _async_records(wiring) == []
    gate.answer(_note("UserPromptSubmit", "fresh"))
    wiring.runtime.run_turn("sent")
    # What was said before the prompt comes before what its own hooks say.
    assert _user_texts(wiring.runtime.backend.seen[-1])[-1] == (
        "sent\n\nFrom a PostToolUse hook: kept\n\nFrom a UserPromptSubmit hook: fresh")


def test_a_status_message_is_shown_as_it_starts(repo, home, tmp_path):
    hook = Gated(tmp_path / "h")
    _hooks_json(home, {"PostToolUse": [_async(hook.command, statusMessage="scanning")]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done")])
    wiring.runtime.run_turn("make it")
    assert "PostToolUse hook: scanning" in _notices(wiring)
    assert not wiring.runtime.hooks.settle(0.2)       # still running
    hook.open()
    assert wiring.runtime.hooks.settle(30)


# --- the session's close ------------------------------------------------------------


def test_the_close_ends_what_still_runs_and_waits_for_sessionend(repo, home, tmp_path):
    hook = Gated(tmp_path / "h").answer(_note("PostToolUse", "never heard"))
    end = Scripted(tmp_path / "e", "e")
    _hooks_json(home, {"PostToolUse": [_async(hook.command)],
                       "SessionEnd": [_async(end.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done")])
    wiring.runtime.run_turn("make it")
    started = time.monotonic()
    wiring.runtime.end_hooks()
    assert time.monotonic() - started < 20
    assert len(end.calls()) == 1                                  # run in step
    assert wiring.runtime.hooks.settle(0)                         # ended, not left
    assert wiring.runtime.hooks.finished() == []
    hook.open()
    time.sleep(0.5)
    assert hook.calls() == []                                     # it never went on


def test_a_hookset_hands_over_each_run_once():
    hooks = HookSet(parse_hooks({"Stop": [_async(f'"{sys.executable}" -c "print(1)"')]}, "f"),
                    cwd=Path.cwd())
    assert hooks.run(list(hooks.hooks), {"hook_event_name": "Stop"}) == []
    assert hooks.settle(30)
    (run,) = hooks.finished()
    assert (run.exit_code, run.stdout.strip()) == (0, "1")
    assert hooks.finished() == []
    # A stopped task's cancel stops nothing that runs in the background.
    assert hooks.run(list(hooks.hooks), {"hook_event_name": "Stop"},
                     cancelled=lambda: True) == []
    assert hooks.settle(30) and len(hooks.finished()) == 1
    hooks.close()
    hooks.run(list(hooks.hooks), {"hook_event_name": "Stop"})
    assert hooks.settle(30) and hooks.finished() == []    # a closed set keeps nothing


def test_no_background_hooks_change_nothing(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_note("PostToolUse", "now"))
    _hooks_json(home, {"PostToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="done")],
                   approve=ApprovalChoice.ONCE)
    wiring.runtime.run_turn("make it")
    assert _async_records(wiring) == []
    assert all(not r.get("hook") for r in _records(wiring, EventType.USER_INPUT))
    assert json.dumps(_records(wiring, EventType.HOOK)).count("PostToolUse") == 1
