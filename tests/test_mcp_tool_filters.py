"""Which of an MCP server's tools are offered (D-080): ``enabled_tools`` and
``disabled_tools`` in the user's ``[mcp_servers.<name>]``, as codex names
them.

What carries the feature: a tool left out is not registered at all -- the
model never sees it, so it cannot be called -- a name the server does not
offer is said (a typo would quietly hide a tool, or keep one), and what was
left out is the user's choice, shown and recorded, not a warning.
"""

from __future__ import annotations

import pytest

from foundry.core.config import McpServerConfig, load_config
from foundry.core.errors import ConfigError
from foundry.core.session import EventType, SessionStore

from test_mcp import _config, _entry, _server, started  # noqa: F401  (fixture)
from test_resume import _wire, repo  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

ALL = ["mcp__fake__echo", "mcp__fake__add_numbers", "mcp__fake__env", "mcp__fake__mixed",
       "mcp__fake__fails"]


# --- the setting -------------------------------------------------------------------------


@pytest.mark.parametrize("enabled, disabled, remote, offered", [
    (None, (), "echo", True),
    (("echo",), (), "echo", True),
    (("echo",), (), "env", False),
    (None, ("env",), "env", False),
    (None, ("env",), "echo", True),
    (("echo", "env"), ("env",), "env", False),           # never wins over only
    ((), (), "echo", False),                              # an empty list offers nothing
])
def test_only_these_and_never_those(enabled, disabled, remote, offered):
    server = McpServerConfig(name="s", command="x", enabled_tools=enabled,
                             disabled_tools=disabled)
    assert server.offers(remote) is offered


def test_the_users_config_sets_them(tmp_path):
    config = load_config(home=_config(tmp_path / "home", _entry(extra=(
        'enabled_tools = ["echo", "env"]\ndisabled_tools = ["env"]\n'))))
    server = config.mcp_servers["fake"]
    assert server.enabled_tools == ("echo", "env") and server.disabled_tools == ("env",)
    plain = load_config(home=_config(tmp_path / "plain", _entry())).mcp_servers["fake"]
    assert plain.enabled_tools is None and plain.disabled_tools == ()


@pytest.mark.parametrize("extra, message", [
    ("enabled_tools = 'echo'\n", "enabled_tools must be a list of strings"),
    ("disabled_tools = [1]\n", "disabled_tools must be a list of strings"),
])
def test_anything_but_a_list_of_names_is_refused(tmp_path, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_config(home=_config(tmp_path / "home", _entry(extra=extra)))


# --- what is offered ---------------------------------------------------------------------


def test_only_the_enabled_ones_are_registered(started):
    # The server's own names: its add.numbers is offered as mcp__fake__add_numbers.
    found, registry = started(_server(enabled_tools=("echo", "add.numbers")))
    assert found.tools["fake"] == ["mcp__fake__echo", "mcp__fake__add_numbers"]
    assert "mcp__fake__env" not in registry.names()
    assert found.left_out["fake"] == ["env", "mixed", "fails"]
    assert found.skipped == []                            # a choice, not a warning


def test_the_disabled_ones_are_not(started):
    found, registry = started(_server(disabled_tools=("env", "fails")))
    assert found.tools["fake"] == ["mcp__fake__echo", "mcp__fake__add_numbers",
                                   "mcp__fake__mixed"]
    assert "mcp__fake__fails" not in registry.names()
    assert found.left_out["fake"] == ["env", "fails"]


def test_without_either_every_tool_is_offered(started):
    found, _ = started(_server())
    assert found.tools["fake"] == ALL and found.left_out == {}


def test_a_name_the_server_does_not_offer_is_said(started):
    found, _ = started(_server(enabled_tools=("echo", "ecoh"), disabled_tools=("nope",)))
    assert found.tools["fake"] == ["mcp__fake__echo"]
    assert found.skipped == [
        "fake: enabled_tools names 'ecoh', which the server does not offer",
        "fake: disabled_tools names 'nope', which the server does not offer"]


# --- shown and recorded ------------------------------------------------------------------


def test_mcp_shows_what_was_left_out(repo, tmp_path):
    home = _config(tmp_path / "home", _entry(extra='enabled_tools = ["echo"]\n'))
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/mcp"))
    assert "  mcp__fake__echo " in shown and "mcp__fake__env" not in shown
    assert "  [dim]left out by your config: add.numbers, env, mixed, fails[/dim]" in shown


def test_the_session_records_and_says_it(repo, tmp_path):
    home = _config(tmp_path / "home", _entry(extra='disabled_tools = ["fails"]\n'))
    wiring = _wire(repo, home, [])
    path = wiring.session.path
    probes = [r.payload for r in SessionStore.read_records(path)
              if r.type == EventType.CAPABILITY_PROBE and "mcp_server" in r.payload]
    assert probes == [{"mcp_server": "fake", "tools": ALL[:4], "left_out": ["fails"]}]
    _repl(wiring)
