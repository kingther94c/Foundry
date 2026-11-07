"""mcp_tool hooks, as codex's (D-121): a hook that calls a tool of an MCP
server the session already has, its arguments filled in from the event, its
answer read as a command hook's."""

from __future__ import annotations

import json
import sys
import time

import pytest

from foundry.core.errors import ConfigError
from foundry.core.hooks import HookSet, fill_input, parse_hooks
from foundry.core.mcp import McpServerConfig, start_servers
from foundry.core.session import EventType
from foundry.core.tools.registry import default_registry
from foundry.core.winapi import IS_WINDOWS
from test_hooks import (  # noqa: F401  (fixtures)
    MAKE,
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
from test_mcp import SERVER

pytestmark = pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")

EVENT = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": "npm test", "timeout_s": 120, "args": ["a", 1],
                        "env": None}}


def _mcp(server="fake", tool="echo", **handler) -> dict:
    return {"type": "mcp_tool", "server": server, "tool": tool, **handler}


def _one(handler: dict, event: str = "PreToolUse"):
    (hook,) = parse_hooks({event: [{"hooks": [handler]}]}, "f")
    return hook


@pytest.fixture()
def fake(tmp_path):
    """The test MCP server, started as a session starts its servers."""
    opened = []

    def start(mode="normal", **extra):
        server = McpServerConfig(name="fake", command=sys.executable, args=(SERVER, mode),
                                 **extra)
        found = start_servers({"fake": server}, tmp_path, default_registry())
        opened.append(found)
        return {client.server.name: client for client in found.clients}

    yield start
    for found in opened:
        found.close()


# --- what is read ---------------------------------------------------------------


def test_an_mcp_tool_hook_is_read_as_codex_writes_it():
    hook = _one(_mcp(input={"text": "${tool_input.command}"}, timeout=30,
                     statusMessage="scanning", **{"async": True}))
    assert (hook.mcp_server, hook.mcp_tool, json.loads(hook.mcp_input)) == (
        "fake", "echo", {"text": "${tool_input.command}"})
    assert (hook.command, hook.timeout_s, hook.status_message) == (
        "mcp_tool fake/echo", 30, "scanning")
    assert hook.background is False          # one call, in step, as codex makes it
    assert json.loads(_one(_mcp()).mcp_input) == {}


@pytest.mark.parametrize("handler, message", [
    (_mcp(server=""), "server must be a non-empty string"),
    ({"type": "mcp_tool", "tool": "t"}, "server must be a non-empty string"),
    (_mcp(tool=3), "tool must be a non-empty string"),
    (_mcp(input=["x"]), "input must be a table of the tool's arguments"),
    (_mcp(command="x"), "no setting 'command'"),
    (_mcp(commandWindows="x"), "no setting 'commandWindows'"),
    (_mcp(imput={}), "no setting 'imput'; did you mean 'input'"),
    (_mcp(timeout=0), "timeout is in seconds"),
    ({"type": "command", "command": "x", "server": "s"}, "no setting 'server'"),
])
def test_a_malformed_mcp_tool_hook_says_where(handler, message):
    with pytest.raises(ConfigError, match=message):
        parse_hooks({"PreToolUse": [{"hooks": [handler]}]}, "f")


def test_sessionend_runs_no_mcp_tool_hook_as_in_codex():
    with pytest.raises(ConfigError, match="a SessionEnd hook runs a command; codex runs no "
                                          "mcp_tool hook there either"):
        _one(_mcp(), "SessionEnd")


def test_slash_hooks_names_the_server_and_tool(repo, home):
    _hooks_json(home, {"PostToolUse": [{"matcher": "Edit", "hooks": [_mcp(tool="scan")]}]})
    assert _shown(_wire(repo, home, [])).splitlines() == [
        f"PostToolUse Edit  mcp_tool fake/scan ({home / 'hooks.json'}, 600s)"]


# --- the arguments ----------------------------------------------------------------


