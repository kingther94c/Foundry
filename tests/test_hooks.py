"""Lifecycle hooks, as codex's (D-115): where they are read, how they are
matched and run, what their answers mean, and what the runtime does with them
at a tool call -- PreToolUse before policy, PostToolUse before the reply."""

from __future__ import annotations

import io
import json
import re
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest
from rich.console import Console

from foundry.cli.wiring import build
from foundry.cli.repl import show_hooks
from foundry.core.backends.replay import ScriptedBackend
from foundry.core.config import load_config, parse_settings
from foundry.core.conversation import ModelTurn, StopReason, ToolResultBlock, ToolUseBlock, Usage
from foundry.core.errors import ConfigError
from foundry.core.events import ApprovalChoice, Notice, ToolRejected
from foundry.core.hooks import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_TIMEOUT_S,
    Hook,
    HookRun,
    HookSet,
    hook_tool_input,
    matcher_names,
    parse_hooks,
    read_hooks_file,
    read_post_tool_use,
    read_pre_tool_use,
    rewritten_args,
)
from foundry.core.policy.engine import Mode
from foundry.core.redaction import default_redactor
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.command import run_program
from foundry.core.tools.git import run_git
from foundry.core.winapi import IS_WINDOWS

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

OVERRIDES = {"base_url": "http://127.0.0.1:9/v1", "model": "fake", "stream": False}

# A hook that logs what it was given and answers as its spec file says.
HOOK_SCRIPT = textwrap.dedent("""\
    import json, os, sys, time
    log, spec = sys.argv[1], sys.argv[2]
    data = sys.stdin.read()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"stdin": json.loads(data), "cwd": os.getcwd(),
                             "env": dict(os.environ)}) + "\\n")
    answer = json.load(open(spec, encoding="utf-8"))
    time.sleep(answer.get("sleep", 0))
    sys.stdout.write(answer.get("stdout", ""))
    sys.stderr.write(answer.get("stderr", ""))
    sys.exit(answer.get("exit", 0))
""")


@pytest.fixture()
def machine(tmp_path, monkeypatch):
    """No machine policy but the test's own."""
    folder = tmp_path / "programdata"
    monkeypatch.setenv("PROGRAMDATA", str(folder))
    return folder / "Foundry"


@pytest.fixture()
def home(tmp_path, machine):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture()
def repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "a.py").write_text("A = 1\n", encoding="utf-8")
    run_git(["init"], repo)
    run_git(["config", "user.email", "t@example.com"], repo)
    run_git(["config", "user.name", "T"], repo)
    run_git(["add", "."], repo)
    run_git(["commit", "-m", "initial"], repo)
    return repo


class Scripted:
    """A hook script, its log, and the answer it gives."""

    def __init__(self, folder: Path, name: str = "hook") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.script = folder / f"{name}.py"
        self.script.write_text(HOOK_SCRIPT, encoding="utf-8", newline="\n")
        self.log = folder / f"{name}.log"
        self.spec = folder / f"{name}.json"
        self.answer()

    def answer(self, stdout: str | dict = "", *, stderr: str = "", exit: int = 0,
               sleep: float = 0) -> "Scripted":
        if isinstance(stdout, dict):
            stdout = json.dumps(stdout)
        self.spec.write_text(json.dumps({"stdout": stdout, "stderr": stderr, "exit": exit,
                                         "sleep": sleep}), encoding="utf-8")
        return self

    @property
    def command(self) -> str:
        return f'"{sys.executable}" "{self.script}" "{self.log}" "{self.spec}"'

    def calls(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


def _hooks_json(home: Path, events: dict) -> None:
    (home / "hooks.json").write_text(json.dumps({"hooks": events}), encoding="utf-8")


def _group(command: str, matcher: str | None = None, **handler) -> dict:
    group: dict = {"hooks": [{"type": "command", "command": command, **handler}]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


def call(cid: str, name: str, args: dict) -> ToolUseBlock:
    return ToolUseBlock(call_id=cid, name=name, arguments=json.dumps(args))


def turn(*calls: ToolUseBlock, text: str = "") -> ModelTurn:
    return ModelTurn(text=text, tool_calls=tuple(calls), usage=Usage(10, 5),
                     stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN)


def _wire(repo, home, turns, *, overrides=None, approve=ApprovalChoice.ONCE):
    wiring = build(repo, home=home, overrides={**OVERRIDES, **(overrides or {})},
                   console=Console(file=io.StringIO(), width=100))
    wiring.runtime.backend = ScriptedBackend(list(turns))
    asked: list = []

    def approval(request):
        asked.append(request)
        return approve

    wiring.runtime.approval = approval if approve else None
    wired = Wired(wiring, asked)
    wiring.runtime.events.subscribe(wired.seen.append)
    return wired


class Wired:
    """A wiring, with what the user was asked and every event emitted."""

    def __init__(self, wiring, asked) -> None:
        self.wiring, self.asked, self.seen = wiring, asked, []

    def __getattr__(self, name):
        return getattr(self.wiring, name)


def _result(wiring, cid: str) -> ToolResultBlock:
    for message in wiring.runtime.context.history:
        for block in message.blocks:
            if isinstance(block, ToolResultBlock) and block.call_id == cid:
                return block
    raise AssertionError(f"no result for {cid}")


def _records(wiring, kind: EventType) -> list[dict]:
    return [r.payload for r in SessionStore.read_records(wiring.session.path) if r.type == kind]


def _notices(wiring) -> list[str]:
    return [e.text for e in wiring.seen if isinstance(e, Notice)]


def _run(hook: Hook, **fields) -> HookRun:
    return HookRun(hook, **fields)


PRE = Hook(event="PreToolUse", matcher="", command="pre")
POST = Hook(event="PostToolUse", matcher="", command="post")


# --- where hooks are read ---------------------------------------------------


def test_a_hooks_json_beside_your_config_is_read(home):
    _hooks_json(home, {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.cmd", "timeout": 30,
                                       "statusMessage": " Checking ",
                                       "additionalContextLimit": 10}]}],
        "PostToolUse": [{"hooks": [{"type": "command", "command": "after.cmd", "async": True}]}]})
    config = load_config(home=home)
    pre, post = config.hooks
    assert (pre.event, pre.matcher, pre.command, pre.timeout_s, pre.status_message,
            pre.context_tokens, pre.background) == ("PreToolUse", "Bash", "guard.cmd", 30,
                                                    "Checking", 10, False)
    assert pre.source == str(home / "hooks.json")
    assert (post.event, post.matcher, post.timeout_s, post.context_tokens, post.background) == (
        "PostToolUse", "", DEFAULT_TIMEOUT_S, DEFAULT_CONTEXT_TOKENS, True)


