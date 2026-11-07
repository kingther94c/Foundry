"""``foundry mcp list | add | remove`` (D-082), as ``codex mcp``: the MCP
servers in the user's own config.toml, from the command line.

What carries it: the file is the user's, so an edit is text checked before
it is written -- the result must parse to exactly the old settings with one
server added or gone, or nothing is written -- and a secret is never put in
the file by it: a credential-shaped ``--env`` is refused for ``--env-var``,
and the listing shows a program, not its arguments, and a host, not a path.
"""

from __future__ import annotations

import json
import os
import tomllib
from types import SimpleNamespace

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.repl import NO_MCP
from foundry.core.config import load_config
from foundry.core.config_edit import add_server, remove_server, server_table
from foundry.core.errors import ConfigError
from foundry.core.policy.engine import PolicyEngine

EXIT_FAILED = 12


@pytest.fixture()
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(path))
    monkeypatch.delenv("FOUNDRY_PROFILE", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    return path


def _servers(home):
    return load_config(home=home).mcp_servers


def _foundry(capsys, *argv):
    code = app.main(list(argv))
    out = capsys.readouterr().out
    return code, " ".join(out.split())


# --- editing the file ---------------------------------------------------------------


def test_adding_to_no_file_makes_one(home):
    add_server(home / "config.toml", "docs", {"command": "uvx", "args": ["our-docs"]})
    assert (home / "config.toml").read_text(encoding="utf-8") == (
        '[mcp_servers.docs]\ncommand = "uvx"\nargs = ["our-docs"]\n')
    assert _servers(home)["docs"].args == ("our-docs",)


def test_adding_keeps_the_file_as_it_was_above_the_new_table(home):
    home.mkdir()
    before = '# mine\n[backend]\nmodel = "m"   # keep'
    (home / "config.toml").write_text(before, encoding="utf-8")
    add_server(home / "config.toml", "web", {"url": "https://mcp.example.com/mcp"})
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert text == before + '\n\n[mcp_servers.web]\nurl = "https://mcp.example.com/mcp"\n'
    config = load_config(home=home)
    assert config.backend.model == "m" and config.mcp_servers["web"].url


def test_awkward_values_are_written_as_toml(home):
    args = ['say "hi"', "C:\\tools\\x.exe", "del\x7fete", "caf\u00e9 \U0001F600", "tab\there"]
    add_server(home / "config.toml", "odd", {"command": "x", "args": args,
                                            "env": {"A B": "1", "PLAIN": "two"}})
    server = _servers(home)["odd"]
    assert list(server.args) == args and server.env == (("A B", "1"), ("PLAIN", "two"))


def test_a_name_already_there_is_refused(home):
    add_server(home / "config.toml", "docs", {"command": "uvx"})
    before = (home / "config.toml").read_bytes()
    with pytest.raises(ConfigError, match="already has an MCP server named docs"):
        add_server(home / "config.toml", "docs", {"command": "npx"})
    assert (home / "config.toml").read_bytes() == before


@pytest.mark.parametrize("name, entry, message", [
    ("docs", {"url": "ftp://x/mcp"}, "url must be an http:// or https:// address"),
    ("a b", {"command": "x"}, "a server name is letters"),
    ("web", {"url": "http://x.example/mcp", "bearer_token_env_var": "T"},
     "a bearer token is sent only over https"),
])
def test_what_reading_it_would_refuse_is_not_written(home, name, entry, message):
    with pytest.raises(ConfigError, match=message):
        add_server(home / "config.toml", name, entry)
    assert not (home / "config.toml").exists()


def test_servers_written_inline_are_left_to_the_user(home):
    home.mkdir()
    before = 'mcp_servers = { docs = { command = "uvx" } }\n'
    (home / "config.toml").write_text(before, encoding="utf-8")
    with pytest.raises(ConfigError, match="add it by hand"):
        add_server(home / "config.toml", "web", {"url": "https://x.example/mcp"})
    with pytest.raises(ConfigError, match="remove it by hand"):
        remove_server(home / "config.toml", "docs")
    assert (home / "config.toml").read_text(encoding="utf-8") == before


def test_a_file_with_crlf_keeps_its_line_ends(home):
    home.mkdir()
    (home / "config.toml").write_bytes(b'mode = "plan"\r\n')
    add_server(home / "config.toml", "docs", {"command": "uvx", "args": ["a"]})
    data = (home / "config.toml").read_bytes()
    assert data == b'mode = "plan"\r\n\r\n[mcp_servers.docs]\r\ncommand = "uvx"\r\nargs = ["a"]\r\n'
    remove_server(home / "config.toml", "docs")
    assert (home / "config.toml").read_bytes() == b'mode = "plan"\r\n'


CONFIG = """mode = "plan"

[mcp_servers.docs]
command = "uvx"
args = ["our-docs"]
# docs' own note, right under it

[mcp_servers.docs.env]
LOG = "debug"

# the tickets server, set apart
[mcp_servers.tickets]
url = "https://mcp.example.com/mcp"

[backend]
model = "m"
"""


def test_removing_takes_out_its_tables_and_nothing_else(home):
    home.mkdir()
    (home / "config.toml").write_text(CONFIG, encoding="utf-8")
    remove_server(home / "config.toml", "docs")
    assert (home / "config.toml").read_text(encoding="utf-8") == (
        'mode = "plan"\n\n# the tickets server, set apart\n[mcp_servers.tickets]\n'
        'url = "https://mcp.example.com/mcp"\n\n[backend]\nmodel = "m"\n')
    remove_server(home / "config.toml", "tickets")
    assert (home / "config.toml").read_text(encoding="utf-8") == (
        'mode = "plan"\n\n[backend]\nmodel = "m"\n')
    assert tomllib.loads((home / "config.toml").read_text(encoding="utf-8")) == {
        "mode": "plan", "backend": {"model": "m"}}


def test_a_cut_that_would_change_another_setting_is_refused(home):
    # A string whose line looks like a table: cut there, what is left parses
    # -- as a server named fake -- so only the check after the edit stops it.
    home.mkdir()
    before = '[mcp_servers.docs]\ncommand = "uvx"\nargs = ["""\n[mcp_servers.fake] # """]\n'
    (home / "config.toml").write_text(before, encoding="utf-8")
    assert tomllib.loads(before)["mcp_servers"]["docs"]["args"] == ["[mcp_servers.fake] # "]
    with pytest.raises(ConfigError, match="remove it by hand"):
        remove_server(home / "config.toml", "docs")
    assert (home / "config.toml").read_text(encoding="utf-8") == before


def test_removing_one_that_is_not_there_is_said(home):
    home.mkdir()
    (home / "config.toml").write_text(CONFIG, encoding="utf-8")
    with pytest.raises(ConfigError, match="has no MCP server named wiki"):
        remove_server(home / "config.toml", "wiki")


def test_a_failed_write_leaves_the_file_as_it_was(home, monkeypatch):
    home.mkdir()
    (home / "config.toml").write_text(CONFIG, encoding="utf-8")

    def broken(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        remove_server(home / "config.toml", "docs")
    assert (home / "config.toml").read_text(encoding="utf-8") == CONFIG
    assert sorted(p.name for p in home.iterdir()) == ["config.toml"]


def test_the_table_reads_back_as_its_settings():
    entry = {"command": "uvx", "args": ["a", "b"], "env": {"K": "v"}, "read_only": True}
    assert tomllib.loads(server_table("docs", entry)) == {"mcp_servers": {"docs": entry}}


# --- the command ---------------------------------------------------------------------


def test_add_a_program_and_its_options(home, capsys):
    code, out = _foundry(capsys, "mcp", "add", "docs", "--env", "LOG=debug", "--env-var",
                         "DOCS_TOKEN", "--read-only", "--", "uvx", "our-docs", "--from", "x")
    assert code == 0 and "added MCP server docs" in out
    server = _servers(home)["docs"]
    assert (server.command, server.args) == ("uvx", ("our-docs", "--from", "x"))
    assert server.env == (("LOG", "debug"),) and server.env_vars == ("DOCS_TOKEN",)
    assert server.read_only


def test_add_a_server_at_an_address(home, capsys):
    code, _ = _foundry(capsys, "mcp", "add", "tickets", "--url", "https://mcp.example.com/mcp",
                       "--bearer-token-env-var", "TICKETS_TOKEN")
    assert code == 0
    server = _servers(home)["tickets"]
    assert (server.url, server.bearer_token_env_var) == ("https://mcp.example.com/mcp",
                                                         "TICKETS_TOKEN")


@pytest.mark.parametrize("argv, message", [
    (["--url", "https://x.example/mcp", "--", "uvx"], "give --url or a command after --, not both"),
    (["--url", "https://x.example/mcp", "--env", "A=1"], "--env and --env-var are for a server"),
    ([], "give the command that starts the server after --, or its --url"),
    (["--bearer-token-env-var", "T", "--", "uvx"], "--bearer-token-env-var is for a server at"),
    (["--env", "API_TOKEN=sk-live-123", "--", "uvx"],
     "API_TOKEN looks like a secret, and --env writes its value into config.toml; set it "
     "in your environment and pass it through with --env-var API_TOKEN"),
    (["--env", "sk-live-123", "--", "uvx"], "--env takes KEY=VALUE"),
])
def test_add_refuses_what_does_not_go_together(home, capsys, argv, message):
    code, out = _foundry(capsys, "mcp", "add", "docs", *argv)
    assert code == EXIT_FAILED and message in out
    assert "sk-live" not in out
    assert not (home / "config.toml").exists()


def test_list_shows_where_not_what_it_is_given(home, capsys):
    _foundry(capsys, "mcp", "add", "docs", "--env", "LOG=sekrit-value", "--env-var",
             "DOCS_TOKEN", "--", "C:/tools/uvx.exe", "--key", "sekrit-arg")
    _foundry(capsys, "mcp", "add", "tickets", "--url", "https://mcp.example.com/mcp/sekrit-path",
             "--bearer-token-env-var", "TICKETS_TOKEN", "--read-only")
    with (home / "config.toml").open("a", encoding="utf-8") as fh:
        fh.write('enabled_tools = ["search"]\ndisabled_tools = ["delete"]\n')
    code, out = _foundry(capsys, "mcp", "list")
    assert code == 0
    assert "docs (uvx.exe) enabled, may change things passes DOCS_TOKEN" in out
    assert ("tickets (https://mcp.example.com) enabled, read-only only search; never delete; "
            "token from TICKETS_TOKEN") in out
    assert "sekrit" not in out
    code, plain = _foundry(capsys, "mcp")
    assert plain == out


def test_list_as_json(home, capsys):
    _foundry(capsys, "mcp", "add", "tickets", "--url", "https://mcp.example.com/mcp")
    assert app.main(["mcp", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [{
        "name": "tickets", "transport": "http", "where": "https://mcp.example.com",
        "enabled": True, "read_only": False, "enabled_tools": None, "disabled_tools": [],
        "env_vars": [], "bearer_token_env_var": None, "startup_timeout_s": 20.0,
        "tool_timeout_s": 120.0}]


def test_list_with_none_says_how_to_add_one(home, capsys):
    code, out = _foundry(capsys, "mcp", "list")
    assert code == 0 and out == ("no MCP servers: `foundry mcp add NAME -- COMMAND` adds one, "
                                 "or `foundry mcp add NAME --url URL`")


def test_remove(home, capsys):
    _foundry(capsys, "mcp", "add", "docs", "--", "uvx")
    code, out = _foundry(capsys, "mcp", "remove", "docs")
    assert code == 0 and "removed MCP server docs" in out
    assert _servers(home) == {}
    code, out = _foundry(capsys, "mcp", "remove", "docs")
    assert code == EXIT_FAILED and "has no MCP server named docs" in out


# --- found on the way ----------------------------------------------------------------


def test_the_empty_mcp_line_names_the_table():
    console = Console(record=True, width=300)
    console.print(NO_MCP)
    assert "as does an [mcp_servers.NAME] table in your config.toml" in console.export_text()


def test_always_on_a_command_with_quotes_leaves_a_config_that_reads(home):
    home.mkdir()
    policy = PolicyEngine(rules=[])
    persist = app._rule_writer(home, policy)
    persist(SimpleNamespace(tool="run_command", target='echo "hi" C:\\temp'))
    rules = [r for r in load_config(home=home).rules if r.tool == "run_command"]
    assert [r.pattern for r in rules] == ['echo "hi" C:/temp']