def test_a_whole_placeholder_keeps_its_type():
    assert fill_input({"n": "${tool_input.timeout_s}", "all": "${tool_input}",
                       "list": "${tool_input.args}", "none": "${tool_input.env}"}, EVENT) == {
        "n": 120, "all": EVENT["tool_input"], "list": ["a", 1], "none": None}


def test_a_placeholder_in_text_is_written_in():
    assert fill_input({"m": "Run ${tool_input.command} (${tool_input.timeout_s}s) "
                            "with ${tool_input.args}, ${tool_input.env}"}, EVENT) == {
        "m": 'Run npm test (120s) with ["a",1], null'}


def test_objects_and_arrays_are_filled_throughout_and_the_rest_kept():
    template = {"a": {"b": ["${tool_name}", {"c": "${hook_event_name}"}]}, "n": 3, "t": True,
                "plain": "no placeholder", "odd": "${}", "brace": "{tool_name}"}
    assert fill_input(template, EVENT) == {
        "a": {"b": ["Bash", {"c": "PreToolUse"}]}, "n": 3, "t": True,
        "plain": "no placeholder", "odd": "${}", "brace": "{tool_name}"}


@pytest.mark.parametrize("template", ["${tool_input.missing}", "x ${nope}",
                                      "${tool_input.command.deeper}", "${tool_input.args.0}",
                                      # Only an object has fields, whatever a string or a
                                      # list holds.
                                      "${tool_input.command.npm}", "${tool_input.args.a}"])
def test_a_field_the_event_lacks_is_named(template):
    path = template[template.index("${") + 2:template.index("}")]
    with pytest.raises(LookupError, match=f"hook input placeholder `\\${{{path}}}` was not found"):
        fill_input({"x": template}, EVENT)


# --- a call ---------------------------------------------------------------------


def _hooks(clients, handler, event="PreToolUse"):
    return HookSet(parse_hooks({event: [{"hooks": [handler]}]}, "f"), mcp=clients)


def test_the_tools_text_is_the_hooks_answer(fake):
    hooks = _hooks(fake(), _mcp(input={"text": "ran ${tool_input.command}"}))
    (run,) = hooks.run(list(hooks.hooks), EVENT)
    assert (run.exit_code, run.stdout, run.error) == (0, "ran npm test", "")


def test_an_error_result_an_unknown_tool_or_server_is_a_failure(fake):
    clients = fake()
    for handler, why in [
            (_mcp(tool="fails"), "returned an error: bad input"),
            (_mcp(tool="nothing"), "could not be run: tools/call failed: unknown tool nothing"),
            (_mcp(server="other"), "could not be run: the MCP server 'other' is not connected"),
            (_mcp(input={"text": "${nope}"}),
             "could not be run: hook input placeholder `${nope}` was not found")]:
        hooks = _hooks(clients, handler)
        (run,) = hooks.run(list(hooks.hooks), EVENT)
        assert (run.exit_code, why in run.error) == (None, True), (handler, run.error)


class _Client:
    """A connected server's client, answering every call with ``result``."""

    def __init__(self, result) -> None:
        self.result, self.running, self.calls = result, True, []
        self.server = McpServerConfig(name="stub", command="x", tool_timeout_s=60.0)

    def request(self, method, params, *, timeout, cancelled=None):
        self.calls.append((method, params, timeout))
        return self.result


def test_only_text_content_is_the_answer():
    client = _Client({"content": [
        {"type": "text", "text": "one"}, {"type": "thought", "text": "not this"},
        {"type": "image", "data": "aGk="}, "junk", {"type": "text", "text": 2},
        {"type": "text", "text": "two"}]})
    hooks = _hooks({"stub": client}, _mcp(server="stub", tool="t", timeout=5))
    (run,) = hooks.run(list(hooks.hooks), EVENT)
    assert (run.exit_code, run.stdout) == (0, "one\ntwo")
    assert client.calls == [("tools/call", {"name": "t", "arguments": {}}, 5)]
    for result in ({}, {"content": "text"}):
        hooks = _hooks({"stub": _Client(result)}, _mcp(server="stub"))
        (run,) = hooks.run(list(hooks.hooks), EVENT)
        assert (run.exit_code, run.stdout) == (0, "")