def test_a_hooks_table_of_config_toml_is_read_as_codex_writes_it(home):
    (home / "config.toml").write_text(textwrap.dedent("""\
        [[hooks.PreToolUse]]
        matcher = "^Bash$"

        [[hooks.PreToolUse.hooks]]
        type = "command"
        command = 'python "check.py"'
        timeout = 30
        statusMessage = "Checking Bash command"
    """), encoding="utf-8")
    (hook,) = load_config(home=home).hooks
    assert (hook.matcher, hook.command, hook.timeout_s, hook.status_message, hook.source) == (
        "^Bash$", 'python "check.py"', 30, "Checking Bash command", "user config")


def test_both_forms_load_and_the_machine_policys_come_first(home, machine):
    machine.mkdir(parents=True)
    (machine / "policy.toml").write_text(
        '[[hooks.PostToolUse]]\n[[hooks.PostToolUse.hooks]]\ntype = "command"\n'
        'command = "policy-toml"\n', encoding="utf-8")
    (machine / "hooks.json").write_text(json.dumps({"hooks": {"PostToolUse": [
        _group("policy-json")]}}), encoding="utf-8")
    (home / "config.toml").write_text(
        '[[hooks.PreToolUse]]\n[[hooks.PreToolUse.hooks]]\ntype = "command"\n'
        'command = "user-toml"\n', encoding="utf-8")
    _hooks_json(home, {"PreToolUse": [_group("user-json")]})
    config = load_config(home=home)
    assert [h.command for h in config.hooks] == ["policy-toml", "policy-json", "user-toml",
                                                 "user-json"]
    assert config.hooks[0].source == "managed config"
    assert config.hooks[1].source == str(machine / "hooks.json")


def test_a_profile_may_add_hooks(home):
    (home / "config.toml").write_text(
        'profile = "work"\n[[profiles.work.hooks.PreToolUse]]\n'
        '[[profiles.work.hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "w"\n',
        encoding="utf-8")
    (hook,) = load_config(home=home).hooks
    assert (hook.command, hook.source) == ("w", "profile work config")


def test_a_repository_may_not_name_a_hook(home, tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        '[[hooks.PreToolUse]]\n[[hooks.PreToolUse.hooks]]\ntype = "command"\n'
        'command = "evil"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not set hooks: each is a program "
                                          "Foundry runs; set them in your own config.toml"):
        load_config(workspace, home=home)


def test_a_repositorys_hooks_json_is_not_read(home, tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        _group("evil")]}}), encoding="utf-8")
    (workspace / ".codex").mkdir()
    (workspace / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        _group("evil")]}}), encoding="utf-8")
    assert load_config(workspace, home=home).hooks == ()


def test_dash_c_says_where_hooks_go():
    with pytest.raises(ConfigError, match=r"\[hooks\] goes in your config.toml"):
        parse_settings(["hooks.PreToolUse=x"])


