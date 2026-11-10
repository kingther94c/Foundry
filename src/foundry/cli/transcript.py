"""A resumed session's conversation, shown before its prompt comes back
(D-108), as codex's resume shows where the conversation was.

What the user typed is shown as they typed it -- ``/review --base main``,
not the prompt it became, and without the notes Foundry put around it -- where
the journal says (``user_input.typed``); the model's answers as Markdown, as
they streamed; each tool call as its line. It is the journal's copy, so a
secret in it is already scrubbed, and every line is stripped of control
sequences as the live session's are.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.text import Text

from foundry.cli.render import safe, sanitize
from foundry.core.context import SUMMARY_PREFIX
from foundry.core.conversation import ImageBlock, Role, TextBlock, ToolUseBlock
from foundry.core.errors import FoundryError

if TYPE_CHECKING:
    from foundry.cli.wiring import Wiring

# The last messages shown; a long session's earlier ones are counted.
MAX_SHOWN = 40
# A message the user sent is shown to this many lines: a paste may be long.
MAX_USER_LINES = 12
# How the user's message is marked, as the prompt marked it.
USER_MARK = "[bold cyan]foundry>[/bold cyan] "
# Drawn by the plan the resume restores, not as a line of its own.
_UNSHOWN_TOOLS = frozenset({"update_plan"})


def show_conversation(wiring: "Wiring") -> None:
    """The conversation the resumed session carries, up to where it stopped."""
    state = wiring.resumed
    if state is None:
        return
    console = wiring.renderer.console
    typed = dict(state.typed)
    messages = [m for m in state.history if m.role in (Role.USER, Role.ASSISTANT)]
    if not messages:
        return
    earlier = len(messages) - MAX_SHOWN
    if earlier > 0:
        console.print(f"[dim]... {earlier} earlier message(s)[/dim]")
        messages = messages[-MAX_SHOWN:]
    for message in messages:
        if message.role is Role.USER:
            _user(console, message, typed)
        else:
            _assistant(wiring, message)
    console.print("[dim]-- the session continues here --[/dim]")


def _user(console, message, typed: dict[str, str]) -> None:
    text = message.text_content
    if text.startswith(SUMMARY_PREFIX):
        console.print("[dim](what came before was compacted into a summary)[/dim]")
        return
    lines = typed.get(text, text).splitlines()
    shown = "\n".join(lines[:MAX_USER_LINES])
    if len(lines) > MAX_USER_LINES:
        shown += f"\n... ({len(lines) - MAX_USER_LINES} more lines)"
    images = sum(isinstance(b, ImageBlock) for b in message.blocks)
    console.print()
    console.print(USER_MARK + safe(shown)
                  + (f" [dim]({images} image(s))[/dim]" if images else ""), soft_wrap=True)


def _assistant(wiring: "Wiring", message) -> None:
    text = "".join(b.text for b in message.blocks if isinstance(b, TextBlock))
    if text.strip():
        wiring.renderer.show_answer(text)
    for block in message.blocks:
        if isinstance(block, ToolUseBlock) and block.name not in _UNSHOWN_TOOLS:
            wiring.renderer.console.print(Text("-> ", style="cyan")
                                          + Text(safe_display(wiring, block)))


def safe_display(wiring: "Wiring", block: ToolUseBlock) -> str:
    """The call's line, as the live session showed it: the tool's own display
    of its arguments, or its name when they no longer make one."""
    try:
        arguments = json.loads(block.arguments)
        display = wiring.runtime.registry.get(block.name).validate(arguments).display
    except (ValueError, TypeError, FoundryError):
        display = block.name
    return sanitize(display)
