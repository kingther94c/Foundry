"""The commands that show or change how the session runs -- its mode, model
and effort -- and what it has: skills, hooks and MCP servers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from foundry.cli.render import safe, sanitize
from foundry.core.config import effort_levels
from foundry.core.errors import FoundryError
from foundry.core.events import Notice
from foundry.core.hooks import HOOKS_FILE, UNMATCHED_EVENTS, events_named
from foundry.core.policy.engine import Mode
from foundry.core.prompts import mode_line, system_prompt
from foundry.core.session import EventType
from foundry.core.skills import REPOSITORY_SKILLS, YOURS
from foundry.core.tools.base import ToolKind

if TYPE_CHECKING:
    from foundry.cli.wiring import Wiring


# --- /mode and /model (D-047) ----------------------------------------------------------


# Either spelling: `accept_edits` as the config writes it, `accept-edits` as a
# command line would.
_MODE_NAMES = {m.value: m for m in Mode} | {m.value.replace("_", "-"): m for m in Mode}

# A model id as providers and gateways spell them: `claude-opus-5-5`,
# `us.anthropic.claude-...:0`, `openai/gpt-5`, `qwen2.5-coder:32b`.
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


# At most this many of the endpoint's models are shown (D-067).
MODELS_SHOWN = 200


def show_models(backend, current: str, console: Console) -> None:
    """The models the endpoint lists, the session's in bold (D-067). Asked
    for each time, with the session's own endpoint and credential; what comes
    back is shown, never sent to the model or journaled. A name Foundry would
    not take in /model is left out, and counted."""
    lister = getattr(backend, "list_models", None)
    if lister is None:
        console.print("[dim]this backend lists no models[/dim]")
        return
    try:
        listed = lister()
    except KeyboardInterrupt:
        console.print("[yellow]stopped asking the endpoint for its models[/yellow]")
        return
    except FoundryError as exc:
        console.print(f"[yellow]the endpoint did not list its models: "
                      f"{escape(sanitize(str(exc)))}[/yellow]", soft_wrap=True)
        return
    usable = [m for m in listed if _MODEL_NAME.fullmatch(m)]
    if not usable:
        console.print("[dim]the endpoint lists no models[/dim]")
    else:
        shown = ", ".join(f"[bold]{escape(m)}[/bold]" if m == current else escape(m)
                          for m in usable[:MODELS_SHOWN])
        more = len(usable) - MODELS_SHOWN
        console.print(f"the endpoint lists {len(usable)}: {shown}"
                      + (f", and {more} more" if more > 0 else ""))
    left_out = len(listed) - len(usable)
    if left_out:
        console.print(f"[dim]{left_out} with a name that is not a model name left "
                      "out[/dim]")


def tell_model(wiring: "Wiring", text: str) -> None:
    """Say ``text`` to the model with the user's next message: the
    conversation grows, and nothing already sent is rewritten."""
    wiring.note = f"{wiring.note}\n\n{text}" if wiring.note else text


def set_mode(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/mode [NAME]``: show the mode, or change it -- what ``--mode`` sets
    at the start, chosen again mid-session. Only the user can: commands are
    read from what they type and nowhere else.

    The permissions paragraph is built again, as /approve does (D-038), so the
    model is never told rules that no longer hold; the change is journaled
    and said to the model with the next message.
    """
    runtime = wiring.runtime
    policy = runtime.policy
    name = arguments.strip().lower()
    if not name:
        console.print(f"this session is in [bold]{policy.mode.value}[/bold] mode")
        for mode in Mode:
            console.print(f"  {mode.value:<13} {escape(mode_line(mode))}", soft_wrap=True)
        console.print("[dim]usage: /mode NAME[/dim]")
        return None
    mode = _MODE_NAMES.get(name)
    if mode is None:
        console.print(f"[yellow]there is no {escape(name)!r} mode; the modes are "
                      f"{', '.join(m.value for m in Mode)}[/yellow]")
        return None
    if mode is policy.mode:
        console.print(f"[dim]already in {mode.value} mode[/dim]")
        return None
    before = policy.mode
    policy.mode = mode
    runtime.context.system_prompt = system_prompt(policy)
    wiring.session.append(EventType.MODE_CHANGE, {"from": before.value, "to": mode.value,
                                                  "by": "repl",
                                                  "reason": "the user typed /mode"})
    wiring.chosen["mode"] = mode
    then = "; type /approve when its plan is ready" if mode is Mode.PLAN else ""
    runtime.events.emit(Notice(f"this session is in {mode.value} mode now{then}"))
    tell_model(wiring, f"The user switched this session from {before.value} to "
                       f"{mode.value} mode. {mode_line(mode)}")
    return None