@pytest.mark.parametrize("event, message", [
    ("SubagentStart", "Foundry does not run SubagentStart hooks yet; it runs PreToolUse, "
                      "PermissionRequest, PostToolUse, UserPromptSubmit, Stop, SessionStart, "
                      "SessionEnd, Interrupt, PreCompact, PostCompact, PreModelRequest and "
                      "PostModelRequest"),
    ("SubagentStop", "Foundry does not run SubagentStop hooks yet"),
    ("PreTooluse", "no hook event 'PreTooluse'; did you mean 'PreToolUse'?"),
    ("state", "hooks.state is codex's record of the hooks you reviewed in its /hooks"),
    ("managed_dir", "hooks.managed_dir belongs to codex's requirements.toml"),
])
def test_an_event_foundry_does_not_run_is_said(event, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_hooks({event: []}, "f")


@pytest.mark.parametrize("handler, message", [
    ({"type": "prompt"}, "a prompt hook is skipped by codex too; remove it"),
    ({"type": "agent"}, "an? agent hook is skipped by codex too"),
    ({"command": "x"}, r'type must be "command" or "mcp_tool"'),
    ({"type": "command"}, "command must be a non-empty string"),
    ({"type": "command", "command": "  "}, "command must be a non-empty string"),
    ({"type": "command", "command": "x", "statusmessage": "s"},
     "no setting 'statusmessage'; did you mean 'statusMessage'"),
    ({"type": "command", "command": "x", "zzz": 1}, "no setting 'zzz' \\(a hook takes "),
    ({"type": "command", "command": "x", "commandWindows": "a", "command_windows": "b"},
     "commandWindows and command_windows are one setting; give one"),
    ({"type": "command", "command": "x", "commandWindows": ""},
     "commandWindows must be a non-empty string"),
    ({"type": "command", "command": "x", "timeout": 0}, "timeout is in seconds"),
    ({"type": "command", "command": "x", "timeout": True}, "timeout is in seconds"),
    ({"type": "command", "command": "x", "timeout": 1.5}, "timeout is in seconds"),
    ({"type": "command", "command": "x", "async": "yes"}, "async must be true or false"),
    ({"type": "command", "command": "x", "statusMessage": 3}, "statusMessage must be a string"),
    ({"type": "command", "command": "x", "additionalContextLimit": -1},
     "additionalContextLimit is a number of tokens, 0 or more"),
    ({"type": "command", "command": "x", "additionalContextLimit": False},
     "additionalContextLimit is a number of tokens"),
    ("x", "must be a table"),
])
def test_a_handler_codex_would_not_run_as_written_is_refused(handler, message):
    with pytest.raises(ConfigError, match=message):
        parse_hooks({"PreToolUse": [{"hooks": [handler]}]}, "f")


def test_commandwindows_is_the_command_on_windows():
    for key in ("commandWindows", "command_windows"):
        (hook,) = parse_hooks({"PreToolUse": [{"hooks": [
            {"type": "command", "command": "sh-only", key: "win.cmd"}]}]}, "f")
        assert hook.command == "win.cmd"


@pytest.mark.parametrize("events, message", [
    ([], "hooks must be a table of events"),
    ({"PreToolUse": {}}, "hooks.PreToolUse must be a list of matcher groups"),
    ({"PreToolUse": ["x"]}, r"hooks.PreToolUse\[0\] must be a table"),
    ({"PreToolUse": [{"matcher": "Bash", "hook": []}]}, "no setting 'hook' \\(a matcher group"),
    ({"PreToolUse": [{"matcher": 3, "hooks": []}]}, "matcher must be a string"),
    ({"PreToolUse": [{"matcher": "mcp__(", "hooks": []}]},
     r"matcher 'mcp__\(' is not a regular expression"),
    ({"PreToolUse": [{"hooks": {}}]}, "hooks must be a list"),
])
def test_a_malformed_table_says_where(events, message):
    with pytest.raises(ConfigError, match=message):
        parse_hooks(events, "f")


@pytest.mark.parametrize("content, message", [
    ("{", "cannot read"),
    ("[]", "a hooks file is one JSON object"),
    ('{"hooks": {}, "version": 1}', "no setting 'version' \\(a hooks file takes"),
    ('{"description": 3, "hooks": {}}', "description must be a string"),
])
def test_a_malformed_hooks_file_says_so(tmp_path, content, message):
    path = tmp_path / "hooks.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        read_hooks_file(path)


def test_a_hooks_file_with_a_description_and_a_bom_is_read(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text("\ufeff" + json.dumps({"description": "mine", "hooks": {
        "PostToolUse": [_group("x")]}}), encoding="utf-8")
    assert [h.command for h in read_hooks_file(path)] == ["x"]
    assert read_hooks_file(tmp_path / "none.json") == ()


# --- which calls a hook matches ---------------------------------------------------


@pytest.mark.parametrize("matcher, tool, matched", [
    ("", "read_file", True),
    ("*", "mcp__fs__read", True),
    ("Bash", "run_command", True),
    ("run_command", "run_command", True),
    ("Edit|Write", "apply_patch", True),
    ("apply_patch", "apply_patch", True),
    ("^apply_patch$", "apply_patch", True),
    ("mcp__fs__.*", "mcp__fs__read", True),
    ("read", "read_file", False),        # names only: a name, not a part of one
    ("Bash", "read_file", False),
    ("Bas", "run_command", False),
    ("^read", "read_file", True),         # a regular expression is searched for
    ("file$", "read_file", True),
    ("Edit", "run_command", False),
])
def test_a_matcher_takes_codexs_names_and_foundrys(matcher, tool, matched):
    assert Hook("PreToolUse", matcher, "c").matches(matcher_names(tool)) is matched


def test_a_hook_sees_codexs_input_for_a_command_and_a_patch():
    assert hook_tool_input("apply_patch", {"patch": "P"}) == {"command": "P", "paths": []}
    assert hook_tool_input("run_command", {"command": "ls", "cwd": "", "timeout_s": 120}) == {
        "command": "ls", "cwd": "", "timeout_s": 120}
    assert hook_tool_input("read_file", {"path": "a"}) == {"path": "a"}


def test_a_rewrite_is_codexs_shape_or_nothing():
    args = {"command": "ls", "cwd": "", "timeout_s": 120}
    assert rewritten_args("run_command", args, {"command": "dir"}) == {
        "command": "dir", "cwd": "", "timeout_s": 120}
    assert rewritten_args("apply_patch", {"patch": "P"}, {"command": "Q"}) == {"patch": "Q"}
    assert rewritten_args("read_file", {"path": "a"}, {"path": "b"}) == {"path": "b"}
    assert rewritten_args("run_command", args, {"cmd": "dir"}) is None
    assert rewritten_args("apply_patch", {"patch": "P"}, {"patch": "Q"}) is None
    assert rewritten_args("read_file", {"path": "a"}, ["b"]) is None


# --- what an answer means ---------------------------------------------------


def _pre(stdout="", **fields):
    return read_pre_tool_use([_run(fields.pop("hook", PRE), exit_code=fields.pop("exit_code", 0),
                                   stdout=json.dumps(stdout) if isinstance(stdout, dict)
                                   else stdout, **fields)])


def _post(stdout="", **fields):
    return read_post_tool_use([_run(fields.pop("hook", POST),
                                    exit_code=fields.pop("exit_code", 0),
                                    stdout=json.dumps(stdout) if isinstance(stdout, dict)
                                    else stdout, **fields)])


def _specific(**fields):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", **fields}}


def test_a_pretooluse_deny_refuses_the_call():
    verdict = _pre(_specific(permissionDecision="deny", permissionDecisionReason=" no rm "))
    assert verdict.block == ["no rm"] and not verdict.failures
    assert verdict.records[0]["outcome"] == "blocked"


def test_codexs_older_block_and_exit_code_two_refuse_too():
    assert _pre({"decision": "block", "reason": "old shape"}).block == ["old shape"]
    verdict = _pre(exit_code=2, stderr=" from stderr \n")
    assert verdict.block == ["from stderr"]
    assert verdict.records[0]["exit_code"] == 2


def test_exit_code_two_without_a_reason_is_a_failure_not_a_refusal():
    verdict = _pre(exit_code=2, stderr=" ")
    assert verdict.block == []
    assert verdict.failures == ["`pre` exited with code 2 but did not write a reason to stderr"]
    assert verdict.records[0]["outcome"] == "failed"


def test_allow_with_updated_input_rewrites():
    verdict = _pre(_specific(permissionDecision="allow", updatedInput={"command": "echo x"}))
    assert verdict.rewrites == [{"command": "echo x"}] and not verdict.block
    assert verdict.records[0]["outcome"] == "rewrote"


@pytest.mark.parametrize("answer, why", [
    (_specific(permissionDecision="ask"), "returned unsupported permissionDecision:ask"),
    (_specific(permissionDecision="allow"), "returned unsupported permissionDecision:allow"),
    (_specific(updatedInput={"command": "x"}),
     "returned updatedInput without permissionDecision:allow"),
    (_specific(permissionDecision="deny", updatedInput={"command": "x"},
               permissionDecisionReason="r"),
     "returned updatedInput without permissionDecision:allow"),
    (_specific(permissionDecision="deny"),
     "returned permissionDecision:deny without a non-empty reason"),
    (_specific(permissionDecision="deny", permissionDecisionReason="  "),
     "returned permissionDecision:deny without a non-empty reason"),
    (_specific(permissionDecisionReason="r"),
     "returned permissionDecisionReason without permissionDecision"),
    ({"decision": "approve"}, "returned unsupported decision:approve"),
    ({"decision": "block"}, "returned decision:block without a non-empty reason"),
    ({"reason": "r"}, "returned reason without decision"),
    ({"continue": False}, "returned unsupported continue:false"),
    ({"stopReason": "s"}, "returned unsupported stopReason"),
    ({"suppressOutput": True}, "returned unsupported suppressOutput"),
    ({"continue": False, **_specific(permissionDecision="deny",
                                     permissionDecisionReason="r")},
     "returned unsupported continue:false"),
])
def test_an_answer_codex_does_not_act_on_is_a_failure_and_the_call_goes_on(answer, why):
    verdict = _pre(answer)
    assert verdict.block == [] and verdict.rewrites == []
    assert verdict.failures == [f"`pre` {why}"]


@pytest.mark.parametrize("stdout", [
    "{not json", "[1, 2]", '{"systemMessage": 3}', '{"decision": "maybe"}',
    '{"hookSpecificOutput": "x"}', '{"hookSpecificOutput": {"permissionDecision": "yes"}}',
    '{"hookSpecificOutput": {"additionalContext": 7}}', '{"continue": "no"}',
])
def test_json_of_the_wrong_shape_is_a_failure(stdout):
    verdict = _pre(stdout)
    assert verdict.failures == ["`pre` returned invalid PreToolUse JSON output"]
    assert verdict.block == []


@pytest.mark.parametrize("stdout", ["", "  \n", "all good", '"a string"', "12"])
def test_plain_text_is_no_answer_and_no_failure(stdout):
    verdict = _pre(stdout)
    assert (verdict.block, verdict.failures, verdict.context) == ([], [], [])
    assert verdict.records[0]["outcome"] == "completed"


def test_a_null_field_is_one_left_out():
    verdict = _pre({"systemMessage": None, "decision": None, "hookSpecificOutput": None})
    assert (verdict.block, verdict.failures, verdict.messages) == ([], [], [])


@pytest.mark.parametrize("fields, why", [
    ({"exit_code": 1}, "exited with code 1"),
    ({"exit_code": None}, "exited without a status code"),
    ({"exit_code": None, "error": "timed out after 5s"}, "timed out after 5s"),
])
def test_a_hook_that_fails_is_a_warning(fields, why):
    verdict = _pre("", **fields)
    assert verdict.failures == [f"`pre` {why}"] and verdict.block == []
    assert verdict.records[0]["outcome"] == "failed"
    if "error" in fields:
        assert verdict.records[0]["error"] == fields["error"]


def test_a_long_command_is_shortened_in_a_warning():
    hook = Hook("PreToolUse", "", "x" * 100)
    (failure,) = _pre(exit_code=1, hook=hook).failures
    assert failure == f"`{'x' * 57}...` exited with code 1"


def test_a_note_for_the_model_and_a_message_for_the_user():
    verdict = _pre({"systemMessage": "careful", **_specific(additionalContext="generated")})
    assert verdict.context == ["generated"] and verdict.messages == ["careful"]
    assert not verdict.failures


def test_a_note_is_cut_at_its_limit_and_zero_is_none():
    text = "y" * 50
    (cut,) = _pre(_specific(additionalContext=text),
                  hook=Hook("PreToolUse", "", "c", context_tokens=5)).context
    assert cut.startswith("y" * 20) and not cut.startswith("y" * 21)
    assert "the hook gave 50 characters, more than its additionalContextLimit of about 5" in cut
    assert _pre(_specific(additionalContext=text),
                hook=Hook("PreToolUse", "", "c", context_tokens=0)).context == [text]
    assert _pre(_specific(additionalContext="y" * 20),
                hook=Hook("PreToolUse", "", "c", context_tokens=5)).context == ["y" * 20]


def test_a_broken_answer_gives_no_note_but_an_async_hooks_does():
    answer = {"decision": "approve", **_specific(additionalContext="note")}
    assert _pre(answer).context == []
    background = Hook("PreToolUse", "", "c", background=True)
    verdict = _pre(answer, hook=background)
    assert verdict.context == ["note"] and verdict.failures == []


def test_an_async_hook_never_decides():
    background = Hook("PreToolUse", "", "c", background=True)
    deny = _specific(permissionDecision="deny", permissionDecisionReason="no")
    assert _pre(deny, hook=background).block == []
    rewrite = _specific(permissionDecision="allow", updatedInput={"command": "x"})
    assert _pre(rewrite, hook=background).rewrites == []
    verdict = _pre(exit_code=2, stderr="no", hook=background)
    assert verdict.block == [] and verdict.failures == ["`c` exited with code 2"]


def test_every_hooks_answer_counts_and_any_refusal_wins():
    deny = json.dumps(_specific(permissionDecision="deny", permissionDecisionReason="no"))
    verdict = read_pre_tool_use([_run(PRE, exit_code=0, stdout=""),
                                 _run(PRE, exit_code=0, stdout=deny),
                                 _run(PRE, exit_code=1)])
    assert verdict.block == ["no"] and len(verdict.failures) == 1
    assert [r["outcome"] for r in verdict.records] == ["completed", "blocked", "failed"]


def test_a_posttooluse_block_gives_its_reason():
    verdict = _post({"decision": "block", "reason": "needs review"})
    assert verdict.block == ["needs review"]
    assert _post(exit_code=2, stderr="from stderr").block == ["from stderr"]


def test_continue_false_after_a_call_gives_its_reason_or_stop_text():
    assert _post({"continue": False, "stopReason": "halt"}).block == ["halt"]
    assert _post({"continue": False, "reason": "why", "stopReason": "halt"}).block == ["why"]
    assert _post({"continue": False}).block == ["PostToolUse hook stopped execution"]
    assert _post({"continue": False}).records[0]["outcome"] == "stopped"


@pytest.mark.parametrize("answer, why", [
    ({"decision": "block"}, "returned decision:block without a non-empty reason"),
    ({"decision": "block", "reason": " "}, "returned decision:block without a non-empty reason"),
    ({"reason": "r"}, "returned reason without decision"),
    ({"suppressOutput": True}, "returned unsupported suppressOutput"),
    ({"hookSpecificOutput": {"updatedMCPToolOutput": {}}},
     "returned unsupported updatedMCPToolOutput"),
])
def test_a_posttooluse_answer_codex_does_not_act_on(answer, why):
    verdict = _post(answer)
    assert verdict.block == [] and verdict.failures == [f"`post` {why}"]


@pytest.mark.parametrize("stdout", ['{"decision": "approve"}', "[]", '{"reason": 1}'])
def test_a_posttooluse_answer_of_the_wrong_shape(stdout):
    assert _post(stdout).failures == ["`post` returned invalid PostToolUse JSON output"]


def test_a_posttooluse_note_and_message():
    verdict = _post({"systemMessage": "m", "hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": "updated generated files"}})
    assert verdict.context == ["updated generated files"] and verdict.messages == ["m"]
    assert _post({"decision": "block", "hookSpecificOutput": {
        "additionalContext": "x"}}).context == []
    background = Hook("PostToolUse", "", "c", background=True)
    assert _post({"decision": "block", "reason": "r"}, hook=background).block == []


# --- running a hook ------------------------------------------------------------


def test_a_hook_gets_the_payload_on_stdin_in_the_workspace(tmp_path):
    hook = Scripted(tmp_path / "h").answer("out", stderr="err", exit=3)
    (run,) = HookSet(cwd=tmp_path).run([Hook("PreToolUse", "", hook.command)], {"a": "b \"c\""})
    assert (run.exit_code, run.stdout, run.stderr, run.error) == (3, "out", "err", "")
    (seen,) = hook.calls()
    assert seen["stdin"] == {"a": "b \"c\""}
    assert Path(seen["cwd"]) == tmp_path


def test_a_hook_gets_the_command_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_SERVICE_API_KEY", "not-for-hooks")
    monkeypatch.setenv("FOUNDRY_TEST_PLAIN", "plain")
    hook = Scripted(tmp_path / "h")
    HookSet(cwd=tmp_path).run([Hook("PreToolUse", "", hook.command)], {})
    env = {k.upper(): v for k, v in hook.calls()[0]["env"].items()}
    assert "MY_SERVICE_API_KEY" not in env and "FOUNDRY_TEST_PLAIN" not in env
    assert env["NODEFAULTCURRENTDIRECTORYINEXEPATH"] == "1"
    assert "PATH" in env


def test_a_program_planted_in_the_workspace_is_not_the_one_run(tmp_path):
    (tmp_path / "hostname.bat").write_text("@echo planted\r\n", encoding="ascii")
    (run,) = HookSet(cwd=tmp_path).run([Hook("PreToolUse", "", "hostname")], {})
    assert run.exit_code == 0 and "planted" not in run.stdout and run.stdout.strip()


def test_a_command_line_with_quotes_and_specials_reaches_cmd_whole(tmp_path):
    (run,) = HookSet(cwd=tmp_path).run(
        [Hook("PreToolUse", "", f'"{sys.executable}" -c "print(\'a & b\')"')], {})
    assert run.stdout.strip() == "a & b"


def test_a_hook_that_outlives_its_timeout_is_ended(tmp_path):
    hook = Scripted(tmp_path / "h").answer(sleep=30)
    started = time.monotonic()
    (run,) = HookSet(cwd=tmp_path).run([Hook("PreToolUse", "", hook.command, timeout_s=1)], {})
    assert time.monotonic() - started < 15
    assert run.error == "timed out after 1s" and run.exit_code is None


def test_a_hook_that_reads_nothing_is_given_a_large_payload_without_a_hang(tmp_path):
    started = time.monotonic()
    (run,) = HookSet(cwd=tmp_path).run(
        [Hook("PreToolUse", "", "echo ok", timeout_s=20)], {"big": "z" * 1_000_000})
    assert run.exit_code == 0 and run.stdout.strip() == "ok"
    assert time.monotonic() - started < 15


def test_a_hook_that_prints_much_and_reads_nothing_does_not_deadlock(tmp_path):
    # The payload is written on a thread: written first, it would wait on a
    # child that waits on its own full output pipe, with no timeout running.
    printer = f'"{sys.executable}" -c "import sys; sys.stdout.write(\'x\' * 300000)"'
    (run,) = HookSet(cwd=tmp_path).run([Hook("PreToolUse", "", printer, timeout_s=20)],
                                       {"big": "z" * 1_000_000})
    assert run.exit_code == 0 and len(run.stdout) == 300000


def test_the_shell_environment_policy_reaches_hooks(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"PreToolUse": [_group(hook.command)]})
    (home / "config.toml").write_text('[shell_environment_policy]\nset = { HOOK_MARK = "1" }\n',
                                      encoding="utf-8")
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("read")
    assert hook.calls()[0]["env"].get("HOOK_MARK") == "1"


