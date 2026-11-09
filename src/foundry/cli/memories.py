"""``/memories`` (D-145), as codex's: what the workspace's memories are, and
whether this session uses them and is read for them.

codex's command decides, for the current chat, whether it may use existing
memories and whether it may be read for future ones; its global settings
stay as they are. So does Foundry's: ``use`` changes what this session's
instructions carry from its next request, ``generate`` is journaled, and
the pass that reads sessions goes by the last one.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from foundry.core.memories import (
    READ_MEMORIES,
    SESSIONS_DIR,
    MemoryTool,
    instructions,
    load_state,
    summary,
)
from foundry.core.session import EventType, SessionStore

if TYPE_CHECKING:
    from foundry.cli.app import Wiring

USAGE = "usage: /memories [use on|off | generate on|off]"
TURN_ON = ("memories are off: foundry features enable memories turns them on (codex's "
           "[features] memories = true), for sessions started after it")
NOT_TRUSTED = "memories are kept only in a workspace you trust, and this one is not"


def generating(wiring: "Wiring") -> bool:
    """Whether this session is read for memories: its journal's last say."""
    mode = ""
    for record in SessionStore.read_records(wiring.session.path):
        if record.type == EventType.MEMORY_MODE:
            mode = str(record.payload.get("mode", ""))
    return mode != "disabled"


def _when(at: Any) -> str:
    if not isinstance(at, (int, float)):
        return "never"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))


def _status(wiring: "Wiring", console: Console, folder: Path) -> None:
    settings = wiring.config.memory_settings
    if wiring.memories_used:
        uses = "uses them"
    elif not settings.use:
        uses = "does not use them (use_memories = false)"
    elif not summary(folder):
        uses = "has none to use yet"
    else:
        uses = "does not use them (/memories use off)"
    read = "is read for them" if generating(wiring) else "is not read for them"
    console.print(f"memories: on, in {escape(str(folder))}", soft_wrap=True)
    console.print(f"  this session {uses}, and {read} once it has been idle "
                  f"{settings.min_idle_hours}h")
    own = folder / SESSIONS_DIR
    kept = len(list(own.glob("*.md"))) if own.is_dir() else 0
    last = load_state(folder).get("last_pass") or {}
    said = (f"; last pass {_when(last.get('at'))}: {last.get('read', 0)} session(s) read"
            if last else "; no pass yet")
    console.print(f"  {kept} session(s) kept{said}")
    console.print("[dim]  foundry memories update reads the sessions due now; foundry "
                  "memories reset forgets them[/dim]")


def memories_command(wiring: "Wiring", console: Console, arguments: str) -> None:
    if not wiring.config.memories:
        console.print(f"[dim]{TURN_ON}[/dim]")
        return None
    folder = wiring.memory_folder
    if folder is None:
        console.print(f"[dim]{NOT_TRUSTED}[/dim]")
        return None
    words = arguments.split()
    if not words:
        _status(wiring, console, folder)
        return None
    if len(words) != 2 or words[0] not in ("use", "generate") or words[1] not in ("on", "off"):
        console.print(f"[yellow]{USAGE}[/yellow]")
        return None
    on = words[1] == "on"
    if words[0] == "generate":
        wiring.session.append(EventType.MEMORY_MODE, {"mode": "enabled" if on else "disabled",
                                                      "by": "user"})
        console.print("this session is read for memories once it has been idle" if on
                      else "this session is not read for memories")
        return None
    registry = wiring.runtime.registry
    if not on:
        wiring.runtime.context.memories = ""
        registry.tools.pop(READ_MEMORIES, None)
        wiring.memories_used = False
        console.print("this session does not use its memories from the next message")
        return None
    text = summary(folder)
    if not text:
        console.print("[dim]there are no memories yet: a pass writes them from this "
                      "workspace's sessions once they have been idle[/dim]")
        return None
    wiring.runtime.context.memories = instructions(text)
    if READ_MEMORIES not in registry.tools:
        registry.register(MemoryTool(folder))
    wiring.memories_used = True
    console.print("this session uses its memories from the next message")
    return None
