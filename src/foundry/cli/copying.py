"""``/copy``: the model's latest answer, or one of its code blocks, on the
clipboard (D-123), as codex's /copy offers the whole response and each code
block in it.

The answer is the conversation's: the latest thing the model wrote -- its
message, or the summary its ``finish`` gave -- so a resumed session has its
own. What is copied is what was shown: control sequences and carriage
returns are stripped as the screen strips them, so a paste carries nothing
the reader did not see. The clipboard is written by Windows PowerShell, as
/image reads it (D-065); the text reaches it as base64 on its input, never on
its command line.
"""

from __future__ import annotations

import base64
import re
import subprocess
from typing import TYPE_CHECKING, Sequence

from rich.console import Console
from rich.markup import escape

from foundry.cli.markdown import code_blocks
from foundry.cli.render import sanitize
from foundry.core.conversation import Message, Role, ToolUseBlock
from foundry.core.winapi import CREATE_NO_WINDOW, IS_WINDOWS, find_program

if TYPE_CHECKING:
    from foundry.cli.app import Wiring

# The text, from base64 on the input, put on the clipboard -- kept there
# after PowerShell ends, and tried again while another program holds it.
# -Sta: the clipboard is not reachable from a multithreaded apartment.
CLIPBOARD_WRITE = "[System.Windows.Forms.Clipboard]::SetDataObject($text, $true, 5, 100)"
COPY_SCRIPT = (
    "$text = [Text.Encoding]::UTF8.GetString("
    "[Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())); "
    "Add-Type -AssemblyName System.Windows.Forms; "
    + CLIPBOARD_WRITE)
COPY_TIMEOUT_S = 15
# A code block's first line, as /copy lists it, to this many characters.
PREVIEW_CHARS = 60
_NUMBER = re.compile(r"[0-9]{1,6}")


class CopyFailed(Exception):
    """The clipboard could not be written; the message says why."""


def latest_answer(history: Sequence[Message]) -> str | None:
    """The latest thing the model wrote to the user: the summary of the
    ``finish`` that ended its task, or else its message; None before it has
    written one."""
    for message in reversed(history):
        if message.role is not Role.ASSISTANT:
            continue
        for block in message.blocks:
            if isinstance(block, ToolUseBlock) and block.name == "finish":
                try:
                    summary = block.parse_arguments().get("summary")
                except ValueError:
                    summary = None
                if isinstance(summary, str) and summary.strip():
                    return summary
        if message.text_content.strip():
            return message.text_content
    return None


def as_shown(text: str) -> str:
    """``text`` as the screen showed it: no control sequences, no carriage
    returns."""
    return sanitize(text).replace("\r", "")


def copy_text(text: str) -> None:
    """Put ``text`` on the clipboard. Raises CopyFailed, saying why."""
    if not IS_WINDOWS:
        raise CopyFailed("the clipboard is written on Windows only")
    # powershell.exe from PATH, not a copy in the current directory (D-049).
    shell = find_program("powershell.exe")
    if shell is None:
        raise CopyFailed("powershell.exe was not found on PATH")
    try:
        done = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Sta", "-Command", COPY_SCRIPT],
            input=base64.b64encode(text.encode("utf-8")).decode("ascii"),
            capture_output=True, text=True, encoding="ascii", errors="replace",
            timeout=COPY_TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise CopyFailed(f"writing the clipboard took over {COPY_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise CopyFailed(f"PowerShell could not be started ({exc})") from exc
    if done.returncode != 0:
        raise CopyFailed(f"PowerShell exit {done.returncode}")


def _lines(text: str) -> str:
    count = len(text.splitlines()) or 1
    return f"{count} line" if count == 1 else f"{count} lines"


def _preview(content: str) -> str:
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return first if len(first) <= PREVIEW_CHARS else first[:PREVIEW_CHARS - 3] + "..."


def copy_answer(wiring: "Wiring", console: Console, arguments: str) -> None:
    """``/copy``: the latest answer, its code blocks listed; ``/copy N``:
    the Nth of them."""
    choice = arguments.strip()
    if choice and not _NUMBER.fullmatch(choice):
        console.print("[yellow]usage: /copy [N] -- N, one of the latest answer's code "
                      "blocks[/yellow]")
        return
    answer = latest_answer(wiring.runtime.context.history)
    if answer is None:
        console.print("[yellow]there is no answer to copy yet[/yellow]")
        return
    answer = as_shown(answer)
    blocks = code_blocks(answer)
    if not choice:
        text, what = answer, "the latest answer"
    elif not blocks:
        console.print("[yellow]the latest answer has no code block; /copy copies all of "
                      "it[/yellow]")
        return
    elif not 1 <= int(choice) <= len(blocks):
        console.print(f"[yellow]the latest answer has {len(blocks)} code block(s): /copy 1 "
                      f"to /copy {len(blocks)}[/yellow]")
        return
    else:
        language, text = blocks[int(choice) - 1]
        what = f"code block {int(choice)}" + (f" ({language})" if language else "")
    if not text.strip():
        console.print(f"[yellow]{escape(what)} is empty: nothing to copy[/yellow]")
        return
    try:
        copy_text(text)
    except CopyFailed as exc:
        console.print(f"[red]cannot copy {escape(what)}: {escape(str(exc))}[/red]")
        return
    console.print(f"[dim]copied {escape(what)} to the clipboard ({_lines(text)})[/dim]")
    if not choice and blocks:
        for index, (language, content) in enumerate(blocks, 1):
            console.print(f"[dim]  /copy {index}  {escape(language or 'code')}: "
                          f"{escape(_preview(content))}[/dim]", soft_wrap=True)