def set_model(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/model [NAME]``: show the model, or change it for the rest of the
    session -- another model behind the same endpoint and credential. A
    different endpoint is a different session: ``--profile``.

    The system prompt is left as it is: the request after the change sends
    the same conversation to the new model, with a line saying so.
    """
    runtime = wiring.runtime
    backend = wiring.config.backend
    name = arguments.strip()
    if not name:
        console.print(f"this session uses [bold]{escape(runtime.model)}[/bold] "
                      f"({backend.protocol}, profile {escape(backend.name)})")
        show_models(runtime.backend, runtime.model, console)
        console.print("[dim]usage: /model NAME -- another model on the same endpoint; for "
                      "another endpoint, start a session with --profile[/dim]")
        return None
    if not _MODEL_NAME.fullmatch(name):
        console.print("[yellow]a model name is letters, digits and . _ : / @ + -, starting "
                      "with a letter or digit, at most 200 characters[/yellow]")
        return None
    if name == runtime.model:
        console.print(f"[dim]already on {escape(name)}[/dim]")
        return None
    before = runtime.model
    runtime.model = name
    backend.model = name            # what /status shows, and what /new starts on
    wiring.session.append(EventType.MODEL_CHANGE, {"from": before, "to": name, "by": "repl"})
    wiring.chosen["model"] = name
    runtime.events.emit(Notice(f"this session uses {name} now (it was {before})"))
    tell_model(wiring, f"The user switched this session from the model {before} to {name}.")
    return None


# What an unset effort is called: none is sent, and the API decides (D-061).
API_DEFAULT_EFFORT = "the API's default"


def set_effort(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/effort [LEVEL]``: show how hard the model reasons, or change it for
    the rest of the session (D-061), as codex's /model offers. ``default``
    sends no effort, leaving it to the API. Only the user can: commands are
    read from what they type and nowhere else.
    """
    backend = wiring.config.backend
    levels = effort_levels(backend.protocol)
    level = arguments.strip().lower()
    current = backend.effort or API_DEFAULT_EFFORT
    if not level:
        console.print(f"reasoning effort: [bold]{escape(current)}[/bold]")
        console.print(f"[dim]usage: /effort LEVEL -- {', '.join(levels)}, or default[/dim]")
        return None
    chosen = None if level == "default" else level
    if chosen is not None and chosen not in levels:
        console.print(f"[yellow]{backend.protocol} takes {', '.join(levels)}, or "
                      "default[/yellow]")
        return None
    if chosen == backend.effort:
        console.print(f"[dim]already {escape(current)}[/dim]")
        return None
    wiring.session.append(EventType.EFFORT_CHANGE,
                          {"from": backend.effort, "to": chosen, "by": "repl"})
    backend.effort = chosen                 # what /status shows
    wiring.runtime.backend.effort = chosen  # what the next request sends
    wiring.chosen["effort"] = chosen        # what /new starts with
    wiring.runtime.events.emit(Notice(
        f"reasoning effort: {chosen or API_DEFAULT_EFFORT} now (it was {current})"))
    return None


# --- /mcp (D-064) ----------------------------------------------------------------------

# How much of a tool's description /mcp shows: enough to tell tools apart.
MCP_DESCRIPTION_SHOWN = 60

# The table's brackets escaped: as markup, [mcp_servers.NAME] was taken for a
# style and vanished from the line.
NO_MCP = ("[dim]no MCP servers: `foundry mcp add NAME -- COMMAND` adds one, as does an "
          "\\[mcp_servers.NAME] table in your config.toml[/dim]")


def show_skills(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/skills``: the skills the model may load (D-113), whose each is, and
    what is not used and why. A description is the skill's own text, so it is
    cleaned of terminal controls before it is shown."""
    skills = wiring.skills
    home = wiring.home / "skills" if wiring.home else "skills in the Foundry folder"
    if not skills.found and not skills.skipped and not skills.disabled:
        where = f"{home}" + (f" or this repository's {REPOSITORY_SKILLS.as_posix()}"
                             if wiring.trusted else "")
        console.print("[dim]no skills: a skill is a folder holding a SKILL.md, in "
                      f"{escape(str(where))}[/dim]", soft_wrap=True)
        return None
    for name, skill in sorted(skills.found.items()):
        source = "yours" if skill.source == YOURS else "the repository's"
        note = " [yellow](not in the model's list, for room)[/yellow]" if (
            name in skills.left_out) else ""
        about = sanitize(skill.description)
        about = about if len(about) <= 100 else about[:97].rstrip() + "..."
        console.print(f"[bold]${escape(name)}[/bold] [dim]({source})[/dim]{note} {escape(about)}",
                      soft_wrap=True)
    for skill in skills.hidden:
        console.print(f"[dim]{escape(skill.name)} in {escape(str(skill.folder))}: hidden by "
                      "another of that name[/dim]", soft_wrap=True)
    for skill in skills.disabled:
        console.print(f"[dim]{escape(skill.name)} in {escape(str(skill.folder))}: turned off "
                      f"by your {escape('[[skills.config]]')}[/dim]", soft_wrap=True)
    for folder, why in skills.skipped:
        console.print(f"[yellow]{escape(str(folder))}: not used, {safe(why)}[/yellow]",
                      soft_wrap=True)
    return None


def show_hooks(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/hooks``: the lifecycle hooks the session runs (D-115), as codex's
    /hooks lists them -- each one's event, matcher, command and file. They
    come from the user's own files and the machine's policy only, so there
    is no review to ask for here, as codex asks of a repository's."""
    hooks = wiring.config.active_hooks()
    off = len(wiring.config.hooks) - len(hooks)
    if off:
        # codex's hooks feature, off (D-144): the machine policy's still run.
        console.print(f"[dim]{off} hook(s) of your own files are not run: features.hooks = "
                      "false[/dim]")
    if not hooks and off:
        return None
    if not hooks:
        where = wiring.home / HOOKS_FILE if wiring.home else HOOKS_FILE
        console.print(f"[dim]no hooks: codex's hooks.json format, in {escape(str(where))} or "
                      "a \\[hooks] table of your config.toml; Foundry runs "
                      f"{events_named()}[/dim]", soft_wrap=True)
        return None
    for hook in hooks:
        background = " [dim](async)[/dim]" if hook.background else ""
        # An event codex gives no matcher runs each of its hooks (D-116).
        matcher = "" if hook.event in UNMATCHED_EVENTS else f" {escape(hook.matcher or '*')}"
        console.print(f"[bold]{hook.event}[/bold]{matcher}{background}  "
                      f"{safe(hook.command)} [dim]({escape(hook.source)}, "
                      f"{hook.timeout_s}s)[/dim]", soft_wrap=True)
    return None


def show_mcp(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/mcp``: the MCP servers the user's configuration names, whether
    each is running, and the tools it offers, as codex's /mcp lists them
    (D-064). Only a server's program is shown, not its arguments, which may
    carry a secret -- of one reached over HTTP, its scheme and host, not its
    path (D-081); what a server says of its tools is its own text, so it
    is cleaned of terminal controls before it is shown.
    """
    servers = wiring.config.mcp_servers
    mcp = wiring.mcp
    if not servers:
        console.print(NO_MCP)
        return None
    clients = {client.server.name: client for client in mcp.clients}
    for name, server in servers.items():
        if not server.enabled:
            state = "[dim]disabled[/dim]"
        elif name in mcp.failures:
            state = f"[yellow]not available:[/yellow] {safe(mcp.failures[name])}"
        elif name in clients and not clients[name].running:
            state = "[yellow]stopped:[/yellow] its tools fail until a new session starts it"
        else:
            state = "[green]running[/green]"
        console.print(f"[bold]{escape(name)}[/bold] [dim]({escape(server.shown_as())})[/dim] "
                      f"{state}",
                      soft_wrap=True)
        for tool_name in mcp.tools.get(name, ()):
            tool = wiring.runtime.registry.tools[tool_name]
            kind = "read-only" if tool.kind is ToolKind.READ_ONLY else "may change things"
            # Listed under its server, the tool need not say whose it is.
            about = sanitize(tool.description.removesuffix(f" (from the MCP server {name!r})"))
            if len(about) > MCP_DESCRIPTION_SHOWN:
                about = about[:MCP_DESCRIPTION_SHOWN - 3].rstrip() + "..."
            console.print(f"  {escape(tool_name)} [dim]{kind}[/dim] {escape(about)}",
                          soft_wrap=True)
        if name in mcp.resources:
            console.print("  [dim]and its resources, through list_mcp_resources and "
                          "read_mcp_resource[/dim]", soft_wrap=True)
        left_out = mcp.left_out.get(name, [])
        if left_out:
            # The server's own names, chosen in the user's config (D-080).
            console.print(f"  [dim]left out by your config: "
                          f"{escape(sanitize(', '.join(left_out)))}[/dim]", soft_wrap=True)
    for skipped in mcp.skipped:
        console.print(f"[yellow]not offered:[/yellow] {safe(skipped)}", soft_wrap=True)
    return None