def test_slash_hooks_is_a_command():
    from foundry.cli.repl import COMMANDS

    assert COMMANDS["hooks"].run is show_hooks


def test_hooks_run_together_and_their_runs_keep_their_order(tmp_path):
    slow = Scripted(tmp_path / "a", "slow").answer("slow", sleep=2)
    fast = Scripted(tmp_path / "b", "fast").answer("fast", sleep=2)
    started = time.monotonic()
    runs = HookSet(cwd=tmp_path).run([Hook("PreToolUse", "", slow.command),
                                      Hook("PreToolUse", "", fast.command)], {})
    assert [r.stdout for r in runs] == ["slow", "fast"]
    assert time.monotonic() - started < 3.8   # not one after the other


def test_a_stopped_session_ends_its_running_hooks(tmp_path):
    hooks = [Hook("PreToolUse", "", Scripted(tmp_path / n, n).answer(sleep=30).command)
             for n in ("a", "b")]
    started = time.monotonic()
    deadline = started + 1.5
    with pytest.raises(KeyboardInterrupt):
        HookSet(cwd=tmp_path).run(hooks, {}, cancelled=lambda: time.monotonic() > deadline)
    assert time.monotonic() - started < 15
    with pytest.raises(KeyboardInterrupt):
        HookSet(cwd=tmp_path).run(hooks[:1], {}, cancelled=lambda: time.monotonic() > deadline)


