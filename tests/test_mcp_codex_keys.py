"""codex's own names for an MCP server's settings (D-110): ``startup_timeout_sec``
and ``tool_timeout_sec`` as Foundry's ``_s`` keys, and ``cwd``, the folder a
server Foundry starts runs in -- so a ``[mcp_servers.<name>]`` table copied
from codex's config.toml loads as it is.

What carries it: one setting under two names is given once; a timeout keeps
its ceiling under either; ``cwd`` is an absolute folder, for a program and not
an address, checked again when the server starts; and it comes only from the
user's own config, as every MCP server does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from foundry.cli import app
from foundry.core.config import McpServerConfig, load_config
from foundry.core.errors import ConfigError
from foundry.core.mcp import start_servers
from foundry.core.redaction import default_redactor
from foundry.core.tools.base import ToolContext
from foundry.core.tools.registry import default_registry

SERVER = str(Path(__file__).with_name("mcp_fake_server.py"))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(path))
    monkeypatch.delenv("FOUNDRY_PROFILE", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    path.mkdir()
    return path


def _server(home, extra=""):
    exe, script = json.dumps(sys.executable), json.dumps(SERVER)
    (home / "config.toml").write_text(
        f'[mcp_servers.docs]\ncommand = {exe}\nargs = [{script}, "cwd"]\n{extra}',
        encoding="utf-8")
    return load_config(home=home).mcp_servers["docs"]


def _refused(home, extra):
    with pytest.raises(ConfigError) as caught:
        _server(home, extra)
    return str(caught.value)


def _toml(path: Path) -> str:
    return json.dumps(str(path))


# --- the timeouts --------------------------------------------------------------------


def test_codexs_names_set_the_timeouts(home):
    server = _server(home, "startup_timeout_sec = 15\ntool_timeout_sec = 90.5\n")
    assert (server.startup_timeout_s, server.tool_timeout_s) == (15.0, 90.5)


def test_foundrys_names_still_do(home):
    server = _server(home, "startup_timeout_s = 7\ntool_timeout_s = 45\n")
    assert (server.startup_timeout_s, server.tool_timeout_s) == (7.0, 45.0)


def test_unsaid_they_are_foundrys_defaults(home):
    server = _server(home)
    assert (server.startup_timeout_s, server.tool_timeout_s) == (20.0, 120.0)


def test_each_name_sets_only_its_own(home):
    server = _server(home, "tool_timeout_sec = 30\n")
    assert (server.startup_timeout_s, server.tool_timeout_s) == (20.0, 30.0)
    server = _server(home, "startup_timeout_sec = 30\n")
    assert (server.startup_timeout_s, server.tool_timeout_s) == (30.0, 120.0)


@pytest.mark.parametrize("ours, theirs", [("startup_timeout_s", "startup_timeout_sec"),
                                          ("tool_timeout_s", "tool_timeout_sec")])
def test_one_setting_is_given_once(home, ours, theirs):
    message = _refused(home, f"{ours} = 5\n{theirs} = 5\n")
    assert f"mcp_servers.docs: {ours} and {theirs} are one setting" in message
    assert "give one" in message


@pytest.mark.parametrize("extra, message", [
    ("startup_timeout_sec = 301\n", "mcp_servers.docs.startup_timeout_sec must be a number "
                                    "of seconds above 0 and at most 300"),
    ("tool_timeout_sec = 3601\n", "mcp_servers.docs.tool_timeout_sec must be a number of "
                                  "seconds above 0 and at most 3600"),
    ("tool_timeout_sec = 0\n", "tool_timeout_sec must be a number of seconds above 0"),
    ("startup_timeout_sec = true\n", "startup_timeout_sec must be a number of seconds"),
    ("tool_timeout_sec = '60'\n", "tool_timeout_sec must be a number of seconds"),
])
def test_codexs_names_are_held_to_the_same_bounds(home, extra, message):
    assert message in _refused(home, extra)


def test_the_ceilings_are_reached_under_either_name(home):
    server = _server(home, "startup_timeout_sec = 300\ntool_timeout_sec = 3600\n")
    assert (server.startup_timeout_s, server.tool_timeout_s) == (300.0, 3600.0)


# --- cwd ---------------------------------------------------------------------------------


def test_cwd_is_read(home, tmp_path):
    folder = tmp_path / "docs-server"
    folder.mkdir()
    assert _server(home, f"cwd = {_toml(folder)}\n").cwd == str(folder)


def test_unsaid_there_is_none(home):
    assert _server(home).cwd == ""


@pytest.mark.parametrize("value", ['"servers/docs"', '""', '"."', "3", '["C:/x"]'])
def test_cwd_is_an_absolute_path(home, value):
    message = _refused(home, f"cwd = {value}\n")
    assert message == ("mcp_servers.docs.cwd must be the absolute path of the folder the "
                       "server runs in")


def test_cwd_is_for_a_program_foundry_starts(home, tmp_path):
    (home / "config.toml").write_text(
        f'[mcp_servers.web]\nurl = "https://example.com/mcp"\ncwd = {_toml(tmp_path)}\n',
        encoding="utf-8")
    with pytest.raises(ConfigError, match="url and cwd do not go together"):
        load_config(home=home)


def test_a_repository_still_may_not_name_a_server(home, tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    exe = json.dumps(sys.executable)
    (workspace / ".foundry" / "config.toml").write_text(
        f"[mcp_servers.x]\ncommand = {exe}\ncwd = {_toml(tmp_path)}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="may not start MCP servers"):
        load_config(workspace, home=home)


def test_a_table_copied_from_codex_loads(home, tmp_path):
    folder = tmp_path / "docs-server"
    folder.mkdir()
    server = _server(home, f"cwd = {_toml(folder)}\nstartup_timeout_sec = 10\n"
                           "tool_timeout_sec = 60\nenabled_tools = ['echo']\n")
    assert (server.cwd, server.startup_timeout_s, server.tool_timeout_s) == (
        str(folder), 10.0, 60.0)
    assert server.enabled_tools == ("echo",)


# --- where the server runs -----------------------------------------------------------------


@pytest.fixture()
def started():
    opened = []

    def start(server, workspace):
        registry = default_registry()
        found = start_servers({server.name: server}, workspace, registry)
        opened.append(found)
        return found, registry

    yield start
    for found in opened:
        found.close()


def _cwd_of(registry):
    tool = registry.get("mcp__docs__echo")
    ctx = ToolContext(workspace=None, artifacts=None, read_tracker=None)
    return tool.execute(tool.validate({"text": "x"}), ctx).content


def _docs(**extra):
    return McpServerConfig(name="docs", command=sys.executable, args=(SERVER, "cwd"), **extra)


def test_a_server_runs_in_its_cwd(started, tmp_path):
    folder = tmp_path / "docs-server"
    folder.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    found, registry = started(_docs(cwd=str(folder)), workspace)
    assert found.failures == {}
    assert Path(_cwd_of(registry)).resolve() == folder.resolve()


def test_without_one_it_runs_in_the_workspace(started, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    found, registry = started(_docs(), workspace)
    assert found.failures == {}
    assert Path(_cwd_of(registry)).resolve() == workspace.resolve()


def test_a_cwd_that_is_not_a_folder_is_reported(started, tmp_path):
    missing = tmp_path / "gone"
    found, registry = started(_docs(cwd=str(missing)), tmp_path)
    assert found.failures == {"docs": f"its cwd {missing} is not a folder"}
    assert not any(name.startswith("mcp__") for name in registry.names())
    afile = tmp_path / "a.txt"
    afile.write_text("x", encoding="utf-8")
    found, _ = started(_docs(cwd=str(afile)), tmp_path)
    assert found.failures == {"docs": f"its cwd {afile} is not a folder"}


# --- what `mcp get` shows --------------------------------------------------------------------


def test_mcp_get_shows_it(home, tmp_path, capsys):
    folder = tmp_path / "docs-server"
    folder.mkdir()
    _server(home, f"cwd = {_toml(folder)}\ntool_timeout_sec = 60\n")
    assert app.main(["mcp", "get", "docs"]) == 0
    out = capsys.readouterr().out
    assert f"cwd      {folder}" in out
    assert "each call 60s" in out
    assert app.main(["mcp", "get", "docs", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["cwd"] == str(folder) and shown["tool_timeout_s"] == 60.0


def test_mcp_get_says_nothing_of_one_unsaid(home, capsys):
    _server(home)
    assert app.main(["mcp", "get", "docs"]) == 0
    labels = [line.split()[0] for line in capsys.readouterr().out.splitlines()[1:]]
    assert labels == ["command", "args", "tools", "timeouts", "remove"]
    assert app.main(["mcp", "get", "docs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cwd"] is None


def test_mcp_get_takes_out_what_the_redactor_knows(home, capsys):
    held = "held-credential-4567"
    default_redactor().register(held)
    _server(home, f'cwd = "C:/servers/{held}"\n')
    assert app.main(["mcp", "get", "docs"]) == 0
    out = capsys.readouterr().out
    assert held not in out and "C:/servers/[redacted]" in out
    assert app.main(["mcp", "get", "docs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cwd"] == "C:/servers/[redacted]"
