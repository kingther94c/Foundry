"""/mcp: the MCP servers the user's configuration names, and the tools each
offers (D-064), as codex's /mcp lists them.

A server's arguments may carry a secret, so only its program is shown; what
a server says of its tools is its own text, cleaned before it is shown.
"""

from __future__ import annotations

import sys
from pathlib import Path

from foundry.cli.repl import MCP_DESCRIPTION_SHOWN, NO_MCP
from foundry.core.config import McpServerConfig
from foundry.core.mcp import McpServers, McpTool
from foundry.core.tools.base import ToolKind

from test_mcp import _config, _entry
from test_resume import _wire, repo  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown


def test_it_lists_each_server_and_its_tools(repo, tmp_path):
    home = _config(tmp_path / "home", _entry() + _entry(extra="read_only = true\n").replace(
        "mcp_servers.fake", "mcp_servers.docs"))
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/mcp"))
    program = Path(sys.executable).name
    assert f"[bold]fake[/bold] [dim]({program})[/dim] [green]running[/green]" in shown
    assert "  mcp__fake__echo [dim]may change things[/dim] Echo the text back." in shown
    assert "  mcp__docs__echo [dim]read-only[/dim] Echo the text back." in shown
    assert "mcp__fake__add_numbers" in shown and "Add two numbers." in shown
    assert "(from the MCP server" not in shown


def test_its_arguments_are_not_shown(repo, tmp_path):
    home = _config(tmp_path / "home", _entry())
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/mcp"))
    assert "mcp_fake_server.py" not in shown and '"normal"' not in shown


def test_one_that_did_not_start_says_why(repo, tmp_path):
    home = _config(tmp_path / "home", _entry("noinit", "startup_timeout_s = 1\n"))
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/mcp"))
    assert "[yellow]not available:[/yellow] initialize timed out after 1s" in shown


def test_one_that_stopped_says_so(repo, tmp_path):
    home = _config(tmp_path / "home", _entry())
    wiring = _wire(repo, home, [])
    process = wiring.mcp.clients[0]._process
    process.kill()
    process.wait(10)
    shown = _shown(_repl(wiring, "/mcp"))
    assert "[yellow]stopped:[/yellow] its tools fail until a new session starts it" in shown


def test_a_disabled_one_is_named_as_such(repo, tmp_path):
    home = _config(tmp_path / "home", _entry(extra="enabled = false\n"))
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/mcp"))
    assert "[bold]fake[/bold]" in shown and "[dim]disabled[/dim]" in shown
    assert "mcp__fake__echo" not in shown


def test_a_tool_not_offered_says_why(repo, tmp_path, monkeypatch):
    import foundry.core.mcp as mcp

    real = mcp.tool_name
    monkeypatch.setattr(mcp, "tool_name",
                        lambda server, tool: "read_file" if tool == "echo" else real(server, tool))
    wiring = _wire(repo, _config(tmp_path / "home", _entry()), [])
    shown = _shown(_repl(wiring, "/mcp"))
    assert "[yellow]not offered:[/yellow] fake: echo: its name read_file is taken" in shown


def test_without_servers_it_says_how_to_add_one(repo, tmp_path):
    shown = _shown(_repl(_wire(repo, tmp_path / "home", []), "/mcp"))
    assert NO_MCP in shown


def test_what_a_server_says_is_cleaned_and_cut(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    server = McpServerConfig(name="evil", command="C:/tools/evil.exe", args=("--token", "s3cr3t"))
    wiring.config.mcp_servers = {"evil": server}
    found = McpServers()
    found.tools["evil"] = ["mcp__evil__x"]
    description = "\x1b[31m[bold]red[/bold]\x1b[0m " + "y" * 100
    wiring.runtime.registry.register(McpTool(
        name="mcp__evil__x", kind=ToolKind.MUTATOR, client=None, server="evil", remote="x",
        description=description + " (from the MCP server 'evil')", parameters={}))
    wiring.mcp = found
    shown = _shown(_repl(wiring, "/mcp"))
    line = next(line for line in shown.splitlines() if "mcp__evil__x" in line)
    assert "\x1b" not in line and "\\[bold]red\\[/bold]" in line
    assert line.endswith("...") and "y" * (MCP_DESCRIPTION_SHOWN - 20) in line
    about = line.split("[/dim] ", 1)[1].replace("\\[", "[")
    assert len(about) == MCP_DESCRIPTION_SHOWN
    assert "evil.exe" in shown and "s3cr3t" not in shown


def test_help_lists_it(repo, tmp_path):
    shown = _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))
    assert "/mcp" in shown