def test_a_hook_that_cannot_start_says_why(tmp_path, monkeypatch):
    import foundry.core.hooks as hooks_module

    monkeypatch.setattr(hooks_module, "find_program", lambda name, env=None: None)
    (run,) = HookSet(cwd=tmp_path).run([Hook("PreToolUse", "", "x")], {})
    assert run.error == "cmd.exe was not found on PATH"


def test_run_program_writes_stdin_and_takes_a_command_line(tmp_path):
    script = "import sys; print(sys.stdin.read().upper())"
    result = run_program([sys.executable, "-c", script], cwd=str(tmp_path), timeout_s=20,
                         stdin_data=b"abc")
    assert result.stdout.decode().strip() == "ABC"
    result = run_program([sys.executable, "-c", script], cwd=str(tmp_path), timeout_s=20)
    assert result.stdout.decode().strip() == ""


# --- at a tool call ---------------------------------------------------------------


def _made(repo):
    return repo / "made.txt"


MAKE = {"command": "New-Item -ItemType File -Path made.txt | Out-Null"}


def test_a_pretooluse_refusal_keeps_the_call_from_running(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_specific(permissionDecision="deny",
                                                     permissionDecisionReason="not today"))
    _hooks_json(home, {"PreToolUse": [_group(hook.command, "Bash")]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert not _made(repo).exists()
    result = _result(wiring, "c1")
    assert result.is_error and result.content == "blocked by a PreToolUse hook: not today"
    assert wiring.asked == []          # nobody is asked about a call a hook refused
    (decision,) = _records(wiring, EventType.POLICY_DECISION)
    assert (decision["verdict"], decision["rule_id"], decision["step"], decision["reason"]) == (
        "deny", "hook.PreToolUse", 1, "not today")
    (rejected,) = [e for e in wiring.seen if isinstance(e, ToolRejected)]
    assert (rejected.rule_id, rejected.reason) == ("hook.PreToolUse", "not today")
    (record,) = _records(wiring, EventType.HOOK)
    assert record["event"] == "PreToolUse" and record["call_id"] == "c1"
    assert record["runs"][0]["outcome"] == "blocked"
    assert record["runs"][0]["source"] == str(home / "hooks.json")


def test_a_hook_is_told_what_codexs_hooks_are(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"PreToolUse": [_group(hook.command)],
                       "PostToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", {"command": "Write-Output hi"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("say hi")
    before, after = (c["stdin"] for c in hook.calls())
    assert Path(before.pop("cwd")) == Path(wiring.workspace.root)
    assert Path(hook.calls()[0]["cwd"]) == Path(wiring.workspace.root)
    assert before == {
        "session_id": wiring.session.session_id,
        "transcript_path": str(wiring.session.path),
        "hook_event_name": "PreToolUse", "model": "fake", "turn_id": "1",
        "permission_mode": "default", "tool_name": "Bash", "tool_use_id": "c1",
        "tool_input": {"command": "Write-Output hi", "cwd": ".", "timeout_s": 120}}
    after.pop("cwd")
    assert after["hook_event_name"] == "PostToolUse"
    assert "hi" in after["tool_response"]
    assert {k: v for k, v in after.items() if k not in ("hook_event_name", "tool_response")} == {
        k: v for k, v in before.items() if k != "hook_event_name"}


def test_a_patch_is_matched_as_edit_and_sent_as_codexs_command(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"PreToolUse": [_group(hook.command, "Edit|Write")]})
    patch = ("*** Begin Patch\n*** Update File: a.py\n"
             "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n*** End Patch\n")
    wiring = _wire(repo, home, [turn(call("r", "read_file", {"path": "a.py"})),
                                turn(call("c1", "apply_patch", {"patch": patch})),
                                turn(text="ok")], overrides={"mode": Mode.ACCEPT_EDITS})
    wiring.runtime.run_turn("edit")
    (seen,) = hook.calls()   # read_file is not an edit
    assert (seen["stdin"]["tool_name"], seen["stdin"]["tool_input"]) == (
        "apply_patch", {"command": patch, "paths": ["a.py"]})
    assert seen["stdin"]["permission_mode"] == "acceptEdits"
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"


def test_a_rewritten_call_runs_and_the_model_is_told(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_specific(
        permissionDecision="allow", updatedInput={"command": "Write-Output rewritten"}))
    _hooks_json(home, {"PreToolUse": [_group(hook.command, "Bash")]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert not _made(repo).exists()
    result = _result(wiring, "c1")
    assert "rewritten" in result.content
    assert result.content.endswith("From a PreToolUse hook: this call was changed before it ran; "
                                   "it ran as: Write-Output rewritten")
    (asked,) = wiring.asked     # what is asked about is what runs
    assert "Write-Output rewritten" in str(asked)
    (record,) = _records(wiring, EventType.TOOL_CALL)
    assert record["target"] == "Write-Output rewritten"
    assert record["rewritten_from"] == MAKE["command"]


def test_a_rewrite_is_checked_by_policy_from_the_start(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_specific(
        permissionDecision="allow", updatedInput={"command": "git reset --hard"}))
    _hooks_json(home, {"PreToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", {"command": "Write-Output a"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("go")
    result = _result(wiring, "c1")
    assert result.is_error and result.content.startswith("blocked by policy:")
    (decision,) = _records(wiring, EventType.POLICY_DECISION)
    assert decision["rule_id"] == "breaker"


def test_a_hook_cannot_allow_what_policy_would_not(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_specific(
        permissionDecision="allow", updatedInput={"command": MAKE["command"]}))
    _hooks_json(home, {"PreToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")],
                   overrides={"mode": Mode.DONT_ASK}, approve=None)
    wiring.runtime.run_turn("make it")
    assert not _made(repo).exists()
    assert _result(wiring, "c1").is_error
    assert _records(wiring, EventType.TOOL_CALL)[0].get("rewritten_from") is None


@pytest.mark.parametrize("updated, warning", [
    ({"cmd": "x"}, "returned an updatedInput that is not this tool's input"),
    ({"command": "x", "timeout_s": 0}, "hook's rewrite is not a valid call (timeout_s must be "
                                      "between 1 and 600)"),
])
def test_a_rewrite_that_cannot_run_leaves_the_call_as_made(repo, home, tmp_path, updated,
                                                           warning):
    hook = Scripted(tmp_path / "h").answer(_specific(permissionDecision="allow",
                                                     updatedInput=updated))
    _hooks_json(home, {"PreToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert _made(repo).exists()
    assert any(warning in text for text in _notices(wiring))


def test_two_hooks_rewriting_a_call_differently_refuse_it(repo, home, tmp_path):
    one = Scripted(tmp_path / "a", "a").answer(_specific(
        permissionDecision="allow", updatedInput={"command": "Write-Output one"}))
    two = Scripted(tmp_path / "b", "b").answer(_specific(
        permissionDecision="allow", updatedInput={"command": "Write-Output two"}))
    same = Scripted(tmp_path / "c", "c").answer(_specific(
        permissionDecision="allow", updatedInput={"command": "Write-Output one"}))
    _hooks_json(home, {"PreToolUse": [_group(one.command), _group(same.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert "From a PreToolUse hook: this call was changed" in _result(wiring, "c1").content
    _hooks_json(home, {"PreToolUse": [_group(one.command), _group(two.command)]})
    wiring = _wire(repo, home, [turn(call("c2", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert not _made(repo).exists()
    assert _result(wiring, "c2").content == ("blocked by a PreToolUse hook: two PreToolUse hooks "
                                             "rewrote this call differently, so it was not run")


def test_a_failing_hook_is_a_warning_and_the_call_goes_on(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer("{broken", exit=0)
    other = Scripted(tmp_path / "o", "o").answer(exit=1)
    _hooks_json(home, {"PreToolUse": [_group(hook.command), _group(other.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", MAKE)), turn(text="ok")])
    wiring.runtime.run_turn("make it")
    assert _made(repo).exists()
    notices = _notices(wiring)
    assert any(t.startswith("PreToolUse hook `") and t.endswith(
        "returned invalid PreToolUse JSON output") for t in notices)
    assert any(t.endswith("exited with code 1") for t in notices)


def test_a_status_and_a_system_message_are_shown(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"systemMessage": "looked fine"})
    _hooks_json(home, {"PreToolUse": [_group(hook.command, statusMessage="Checking")]})
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("read")
    notices = [e for e in wiring.seen if isinstance(e, Notice)]
    assert ("PreToolUse hook: Checking", "info") in [(n.text, n.level) for n in notices]
    assert ("PreToolUse hook: looked fine", "warning") in [(n.text, n.level) for n in notices]


def test_a_note_reaches_the_model_however_the_call_ends(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer(_specific(additionalContext="touches generated code"))
    _hooks_json(home, {"PreToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", {"command": "Write-Output a"}),
                                     call("c2", "run_command", {"command": "git reset --hard"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("go")
    note = "\n\nFrom a PreToolUse hook: touches generated code"
    assert _result(wiring, "c1").content.endswith(note)
    blocked = _result(wiring, "c2")
    assert blocked.content.startswith("blocked by policy:") and blocked.content.endswith(note)
    assert wiring.runtime._hook_notes == {}


def test_a_posttooluse_block_puts_its_feedback_in_place_of_the_result(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"decision": "block", "reason": "output withheld"})
    _hooks_json(home, {"PostToolUse": [_group(hook.command, "Bash")]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command",
                                          {"command": "Write-Output secretish-output"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("go")
    result = _result(wiring, "c1")
    assert result.content == ("The call ran; a PostToolUse hook gave this in place of its "
                              "result:\noutput withheld")
    assert "secretish-output" in hook.calls()[0]["stdin"]["tool_response"]
    (recorded,) = _records(wiring, EventType.TOOL_RESULT)
    assert recorded["call_id"] == "c1" and recorded["is_error"] is False
    (reply,) = _records(wiring, EventType.TOOL_REPLY)
    assert "secretish-output" not in reply["content"]


def test_a_posttooluse_note_is_added_to_the_result(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h").answer({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": "lint is clean"}})
    _hooks_json(home, {"PostToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("read")
    content = _result(wiring, "c1").content
    assert "A = 1" in content and content.endswith("\n\nFrom a PostToolUse hook: lint is clean")


def test_posttooluse_does_not_run_for_a_call_that_never_ran(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"PostToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command", {"command": "git reset --hard"}),
                                     call("c2", "read_file", {"path": "missing.py"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("go")
    assert hook.calls() == []    # refused by policy; failed in the tool


def test_secrets_are_scrubbed_both_ways(repo, home, tmp_path):
    secret = "sk-hooktest-0123456789abcdef"
    default_redactor().register(secret)
    hook = Scripted(tmp_path / "h").answer({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": f"saw {secret}"},
        "systemMessage": f"key {secret}"})
    _hooks_json(home, {"PostToolUse": [_group(hook.command)]})
    wiring = _wire(repo, home, [turn(call("c1", "run_command",
                                          {"command": f"Write-Output {secret}"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("go")
    told = json.dumps(hook.calls()[0]["stdin"])
    assert secret not in told
    assert secret not in _result(wiring, "c1").content
    assert all(secret not in text for text in _notices(wiring))
    assert secret not in wiring.session.path.read_text(encoding="utf-8")


def test_no_hooks_means_no_hook_records(repo, home):
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(text="ok")])
    assert wiring.runtime.hooks is None
    wiring.runtime.run_turn("read")
    assert _records(wiring, EventType.HOOK) == []


def test_a_hook_that_matches_no_call_is_not_run(repo, home, tmp_path):
    hook = Scripted(tmp_path / "h")
    _hooks_json(home, {"PreToolUse": [_group(hook.command, "mcp__.*")]})
    wiring = _wire(repo, home, [turn(call("c1", "read_file", {"path": "a.py"})),
                                turn(text="ok")])
    wiring.runtime.run_turn("read")
    assert hook.calls() == [] and _records(wiring, EventType.HOOK) == []


# --- /hooks ------------------------------------------------------------------------


def _shown(wiring) -> str:
    out = io.StringIO()
    show_hooks(wiring.wiring, Console(file=out, width=200))
    return out.getvalue()


def test_slash_hooks_lists_each_hook_and_its_file(repo, home):
    _hooks_json(home, {"PreToolUse": [_group("guard.cmd [x]", "Bash", timeout=30)],
                       "PostToolUse": [_group("after.cmd", **{"async": True})]})
    shown = _shown(_wire(repo, home, []))
    lines = shown.splitlines()
    assert lines[0] == f"PreToolUse Bash  guard.cmd [x] ({home / 'hooks.json'}, 30s)"
    assert lines[1] == f"PostToolUse * (async)  after.cmd ({home / 'hooks.json'}, 600s)"


def test_slash_hooks_says_where_hooks_go(repo, home):
    shown = _shown(_wire(repo, home, []))
    assert shown.split() == (f"no hooks: codex's hooks.json format, in {home / 'hooks.json'} or "
                             "a [hooks] table of your config.toml; Foundry runs PreToolUse, "
                             "PermissionRequest, PostToolUse, UserPromptSubmit, Stop, SessionStart, SessionEnd, "
                             "Interrupt, PreCompact, PostCompact, PreModelRequest and "
                             "PostModelRequest").split()
