"""``foundry mcp`` -- the MCP servers in the user's config.toml, listed, shown
with their secrets hidden, added and removed -- and ``foundry mcp-server``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from rich.console import Console
from rich.markup import escape

from foundry.cli.render import safe
from foundry.core.config import load_config, user_dir
from foundry.core.config_edit import add_server, remove_server
from foundry.core.errors import ConfigError
from foundry.core.redaction import default_redactor
from foundry.core.winapi import looks_like_credential


NO_SERVERS = ("[dim]no MCP servers: `foundry mcp add NAME -- COMMAND` adds one, or "
              "`foundry mcp add NAME --url URL`[/dim]")


def cmd_mcp(args: argparse.Namespace) -> int:
    """``foundry mcp list | get | add | remove``, as ``codex mcp``: the servers
    in the user's own config.toml, from the command line. Listing starts none
    of them -- /mcp in a session says how each is."""
    console = Console()
    path = user_dir() / "config.toml"
    action = getattr(args, "mcp_command", None) or "list"
    if action == "get":
        return _mcp_get(args, console)
    if action == "add":
        add_server(path, args.name, _server_entry(args))
        console.print(f"[green]added[/green] MCP server {escape(args.name)} to "
                      f"{escape(str(path))}; a new session starts it")
        return 0
    if action == "remove":
        remove_server(path, args.name)
        console.print(f"[green]removed[/green] MCP server {escape(args.name)} from "
                      f"{escape(str(path))}")
        return 0
    servers = load_config(home=user_dir(), profile=getattr(args, "profile", None)).mcp_servers
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps([_server_record(s) for s in servers.values()],
                                    indent=2) + "\n")
        return 0
    if not servers:
        console.print(NO_SERVERS)
    for server in servers.values():
        # As /mcp shows it (D-064, D-081): a program, not its arguments; an
        # address's host, not its path.
        state = "enabled" if server.enabled else "disabled"
        kind = "read-only" if server.read_only else "may change things"
        console.print(f"[bold]{escape(server.name)}[/bold] [dim]({escape(server.shown_as())})"
                      f"[/dim] {state}, {kind}", soft_wrap=True)
        said = []
        if server.enabled_tools is not None:
            said.append("only " + (", ".join(server.enabled_tools) or "no tools"))
        if server.disabled_tools:
            said.append("never " + ", ".join(server.disabled_tools))
        if server.env_vars:
            said.append("passes " + ", ".join(server.env_vars))
        if server.bearer_token_env_var:
            said.append(f"token from {server.bearer_token_env_var}")
        if said:
            console.print(f"  [dim]{safe('; '.join(said))}[/dim]", soft_wrap=True)
    return 0


# What `foundry mcp get` shows in place of a value a credential's name marks.
HIDDEN = "[hidden]"


def _mcp_get(args: argparse.Namespace, console: Console) -> int:
    """``foundry mcp get NAME``, as ``codex mcp get``: one server's settings
    in full -- what the listing leaves out, its arguments, environment and
    address -- except a value a credential's name marks, and what the
    redactor knows to be one."""
    servers = load_config(home=user_dir(), profile=getattr(args, "profile", None)).mcp_servers
    server = servers.get(args.name)
    if server is None:
        known = f"; it names {', '.join(sorted(servers))}" if servers else ""
        raise ConfigError(f"your configuration has no MCP server named {args.name}{known}")
    scrub = default_redactor().scrub
    shown = _shown_settings(server, scrub)
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({**_server_record(server), **shown}, indent=2) + "\n")
        return 0
    state = "enabled" if server.enabled else "disabled"
    kind = "read-only" if server.read_only else "may change things"
    console.print(f"[bold]{escape(server.name)}[/bold] [dim]({'http' if server.url else 'stdio'})"
                  f"[/dim] {state}, {kind}", soft_wrap=True)
    lines = [("url", shown["url"]) if server.url else ("command", shown["command"])]
    if shown["args"]:
        import subprocess

        lines.append(("args", subprocess.list2cmdline(shown["args"])))
    if shown["cwd"]:
        lines.append(("cwd", shown["cwd"]))
    if shown["env"]:
        lines.append(("env", ", ".join(f"{key}={value}" for key, value in shown["env"].items())))
    if server.env_vars:
        lines.append(("passes", ", ".join(server.env_vars)))
    if server.bearer_token_env_var:
        lines.append(("token", f"from {server.bearer_token_env_var}"))
    headers = [f"{key}: {value}" for key, value in shown["http_headers"].items()]
    headers += [f"{key} from {variable}" for key, variable in server.env_http_headers]
    if headers:
        lines.append(("headers", ", ".join(headers)))
    tools = []
    if server.enabled_tools is not None:
        tools.append("only " + (", ".join(server.enabled_tools) or "no tools"))
    if server.disabled_tools:
        tools.append("never " + ", ".join(server.disabled_tools))
    lines.append(("tools", "; ".join(tools) or "all it lists"))
    lines.append(("timeouts", f"start {server.startup_timeout_s:g}s, each call "
                              f"{server.tool_timeout_s:g}s"))
    lines.append(("remove", f"foundry mcp remove {server.name}"))
    for label, value in lines:
        console.print(f"  [dim]{label:<9}[/dim]{safe(value)}", soft_wrap=True)
    return 0


def _shown_settings(server, scrub) -> dict[str, Any]:
    """The settings the listing leaves out, as `mcp get` shows them."""
    return {"command": scrub(server.command) if server.command else None,
            "args": _shown_args(server.args, scrub),
            "cwd": scrub(server.cwd) if server.cwd else None,
            "http_headers": {key: scrub(value) for key, value in server.http_headers},
            "env_http_headers": dict(server.env_http_headers),
            "env": {key: HIDDEN if looks_like_credential(key) else scrub(value)
                    for key, value in server.env},
            "url": _shown_url(server.url, scrub) if server.url else None}


def _shown_args(args: tuple[str, ...], scrub) -> list[str]:
    """A value a credential's name marks -- ``--api-key X``, ``--token=X``,
    ``API_KEY=X`` -- is shown as hidden. An option so named that takes no
    value hides the argument after it all the same: the cautious mistake."""
    shown: list[str] = []
    hide = False
    for arg in args:
        if hide:
            shown.append(HIDDEN)
            hide = False
            continue
        name, sep, _ = arg.partition("=")
        if (sep or arg.startswith("-")) and looks_like_credential(name):
            shown.append(f"{name}={HIDDEN}" if sep else arg)
            hide = not sep
            continue
        shown.append(scrub(arg))
    return shown


def _shown_url(url: str, scrub) -> str:
    """An address without a query value a credential's name marks --
    ``?api_key=X``. The rest is shown as written; the configuration refuses a
    user name or password in it."""
    import urllib.parse

    parts = urllib.parse.urlsplit(url)
    query = "&".join(_shown_pair(pair, urllib.parse.unquote_plus)
                     for pair in parts.query.split("&"))
    return scrub(urllib.parse.urlunsplit(parts._replace(query=query)))


def _shown_pair(pair: str, unquote) -> str:
    key, sep, _ = pair.partition("=")
    return f"{key}={HIDDEN}" if sep and looks_like_credential(unquote(key)) else pair


def cmd_mcp_server(args: argparse.Namespace) -> int:
    """Foundry as an MCP server over stdio (D-103)."""
    from foundry.cli.mcp_server import serve_stdio

    return serve_stdio(args)


def _server_entry(args: argparse.Namespace) -> dict[str, Any]:
    """The ``[mcp_servers.<name>]`` settings ``foundry mcp add`` was given."""
    command = list(args.server_command)
    entry: dict[str, Any] = {}
    if args.url is not None:
        if command:
            raise ConfigError("a server is a program Foundry starts or an address it reaches: "
                              "give --url or a command after --, not both")
        if args.env or args.env_var:
            raise ConfigError("--env and --env-var are for a server Foundry starts, not one "
                              "at a --url")
        entry["url"] = args.url
        if args.bearer_token_env_var:
            entry["bearer_token_env_var"] = args.bearer_token_env_var
    else:
        if not command:
            raise ConfigError("give the command that starts the server after --, or its --url")
        if args.bearer_token_env_var:
            raise ConfigError("--bearer-token-env-var is for a server at a --url")
        entry["command"] = command[0]
        if command[1:]:
            entry["args"] = command[1:]
        env: dict[str, str] = {}
        for item in args.env or []:
            key, sep, value = item.partition("=")
            if not sep or not key:
                raise ConfigError("--env takes KEY=VALUE")   # not echoed: it may be a secret
            if looks_like_credential(key):
                # Written into config.toml, and readable on this command
                # line by other processes meanwhile.
                raise ConfigError(f"{key} looks like a secret, and --env writes its value into "
                                  f"config.toml; set it in your environment and pass it "
                                  f"through with --env-var {key}")
            env[key] = value
        if env:
            entry["env"] = env
        if args.env_var:
            entry["env_vars"] = list(args.env_var)
    if args.read_only:
        entry["read_only"] = True
    return entry


def _server_record(server) -> dict[str, Any]:
    """``foundry mcp list --json``: what the listing shows, as data."""
    return {
        "name": server.name,
        "transport": "http" if server.url else "stdio",
        "where": server.shown_as(),
        "enabled": server.enabled,
        "read_only": server.read_only,
        "enabled_tools": (list(server.enabled_tools)
                          if server.enabled_tools is not None else None),
        "disabled_tools": list(server.disabled_tools),
        "env_vars": list(server.env_vars),
        "bearer_token_env_var": server.bearer_token_env_var or None,
        "startup_timeout_s": server.startup_timeout_s,
        "tool_timeout_s": server.tool_timeout_s,
    }
