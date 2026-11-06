"""The MCP client (D-043): a configured server's tools, under Foundry's policy.

A real process speaks the protocol here (tests/mcp_fake_server.py). What must
hold: the handshake and paging work, every call is a mutator to the policy
unless the user vouched for the server, a broken or hanging server costs a
warning and never the session, the server gets a scrubbed environment, and
closing the session ends the server and what it started.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.core.config import McpServerConfig, load_config
from foundry.core.errors import Cancelled, ConfigError, InvalidToolCall, ToolError
from foundry.core.events import ApprovalChoice
from foundry.core.mcp import (
    MAX_TOOLS_PER_SERVER,
    McpError,
    StdioClient,
    render_result,
    start_servers,
    tool_name,
)
from foundry.core.policy.engine import Layer, Mode, PolicyEngine, Rule, Verdict, builtin_rules
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import ToolContext, ToolKind
from foundry.core.tools.registry import default_registry
from test_resume import _core, _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

SERVER = str(Path(__file__).with_name("mcp_fake_server.py"))


def _server(mode="normal", name="fake", **extra) -> McpServerConfig:
    return McpServerConfig(name=name, command=sys.executable, args=(SERVER, mode), **extra)


@pytest.fixture()
def started(tmp_path):
    opened = []

    def start(*servers, environ=None):
        registry = default_registry()
        found = start_servers({s.name: s for s in servers}, tmp_path, registry, environ=environ)
        opened.append(found)
        return found, registry

    yield start
    for found in opened:
        found.close()


def _ctx(tmp_path, **extra):
    return ToolContext(workspace=None, artifacts=None, read_tracker=None, **extra)


def _run(registry, name, args, tmp_path, **ctx):
    tool = registry.get(name)
    return tool.execute(tool.validate(args), _ctx(tmp_path, **ctx))


# --- the protocol ------------------------------------------------------------------


def test_a_server_comes_up_and_its_tools_are_offered(started, tmp_path):
    found, registry = started(_server())
    assert found.failures == {}
    assert found.tools["fake"] == ["mcp__fake__echo", "mcp__fake__add_numbers",
                                   "mcp__fake__env", "mcp__fake__mixed", "mcp__fake__fails"]
    echo = registry.get("mcp__fake__echo")
    schema = echo.schema()
    assert schema.parameters["required"] == ["text"]
    assert schema.description == "Echo the text back. (from the MCP server 'fake')"
    assert echo.kind is ToolKind.MUTATOR
    # A tool without a schema gets an empty object, never a missing one.
    assert registry.get("mcp__fake__mixed").schema().parameters == {"type": "object",
                                                                    "properties": {}}
    assert _run(registry, "mcp__fake__echo", {"text": "hello"}, tmp_path).content == "hello"
    assert _run(registry, "mcp__fake__add_numbers", {"a": 2, "b": 3}, tmp_path).content == "5"


def test_tools_listed_over_several_pages(started):
    found, _ = started(_server("paged"))
    assert len(found.tools["fake"]) == 5


def test_a_server_without_tools_offers_none(started):
    found, _ = started(_server("notools"))
    assert found.tools == {"fake": []} and found.failures == {}


def test_log_lines_on_stdout_are_not_messages(started, tmp_path):
    found, registry = started(_server("noisy"))
    assert _run(registry, "mcp__fake__echo", {"text": "hi"}, tmp_path).content == "hi"
    client = found.clients[0]
    assert "debug: something on stderr" in client.stderr_tail()


def test_the_servers_own_requests_get_a_ping_and_nothing_else(started, tmp_path):
    _, registry = started(_server("ping"))
    output = _run(registry, "mcp__fake__echo", {"text": "done"}, tmp_path).content
    assert output == "result error -32601 | done"


def test_content_foundry_cannot_show_is_named_not_dropped(started, tmp_path):
    _, registry = started(_server())
    assert _run(registry, "mcp__fake__mixed", {}, tmp_path).content.splitlines() == [
        "first", "[image content (image/png) not shown]", "[resource link: file:///x.txt]",
        "inline", "[binary resource file:///z.bin not shown]", "[hologram content not shown]"]
    assert render_result({"content": [], "structuredContent": {"a": 1}}) == '{\n  "a": 1\n}'


def test_an_error_result_is_an_error_for_the_model(started, tmp_path):
    _, registry = started(_server())
    output = _run(registry, "mcp__fake__fails", {}, tmp_path)
    assert output.is_error and output.content == "bad input"


def test_a_huge_result_is_truncated_and_kept(started, tmp_path):
    from foundry.core.session import ArtifactStore

    _, registry = started(_server("big"))
    artifacts = ArtifactStore(tmp_path / "artifacts")
    tool = registry.get("mcp__fake__echo")
    output = tool.execute(tool.validate({"text": "x"}),
                          ToolContext(workspace=None, artifacts=artifacts, read_tracker=None,
                                      max_output_bytes=2_000))
    assert output.truncated and len(output.content) < 2_200
    assert 'read_artifact artifact_id="' in output.content
    assert len(artifacts.read_text(output.artifact_id)) == 200_000


def test_arguments_are_checked_before_anything_is_sent(started):
    _, registry = started(_server())
    echo = registry.get("mcp__fake__echo")
    with pytest.raises(InvalidToolCall, match="needs text"):
        echo.validate({})
    with pytest.raises(InvalidToolCall, match="arguments over"):
        echo.validate({"text": "y" * 70_000})
    op = echo.validate({"text": "hi"})
    assert op.target == '{"text": "hi"}' and op.display == 'fake: echo {"text": "hi"}'


# --- a server that misbehaves ----------------------------------------------------------


def test_a_server_that_never_answers_is_reported_not_waited_for(started, monkeypatch):
    import foundry.core.mcp as mcp

    made = []

    class Recording(mcp.StdioClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(mcp, "StdioClient", Recording)
    began = time.monotonic()
    found, registry = started(_server("noinit", startup_timeout_s=1.0))
    assert time.monotonic() - began < 10
    assert found.failures == {"fake": "initialize timed out after 1s"}
    assert not any(n.startswith("mcp__") for n in registry.names())
    # Not left running for the rest of the session.
    assert made and not made[0].running and made[0]._process is None


def test_a_missing_program_is_reported(started):
    found, _ = started(McpServerConfig(name="gone", command="no-such-program-foundry-test"))
    assert found.failures == {"gone": "'no-such-program-foundry-test' was not found on PATH"}


def test_a_server_that_dies_mid_call_says_why(started, tmp_path):
    _, registry = started(_server("crash"))
    with pytest.raises(ToolError, match=r"MCP server fake: the server stopped \(exit code 3\)"
                                        r"; it said: fatal: out of cheese"):
        _run(registry, "mcp__fake__echo", {"text": "x"}, tmp_path)
    with pytest.raises(ToolError, match="not running"):
        _run(registry, "mcp__fake__echo", {"text": "x"}, tmp_path)


def test_a_call_that_hangs_times_out(started, tmp_path):
    _, registry = started(_server("slow", tool_timeout_s=1.0))
    began = time.monotonic()
    with pytest.raises(ToolError, match="tools/call timed out after 1s"):
        _run(registry, "mcp__fake__echo", {"text": "x"}, tmp_path)
    assert time.monotonic() - began < 10


def test_a_call_can_be_interrupted(started, tmp_path):
    _, registry = started(_server("slow"))
    deadline = time.monotonic() + 0.5
    with pytest.raises(Cancelled):
        _run(registry, "mcp__fake__echo", {"text": "x"}, tmp_path,
             cancelled=lambda: time.monotonic() > deadline)


def test_one_broken_server_leaves_the_others(started):
    found, registry = started(_server(), _server("noinit", name="stuck", startup_timeout_s=1.0),
                              McpServerConfig(name="gone", command="no-such-program-foundry"))
    assert list(found.tools) == ["fake"] and set(found.failures) == {"stuck", "gone"}
    assert "mcp__fake__echo" in registry.names()


def test_a_disabled_server_is_not_started(started):
    found, _ = started(_server(enabled=False))
    assert found.tools == {} and found.failures == {} and found.clients == []


# --- names, environment, and the end of a session ----------------------------------------


def test_names_every_provider_accepts():
    assert tool_name("docs", "search.v2") == "mcp__docs__search_v2"
    long = tool_name("server", "t" * 80)
    assert len(long) == 64 and long != tool_name("server", "t" * 81)
    assert tool_name("a", "b c/d") == "mcp__a__b_c_d"


def test_a_name_already_taken_is_not_offered_twice(started, monkeypatch):
    import foundry.core.mcp as mcp

    monkeypatch.setattr(mcp, "tool_name", lambda server, tool: "read_file")
    found, registry = started(_server())
    assert found.tools["fake"] == []
    assert found.skipped[0] == "fake: echo: its name read_file is taken"
    assert registry.get("read_file").kind is ToolKind.READ_ONLY


def test_the_server_gets_a_scrubbed_environment_and_what_it_is_given(started, tmp_path):
    environ = {**os.environ, "MY_API_TOKEN": "t0ken-value-123456", "DOCS_TOKEN": "d0cs-123456",
               "PLAIN_SETTING": "visible?"}
    _, registry = started(_server(env_vars=("DOCS_TOKEN",), env=(("LOG_LEVEL", "debug"),)),
                          environ=environ)
    seen = json.loads(_run(registry, "mcp__fake__env", {
        "names": ["MY_API_TOKEN", "DOCS_TOKEN", "LOG_LEVEL", "PLAIN_SETTING"]}, tmp_path).content)
    assert seen == {"MY_API_TOKEN": None, "DOCS_TOKEN": "d0cs-123456", "LOG_LEVEL": "debug",
                    "PLAIN_SETTING": None}


def test_closing_ends_the_server_and_what_it_started(started, tmp_path):
    found, registry = started(_server("spawn"))
    child = int(_run(registry, "mcp__fake__echo", {"text": "x"}, tmp_path).content)
    process = found.clients[0]._process
    found.close()
    assert process.poll() is not None
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        handle = kernel32.OpenProcess(0x1000, False, child)  # QUERY_LIMITED_INFORMATION
        code = ctypes.c_ulong()
        alive = bool(handle) and kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) \
            and code.value == 259  # STILL_ACTIVE
        if handle:
            kernel32.CloseHandle(handle)
        assert not alive
    found.close()   # twice is harmless


def test_a_client_that_never_started_closes_quietly(tmp_path):
    StdioClient(_server(), tmp_path).close()
    with pytest.raises(McpError, match="not running"):
        StdioClient(_server(), tmp_path).notify("x")


def test_tools_per_server_are_capped(started):
    found, _ = started(_server("many"))
    assert len(found.tools["fake"]) == MAX_TOOLS_PER_SERVER
    assert found.tools["fake"][-1] == f"mcp__fake__t{MAX_TOOLS_PER_SERVER - 1}"


# --- the policy ---------------------------------------------------------------------------


def _op(registry, name="mcp__fake__echo", args=None):
    return registry.get(name).validate(args or {"text": "hi"})


def test_a_call_is_asked_about_and_refused_where_nothing_may_change(started):
    _, registry = started(_server())
    op = _op(registry)
    assert PolicyEngine(mode=Mode.DEFAULT).evaluate(op)[0].verdict is Verdict.ASK
    assert PolicyEngine(mode=Mode.ACCEPT_EDITS).evaluate(op)[0].verdict is Verdict.ASK
    assert PolicyEngine(mode=Mode.PLAN).evaluate(op)[0].verdict is Verdict.DENY
    assert PolicyEngine(mode=Mode.DONT_ASK).evaluate(op)[0].verdict is Verdict.DENY
    review = PolicyEngine(mode=Mode.ACCEPT_EDITS, read_only="a review")
    assert review.evaluate(op)[0].verdict is Verdict.DENY


def test_a_server_the_user_vouches_for_is_offered_in_plan_mode_and_still_asks(started):
    _, registry = started(_server(read_only=True))
    op = _op(registry)
    assert op.kind is ToolKind.READ_ONLY
    decision = PolicyEngine(mode=Mode.PLAN).evaluate(op)[0]
    assert decision.verdict is Verdict.ASK and decision.step == 6


def test_a_rule_can_allow_a_whole_server_or_one_tool_with_some_arguments(started):
    _, registry = started(_server())
    everything = PolicyEngine(rules=[*builtin_rules(), Rule(
        tool="mcp__fake__*", pattern="*", verdict=Verdict.ALLOW)], mode=Mode.DONT_ASK)
    assert everything.evaluate(_op(registry))[0].verdict is Verdict.ALLOW
    assert everything.evaluate(_op(registry, "mcp__fake__fails", {}))[0].verdict is Verdict.ALLOW

    narrow = PolicyEngine(rules=[*builtin_rules(), Rule(
        tool="mcp__fake__echo", pattern='{"text": "safe*"}', verdict=Verdict.ALLOW)],
        mode=Mode.DONT_ASK)
    assert narrow.evaluate(_op(registry, args={"text": "safe one"}))[0].verdict is Verdict.ALLOW
    assert narrow.evaluate(_op(registry, args={"text": "other"}))[0].verdict is Verdict.DENY

    other = PolicyEngine(rules=[Rule(tool="mcp__web__*", pattern="*", verdict=Verdict.ALLOW)],
                         mode=Mode.DONT_ASK)
    assert other.evaluate(_op(registry))[0].verdict is Verdict.DENY


def test_a_tool_pattern_is_matched_case_sensitively_and_dead_ones_are_found():
    policy = PolicyEngine(rules=[
        Rule(tool="MCP__fake__*", pattern="*", verdict=Verdict.ALLOW, layer=Layer.USER),
        Rule(tool="mcp__fake__*", pattern="*", verdict=Verdict.DENY, layer=Layer.USER),
        Rule(tool="*", pattern="rm *", verdict=Verdict.DENY, layer=Layer.USER),
    ])
    dead = policy.dead_rules(["read_file", "mcp__fake__echo"])
    assert [r.tool for r in dead] == ["MCP__fake__*"]


def test_through_the_runtime_the_user_is_asked_and_the_answer_holds(started, tmp_path):
    _, registry = started(_server())
    runtime, session, _ = _core(tmp_path, [
        turn(call("c1", "mcp__fake__echo", {"text": "via the loop"})), turn(text="done")])
    for name in registry.names():
        runtime.registry.register(registry.get(name))
    asked = []
    runtime.approval = lambda request: asked.append(request) or ApprovalChoice.ONCE
    runtime.policy.mode = Mode.DEFAULT
    runtime.run_turn("echo it")
    session.close()
    assert asked[0].tool == "mcp__fake__echo" and asked[0].display.startswith("fake: echo")
    reply = next(r for r in SessionStore.read_records(session.path)
                 if r.type == EventType.TOOL_REPLY)
    assert reply.payload["content"] == "via the loop"


# --- configuration and the CLI --------------------------------------------------------------


def _config(home, text):
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return home


def _entry(mode="normal", extra=""):
    exe, script = json.dumps(sys.executable), json.dumps(SERVER)
    return f'[mcp_servers.fake]\ncommand = {exe}\nargs = [{script}, "{mode}"]\n{extra}'


def test_the_users_config_names_its_servers(tmp_path):
    config = load_config(home=_config(tmp_path / "home", _entry(extra=(
        'env = { LOG_LEVEL = "debug" }\nenv_vars = ["DOCS_TOKEN"]\nstartup_timeout_s = 5\n'
        "tool_timeout_s = 30\nread_only = true\n"))))
    server = config.mcp_servers["fake"]
    assert server.args == (SERVER, "normal") and server.env == (("LOG_LEVEL", "debug"),)
    assert server.env_vars == ("DOCS_TOKEN",) and server.read_only
    assert (server.startup_timeout_s, server.tool_timeout_s) == (5.0, 30.0)


@pytest.mark.parametrize("extra, message", [
    ("comand = 'x'\n", "no setting 'comand'"),
    ("env_vars = 'x'\n", "env_vars must be a list of strings"),
    ("env = { A = 1 }\n", "env must be a table of strings"),
    ("tool_timeout_s = 0\n", "tool_timeout_s must be a number of seconds above 0"),
    ("startup_timeout_s = 1000\n", "at most 300"),
    ("read_only = 'yes'\n", "read_only must be true or false"),
])
def test_a_bad_entry_is_refused(tmp_path, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_config(home=_config(tmp_path / "home", _entry(extra=extra)))


def test_a_server_needs_a_command_and_a_plain_name(tmp_path):
    with pytest.raises(ConfigError, match="command must name the program"):
        load_config(home=_config(tmp_path / "h1", "[mcp_servers.x]\nargs = []\n"))
    with pytest.raises(ConfigError, match="a server name is letters"):
        load_config(home=_config(tmp_path / "h2", '[mcp_servers."a b"]\ncommand = "x"\n'))


def test_a_repository_may_not_start_a_server(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(_entry(), encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not start MCP servers"):
        load_config(workspace, home=_config(tmp_path / "home", ""))


def test_a_profile_may_add_one(tmp_path):
    home = _config(tmp_path / "home", _entry().replace("[mcp_servers.fake]",
                                                       "[profiles.p.mcp_servers.fake]"))
    assert load_config(home=home).mcp_servers == {}
    assert list(load_config(home=home, profile="p").mcp_servers) == ["fake"]


def test_a_session_starts_them_records_them_and_ends_them(repo, tmp_path):
    home = _config(tmp_path / "home", _entry() + _entry("noinit", "startup_timeout_s = 1\n")
                   .replace("mcp_servers.fake", "mcp_servers.stuck"))
    wiring = _wire(repo, home, [])
    assert "mcp__fake__echo" in wiring.runtime.registry.names()
    process = wiring.mcp.clients[0]._process
    shown = _shown(_repl(wiring, "/status"))          # closes the session at /exit
    assert "fake: 5 tool(s); stuck: not available (initialize timed out after 1s)" in shown
    assert process.poll() is not None

    probes = [r.payload for r in SessionStore.read_records(wiring.session.path)
              if r.type == EventType.CAPABILITY_PROBE]
    assert probes[0] == {"mcp_server": "fake", "tools": wiring.mcp.tools["fake"]}
    assert probes[1]["mcp_server"] == "stuck" and "timed out" in probes[1]["error"]
    screen = wiring.renderer.console.file.getvalue()
    assert "MCP server fake: 5 tool(s)" in screen and "MCP server stuck is not available" in screen


def test_a_rule_for_a_servers_tool_is_not_called_dead(repo, tmp_path):
    home = _config(tmp_path / "home", _entry() + '\n[[permissions]]\ntool = "mcp__fake__*"\n'
                   'pattern = "*"\ndecision = "allow"\n')
    out = io.StringIO()
    wiring = app.build(repo, home=home, console=Console(file=out, width=200),
                       overrides={"base_url": "http://127.0.0.1:9/v1", "model": "fake"})
    app._close(wiring, app.TerminalStatus.CANCELLED, "done")
    assert "can never match" not in out.getvalue()


def test_a_failed_build_still_ends_the_servers(repo, tmp_path, monkeypatch):
    home = _config(tmp_path / "home", _entry() + '\n[backend]\nprotocol = "carrier-pigeon"\n')
    started_clients = []
    real = app.start_servers

    def spy(*args, **kwargs):
        found = real(*args, **kwargs)
        started_clients.extend(found.clients)
        return found

    monkeypatch.setattr(app, "start_servers", spy)
    with pytest.raises(ConfigError, match="unknown protocol"):
        app.build(repo, home=home, console=Console(file=io.StringIO()))
    assert started_clients and all(not c.running for c in started_clients)
