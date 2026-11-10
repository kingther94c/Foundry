"""PermissionRequest hooks, as codex's (D-119): what their answers mean, and
what the runtime does with them when policy would ask the user about a call
-- allowed once without asking, denied, or left to the user."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry.core.events import ApprovalChoice, ApprovalRequest, Notice, ToolRejected
from foundry.core.hooks import Hook, HookRun, HookSet, parse_hooks, read_permission_request
from foundry.core.policy.engine import Mode
from foundry.core.redaction import default_redactor
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
    _shown,
    _wire,
    call,
    home,
    machine,
    repo,
    turn,
)

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

HOOK = Hook(event="PermissionRequest", matcher="", command="ask")
PATCH = ("*** Begin Patch\n*** Update File: a.py\n"
         "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n*** End Patch\n")


def _decision(**decision) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


ALLOW = _decision(behavior="allow")


def _read(stdout="", *, hook=HOOK, **fields):
    return read_permission_request([HookRun(
        hook, exit_code=fields.pop("exit_code", 0),
        stdout=json.dumps(stdout) if isinstance(stdout, dict) else stdout, **fields)])


def _outcome(verdict) -> str:
    (record,) = verdict.records
    return record["outcome"]


def _said(verdict) -> tuple:
    return verdict.allow, verdict.block, verdict.failures


# --- what their answers mean ------------------------------------------------------


@pytest.mark.parametrize("stdout", ["", "  yes, go ahead \n", {}, {"systemMessage": None},
                                    {"hookSpecificOutput": {"hookEventName": "PermissionRequest"}}])
def test_no_decision_leaves_it_to_the_user(stdout):
    verdict = _read(stdout)
    assert _said(verdict) == (False, [], [])
    assert _outcome(verdict) == "completed"


def test_allow_answers_for_the_user():
    verdict = _read(ALLOW)
    assert _said(verdict) == (True, [], [])
    assert _outcome(verdict) == "allowed"
    assert _said(_read(_decision(behavior="allow", interrupt=False))) == (True, [], [])


def test_deny_gives_its_message_or_codexs():
    verdict = _read(_decision(behavior="deny", message=" not on Fridays "))
    assert _said(verdict) == (False, ["not on Fridays"], [])
    assert _outcome(verdict) == "blocked"
    for decision in (_decision(behavior="deny"), _decision(behavior="deny", message="  ")):
        assert _read(decision).block == ["PermissionRequest hook denied approval"]


def test_exit_code_two_denies_with_its_stderr():
    assert _said(_read(exit_code=2, stderr=" too risky \n")) == (False, ["too risky"], [])
    assert _said(_read(ALLOW, exit_code=2, stderr="no")) == (False, ["no"], [])
    assert _read(exit_code=2).failures == [
        "`ask` exited with code 2 but did not write a reason to stderr"]
    assert not _read(exit_code=2).block


@pytest.mark.parametrize("fields, why", [
    ({"exit_code": 1}, "exited with code 1"),
    ({"exit_code": None}, "exited without a status code"),
    ({"exit_code": None, "error": "timed out after 5s"}, "timed out after 5s"),
])
def test_a_failed_hook_decides_nothing(fields, why):
    verdict = _read(ALLOW, **fields)
    assert _said(verdict) == (False, [], [f"`ask` {why}"])


@pytest.mark.parametrize("answer, why", [
    ({**ALLOW, "continue": False}, "returned unsupported continue:false"),
    ({**ALLOW, "stopReason": "x"}, "returned unsupported stopReason"),
    ({**ALLOW, "suppressOutput": True}, "returned unsupported suppressOutput"),
    (_decision(behavior="allow", updatedInput={"command": "x"}),
     "returned unsupported updatedInput"),
    (_decision(behavior="deny", updatedPermissions=[{"type": "x"}]),
     "returned unsupported updatedPermissions"),
    (_decision(behavior="allow", interrupt=True), "returned unsupported interrupt:true"),
])
def test_what_codex_does_not_act_on_fails_closed(answer, why):
    verdict = _read(answer)
    assert _said(verdict) == (False, [], [f"`ask` {why}"])
    assert _outcome(verdict) == "failed"


@pytest.mark.parametrize("stdout", [
    _decision(behavior="ask"), _decision(message="x"), _decision(behavior="allow", message=1),
    _decision(behavior="allow", interrupt="yes"),
    {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": "allow"}},
    {"hookSpecificOutput": ["allow"]}, {**ALLOW, "systemMessage": 1}, "[1]", "{broken"])
def test_an_answer_of_the_wrong_shape_is_a_failure(stdout):
    verdict = _read(stdout)
    assert _said(verdict) == (False, [], ["`ask` returned invalid PermissionRequest JSON output"])


def test_a_message_for_the_user_and_an_async_hook_never_decides():
    verdict = _read({**ALLOW, "systemMessage": "approved by the script"})
    assert (verdict.allow, verdict.messages) == (True, ["approved by the script"])
    background = Hook(event="PermissionRequest", matcher="", command="ask", background=True)
    for answer in (ALLOW, _decision(behavior="deny", message="no"), {**ALLOW, "continue": False}):
        verdict = _read({**answer, "systemMessage": "m"}, hook=background)
        assert _said(verdict) == (False, [], []) and verdict.messages == ["m"]


def test_a_denial_outranks_an_allow():
    verdict = read_permission_request([
        HookRun(HOOK, exit_code=0, stdout=json.dumps(ALLOW)),
        HookRun(HOOK, exit_code=0, stdout=json.dumps(_decision(behavior="deny", message="no"))),
        HookRun(HOOK, exit_code=0, stdout=json.dumps(ALLOW))])
    assert verdict.block == ["no"]
    assert [r["outcome"] for r in verdict.records] == ["allowed", "blocked", "allowed"]


def test_they_are_matched_on_the_tool_as_a_pretooluse_hook_is():
    hooks = HookSet(parse_hooks({"PermissionRequest": [
        _group("shell", "Bash"), _group("edit", "Edit"), _group("ours", "run_command"),
        _group("any"), _group("mcp", "mcp__.*")]}, "f"))
    names = lambda *tool: [h.command for h in hooks.matching("PermissionRequest", tool)]
    assert names("Bash", "run_command") == ["shell", "ours", "any"]
    assert names("apply_patch", "Edit", "Write") == ["edit", "any"]
    assert names("mcp__fs__write") == ["any", "mcp"]


def test_slash_hooks_lists_them(repo, home):
    _hooks_json(home, {"PermissionRequest": [_group("ok.cmd", "Bash")]})
    assert _shown(_wire(repo, home, [])).splitlines() == [
        f"PermissionRequest Bash  ok.cmd ({home / 'hooks.json'}, 600s)"]


# --- at a call policy would ask about ----------------------------------------------


def _asked(repo, home, hook, *, matcher="Bash", approve=ApprovalChoice.ONCE, calls=None,
           overrides=None):
    _hooks_json(home, {"PermissionRequest": [_group(hook.command, matcher)]})
    wiring = _wire(repo, home, [turn(*(calls or [call("c1", "run_command", MAKE)])),
                                turn(text="ok")], approve=approve, overrides=overrides)
    wiring.runtime.run_turn("make it")
    return wiring


def test_an_allow_runs_the_call_without_asking(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(ALLOW)
    wiring = _asked(repo, home, hook)
    assert _made(repo).exists()
    assert wiring.asked == []
    assert not any(isinstance(e, ApprovalRequest) for e in wiring.seen)
    (approval,) = _records(wiring, EventType.APPROVAL)
    assert approval == {"call_id": "c1", "display": approval["display"], "choice": "once",
                        "hook": "PermissionRequest"}
    assert f"PermissionRequest hook approved: {approval['display']}" in _notices(wiring)
    (record,) = _records(wiring, EventType.HOOK)
    assert (record["event"], record["call_id"], record["runs"][0]["outcome"]) == (
        "PermissionRequest", "c1", "allowed")
    assert not _result(wiring, "c1").is_error


def test_a_hook_is_told_what_codexs_permission_request_is(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    wiring = _asked(repo, home, hook)
    (told,) = [c["stdin"] for c in hook.calls()]
    assert Path(told.pop("cwd")) == Path(wiring.workspace.root)
    description = told["tool_input"].pop("description")
    assert description and description == wiring.asked[0].reason
    assert told.pop("permission_reason") == description
    assert told == {
        "session_id": wiring.session.session_id,
        "transcript_path": str(wiring.session.path),
        "hook_event_name": "PermissionRequest", "model": "fake", "turn_id": "1",
        "permission_mode": "default", "tool_name": "Bash",
        "tool_input": {**MAKE, "cwd": ".", "timeout_s": 120}}


def test_a_denial_refuses_the_call_without_asking(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_decision(behavior="deny", message="ask Dana first"))
    wiring = _asked(repo, home, hook)
    assert not _made(repo).exists()
    assert wiring.asked == []
    result = _result(wiring, "c1")
    assert result.is_error
    assert result.content == "denied by a PermissionRequest hook: ask Dana first"
    (approval,) = _records(wiring, EventType.APPROVAL)
    assert (approval["choice"], approval["hook"]) == ("deny", "PermissionRequest")
    (rejected,) = [e for e in wiring.seen if isinstance(e, ToolRejected)]
    assert (rejected.reason, rejected.rule_id) == ("ask Dana first", "hook.PermissionRequest")
    (decision,) = _records(wiring, EventType.POLICY_DECISION)
    assert decision["verdict"] == "ask"        # policy's own word stands in the journal


def test_no_decision_asks_the_user(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"systemMessage": "no opinion"})
    wiring = _asked(repo, home, hook)
    assert len(wiring.asked) == 1 and _made(repo).exists()
    (approval,) = _records(wiring, EventType.APPROVAL)
    assert "hook" not in approval and approval["choice"] == "once"
    assert "PermissionRequest hook: no opinion" in _notices(wiring)
    wiring = _asked(repo, home, hook, approve=ApprovalChoice.DENY,
                    calls=[call("c2", "run_command", {"command": "Remove-Item made.txt"})])
    assert _result(wiring, "c2").content == "the user declined this operation"


def test_a_failing_hook_is_a_warning_and_the_user_is_asked(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(ALLOW, exit=1)
    wiring = _asked(repo, home, hook)
    assert len(wiring.asked) == 1
    assert any(text.endswith("exited with code 1") for text in _notices(wiring))


def test_an_allow_is_for_this_call_only(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(ALLOW)
    again = {"command": "New-Item -ItemType File -Path made.txt -Force | Out-Null"}
    wiring = _asked(repo, home, hook, calls=[call("c1", "run_command", MAKE),
                                             call("c2", "run_command", again)])
    assert len(hook.calls()) == 2 and wiring.asked == []
    hook.answer("")
    wiring.runtime.backend.turns.extend([turn(call("c3", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("once more")
    assert len(wiring.asked) == 1           # no grant for the session was made


def test_a_hook_is_not_asked_what_policy_decides(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(ALLOW)
    wiring = _asked(repo, home, hook, matcher="", calls=[
        call("c1", "read_file", {"path": "a.py"}),
        call("c2", "run_command", {"command": "git reset --hard"})])
    assert hook.calls() == []
    assert _result(wiring, "c2").content.startswith("blocked by policy:")


def test_an_unattended_run_asks_no_hook_either(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(ALLOW)
    wiring = _asked(repo, home, hook, approve=None)
    assert hook.calls() == [] and not _made(repo).exists()
    assert "this run is unattended, so nobody could be asked" in _result(wiring, "c1").content


def test_a_call_a_pretooluse_hook_refused_is_never_asked_about(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(ALLOW)
    pre = Scripted(tmp_path / "p").answer({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "no"}})
    _hooks_json(home, {"PreToolUse": [_group(pre.command)],
                       "PermissionRequest": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert hook.calls() == [] and not _made(repo).exists()


def test_a_patch_is_asked_about_as_edit(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(ALLOW)
    wiring = _asked(repo, home, hook, matcher="Edit", calls=[
        call("r", "read_file", {"path": "a.py"}), call("c1", "apply_patch", {"patch": PATCH})])
    (told,) = [c["stdin"] for c in hook.calls()]
    assert (told["tool_name"], told["tool_input"]["command"]) == ("apply_patch", PATCH)
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert wiring.asked == []


def test_one_denial_among_allows_refuses(repo, home, tmp_path):
    yes = Scripted(tmp_path / "y").answer(ALLOW)
    no = Scripted(tmp_path / "n").answer(stderr="not here", exit=2)
    _hooks_json(home, {"PermissionRequest": [_group(yes.command), _group(no.command),
                                             _group(yes.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert not _made(repo).exists() and wiring.asked == []
    assert _result(wiring, "c1").content == "denied by a PermissionRequest hook: not here"


def test_a_secret_in_the_call_or_the_denial_is_scrubbed(repo, home, tmp_path):
    secret = "sk-permtest-0123456789abcdef"
    default_redactor().register(secret)
    hook = Scripted(tmp_path / "h").answer(_decision(behavior="deny", message=f"saw {secret}"))
    wiring = _asked(repo, home, hook, calls=[
        call("c1", "run_command", {"command": f"New-Item -Path {secret}.txt"})])
    assert secret not in json.dumps(hook.calls()[0]["stdin"])
    assert secret not in _result(wiring, "c1").content
    assert secret not in wiring.session.path.read_text(encoding="utf-8")
    (rejected,) = [e for e in wiring.seen if isinstance(e, ToolRejected)]
    assert rejected.reason == "saw [redacted]"


def test_accept_edits_asks_no_hook_about_an_edit(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_decision(behavior="deny", message="no"))
    _asked(repo, home, hook, matcher="", overrides={"mode": Mode.ACCEPT_EDITS}, calls=[
        call("r", "read_file", {"path": "a.py"}), call("c1", "apply_patch", {"patch": PATCH})])
    assert hook.calls() == []
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"


def test_no_hooks_change_nothing_at_an_approval(repo, home):
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert len(wiring.asked) == 1
    assert _records(wiring, EventType.HOOK) == []
    assert not any(isinstance(e, Notice) and "PermissionRequest" in e.text for e in wiring.seen)