def test_a_server_that_has_gone_is_not_started_again(fake):
    clients = fake()
    clients["fake"].close()
    hooks = _hooks(clients, _mcp())
    (run,) = hooks.run(list(hooks.hooks), EVENT)
    assert "is not connected" in run.error and not clients["fake"].running


def test_the_shorter_timeout_holds(fake):
    for clients, handler in [(fake("slow"), _mcp(timeout=1)),
                             (fake("slow", tool_timeout_s=1.0), _mcp(timeout=30))]:
        hooks = _hooks(clients, handler)
        began = time.monotonic()
        (run,) = hooks.run(list(hooks.hooks), EVENT)
        assert "tools/call timed out after 1s" in run.error
        assert time.monotonic() - began < 10


def test_a_stopped_session_stops_the_call(fake):
    hooks = _hooks(fake("slow"), _mcp())
    deadline = time.monotonic() + 0.5
    with pytest.raises(KeyboardInterrupt):
        hooks.run(list(hooks.hooks), EVENT, cancelled=lambda: time.monotonic() > deadline)


# --- through a session --------------------------------------------------------------


def _session(repo, home, handler, event="PreToolUse", matcher="Bash", turns=None):
    exe, script = json.dumps(sys.executable), json.dumps(SERVER)
    (home / "config.toml").write_text(
        f'[mcp_servers.fake]\ncommand = {exe}\nargs = [{script}, "normal"]\n', encoding="utf-8")
    _hooks_json(home, {event: [{"matcher": matcher, "hooks": [handler]}]})
    wiring = _wire(repo, home, turns or [turn(call("c1", "run_command", MAKE)),
                                         turn(text="done")])
    return wiring


def test_a_pretooluse_mcp_hook_may_refuse_a_call(repo, home):
    answer = {"decision": "block", "reason": "the scanner says no to ${tool_input.command}"}
    wiring = _session(repo, home, _mcp(input={"text": json.dumps(answer)}))
    try:
        wiring.runtime.run_turn("make it")
        assert not _made(repo).exists()
        assert _result(wiring, "c1").content == (
            f"blocked by a PreToolUse hook: the scanner says no to {MAKE['command']}")
        (record,) = _records(wiring, EventType.HOOK)
        assert record["runs"][0]["command"] == "mcp_tool fake/echo"
        assert record["runs"][0]["outcome"] == "blocked"
    finally:
        wiring.mcp.close()


def test_a_note_from_an_mcp_hook_reaches_the_model(repo, home):
    answer = {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                     "additionalContext": "scanned: clean"}}
    wiring = _session(repo, home, _mcp(input={"text": json.dumps(answer)}), "PostToolUse")
    try:
        wiring.runtime.run_turn("make it")
        assert _made(repo).exists()
        assert _result(wiring, "c1").content.endswith("From a PostToolUse hook: scanned: clean")
    finally:
        wiring.mcp.close()


def test_a_failing_mcp_hook_is_a_warning_and_the_call_goes_on(repo, home):
    wiring = _session(repo, home, _mcp(tool="fails"))
    try:
        wiring.runtime.run_turn("make it")
        assert _made(repo).exists()
        assert "PreToolUse hook `mcp_tool fake/fails` returned an error: bad input" in (
            _notices(wiring))
    finally:
        wiring.mcp.close()


def test_the_tool_is_told_the_scrubbed_event(repo, home):
    from foundry.core.redaction import default_redactor

    secret = "sk-mcphook-0123456789abcdef"
    default_redactor().register(secret)
    answer = {"systemMessage": "${tool_input.command}"}
    wiring = _session(repo, home, _mcp(input={"text": json.dumps(answer)}), turns=[
        turn(call("c1", "run_command", {"command": f"Write-Output {secret}"})),
        turn(text="done")])
    try:
        wiring.runtime.run_turn("go")
        assert "PreToolUse hook: Write-Output [redacted]" in _notices(wiring)
    finally:
        wiring.mcp.close()
