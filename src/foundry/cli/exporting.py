"""``/export [PATH]``: the whole conversation as Markdown (D-124), as codex's
/export puts it on the clipboard or saves it to a file.

The conversation is the journal's, from its first message: what a
compaction or a rewind took out of the model's history is in it, and so is
what came before a resume. It is the journal's copy, so a secret in it is
already scrubbed; what the user typed is shown as typed (D-108), the model's
answers as the Markdown it wrote, each tool call as its line, and all of it
as it was shown -- without control sequences or carriage returns. A file is
never written over: an export goes to a new one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from rich.console import Console
from rich.markup import escape

from foundry.cli.copying import CopyFailed, as_shown, copy_text
from foundry.core.session import EventType, JournalRecord, SessionStore

if TYPE_CHECKING:
    from foundry.cli.app import Wiring

TITLE = "# Foundry conversation\n"
# Not shown as a line of its own: the answer a finish gave is its task's
# result.
_UNLISTED = frozenset({"finish"})


class _Transcript:
    def __init__(self) -> None:
        self.parts = [TITLE]
        self.heading = ""

    def section(self, heading: str, text: str) -> None:
        self.parts.append(f"\n## {heading}\n\n{as_shown(text).lstrip(chr(10)).rstrip()}\n")
        self.heading = heading

    def activity(self, line: str) -> None:
        # Activity in a row is one section, its lines indented as codex's.
        if self.heading != "Activity":
            self.parts.append("\n## Activity\n\n")
            self.heading = "Activity"
        self.parts.append(f"    {as_shown(line).strip()}\n")

    def markdown(self) -> str | None:
        return "".join(self.parts) if len(self.parts) > 1 else None


def conversation_markdown(records: Iterable[JournalRecord], *,
                          show_reasoning: bool = True) -> str | None:
    """The conversation the journal holds, as Markdown; None when it holds
    no message yet."""
    out = _Transcript()
    for record in records:
        kind, payload = record.type, record.payload
        if kind == EventType.USER_INPUT and "hook" not in payload:
            text = str(payload.get("typed") or payload.get("text") or "")
            images = len(payload.get("images") or ())
            labels = "\n".join(f"[image {n}]" for n in range(1, images + 1))
            out.section("User", "\n\n".join(part for part in (text.strip(), labels) if part))
        elif kind == EventType.MODEL_RESPONSE and not payload.get("purpose"):
            if show_reasoning:
                for reasoning in payload.get("reasoning") or ():
                    if str(reasoning).strip():
                        out.section("Reasoning", str(reasoning))
            if str(payload.get("text") or "").strip():
                out.section("Assistant", str(payload["text"]))
        elif kind == EventType.TOOL_CALL and payload.get("display") \
                and payload.get("name") not in _UNLISTED:
            out.activity(f"-> {payload['display']}")
        elif kind == EventType.USER_COMMAND and payload.get("command"):
            out.activity(f"$ {payload['command']}")
        elif kind == EventType.COMPACTED:
            out.activity("(the conversation so far was compacted into a summary)")
        elif kind == EventType.REWOUND:
            out.activity("(the conversation went back to an earlier point here)")
        elif kind == EventType.TERMINATION:
            said = str(payload.get("summary") or payload.get("reason") or "")
            status = str(payload.get("status") or "")
            out.section("Result", f"**{status}**" + (f"\n\n{said}" if said.strip() else ""))
    return out.markdown()


def _destination(wiring: "Wiring", raw: str) -> Path:
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else wiring.workspace.root / path


def export_conversation(wiring: "Wiring", console: Console, arguments: str) -> None:
    """``/export``: the conversation on the clipboard; ``/export PATH``: in
    a new file there."""
    markdown = conversation_markdown(SessionStore.read_records(wiring.session.path),
                                     show_reasoning=wiring.runtime.show_reasoning)
    if markdown is None:
        console.print("[yellow]there is no conversation to export yet[/yellow]")
        return
    lines = len(markdown.splitlines())
    raw = arguments.strip()
    if not raw:
        try:
            copy_text(markdown)
        except CopyFailed as exc:
            console.print(f"[red]cannot copy the conversation: {escape(str(exc))}[/red]; "
                          "/export PATH saves it to a file", soft_wrap=True)
            return
        console.print(f"[dim]copied the conversation to the clipboard ({lines} lines)[/dim]")
        return
    path = _destination(wiring, raw)
    try:
        # "x": an export never writes over a file.
        with path.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(markdown)
    except FileExistsError:
        console.print(f"[yellow]{escape(str(path))} already exists; /export another name"
                      "[/yellow]", soft_wrap=True)
        return
    except OSError as exc:
        console.print(f"[red]cannot write {escape(str(path))}: "
                      f"{escape(exc.strerror or str(exc))}[/red]", soft_wrap=True)
        return
    console.print(f"[dim]saved the conversation to {escape(str(path))} ({lines} lines)[/dim]",
                  soft_wrap=True)
