"""Terminal rendering: one subscriber to the runtime's event stream.

Two hardening rules apply to everything printed here, because model output and
tool output are untrusted text: rich markup is escaped so an emitted ``[bold]``
cannot forge UI, and ANSI/OSC sequences are stripped so tool output cannot
rewrite the terminal title or push data into the clipboard (OSC 52).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from foundry.core.events import (
    ApprovalChoice,
    ApprovalKind,
    ApprovalRequest,
    ErrorEvent,
    Event,
    MessageDelta,
    Notice,
    TerminalStatus,
    Termination,
    TokenCount,
    ToolBegin,
    ToolEnd,
    ToolRejected,
    TurnComplete,
)

PATCH_PREVIEW_LINES = 120

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DISCLOSURE = (
    "Foundry runs on your machine with no sandbox. Every command you approve runs "
    "with your full user permissions: it can read and write any of your files "
    "(including credentials), reach the network, and read your environment. "
    "Approval reduces mistakes; it is not containment. Use it only on repositories "
    "you trust."
)

_STATUS_STYLE = {
    TerminalStatus.COMPLETED: "green",
    TerminalStatus.PARTIAL: "yellow",
    TerminalStatus.BLOCKED: "yellow",
    TerminalStatus.FAILED: "red",
    TerminalStatus.CANCELLED: "yellow",
    TerminalStatus.INTERRUPTED: "red",
}


def sanitize(text: str) -> str:
    """Strip terminal control sequences from untrusted text."""
    text = _OSC.sub("", text)
    text = _ANSI.sub("", text)
    return _CONTROL.sub("", text)


def safe(text: str) -> str:
    return escape(sanitize(text))


@dataclass(slots=True)
class Renderer:
    console: Console = field(default_factory=Console)
    verbose: bool = False
    _streaming: bool = False

    def show_disclosure(self, workspace: str, model: str, mode: str) -> None:
        self.console.print(Panel(
            Text(DISCLOSURE, style="yellow"),
            title="[bold]no sandbox[/bold]", border_style="yellow",
        ))
        self.console.print(
            f"[dim]workspace[/dim] {safe(workspace)}   "
            f"[dim]model[/dim] {safe(model)}   [dim]mode[/dim] {safe(mode)}"
        )

    def handle(self, event: Event) -> None:
        if isinstance(event, MessageDelta):
            self.console.print(safe(event.text), end="", markup=False, highlight=False)
            self._streaming = True
            return

        if self._streaming:
            self.console.print()
            self._streaming = False

        if isinstance(event, ToolBegin):
            self.console.print(f"[cyan]->[/cyan] {safe(event.display)}")
        elif isinstance(event, ToolEnd):
            mark = "[green]ok[/green]" if event.ok else "[red]failed[/red]"
            detail = f" {safe(event.summary)}" if self.verbose and event.summary else ""
            self.console.print(f"   {mark}{detail}")
        elif isinstance(event, ToolRejected):
            # The most safety-relevant thing the system does is refuse a
            # destructive command; it used to produce no output at all.
            where = f" [{event.rule_id}]" if event.rule_id and self.verbose else ""
            self.console.print(f"[red]x[/red] {safe(event.display)}")
            self.console.print(f"   [red]{safe(event.reason)}{where}[/red]")
        elif isinstance(event, TokenCount):
            if self.verbose:
                total = event.session_total
                self.console.print(
                    f"[dim]tokens: {total.input_tokens} in / {total.output_tokens} out[/dim]"
                )
        elif isinstance(event, Notice):
            style = "yellow" if event.level == "warning" else "dim"
            self.console.print(f"[{style}]{safe(event.text)}[/{style}]")
        elif isinstance(event, ErrorEvent):
            self.console.print(f"[red]error[/red] {safe(event.message)}")
        elif isinstance(event, Termination):
            style = _STATUS_STYLE.get(event.status, "white")
            self.console.print(
                f"\n[{style}][bold]{event.status.value}[/bold][/{style}] "
                f"{safe(event.reason)}"
            )
            if event.summary:
                self.console.print(safe(event.summary))
        elif isinstance(event, TurnComplete):
            pass  # the text already streamed

    # -- approval ---------------------------------------------------------

    def ask_approval(self, request: ApprovalRequest) -> ApprovalChoice:
        self.console.print()
        if request.approval_kind is ApprovalKind.PATCH and request.detail:
            # The summary first: a large envelope used to scroll the file list
            # off the screen, leaving the reader to approve a patch whose
            # targets they could no longer see.
            self.console.print(f"[bold]{safe(request.display)}[/bold]")
            body = sanitize(request.detail)
            lines = body.splitlines()
            if len(lines) > PATCH_PREVIEW_LINES:
                shown = "\n".join(lines[:PATCH_PREVIEW_LINES])
                body = (f"{shown}\n\n[... {len(lines) - PATCH_PREVIEW_LINES} more lines; "
                        "the full patch is in the session journal ...]")
            self.console.print(Panel(
                Syntax(body, "diff", theme="ansi_dark", word_wrap=True),
                title="[bold]proposed patch[/bold]", border_style="cyan",
            ))
        else:
            self.console.print(Panel(
                Text(sanitize(request.display), style="bold"),
                title=f"[bold]{request.tool}[/bold]", border_style="cyan",
            ))
        self.console.print(f"[dim]{safe(request.reason)}[/dim]")

        prompts = {
            "y": ApprovalChoice.ONCE,
            "s": ApprovalChoice.SESSION,
            "w": ApprovalChoice.ALWAYS,
            "n": ApprovalChoice.DENY,
            "a": ApprovalChoice.ABORT,
        }
        while True:
            try:
                # Parentheses, not brackets: rich reads [y] as a style tag and
                # deletes it, so the prompt rendered as "allow? es / ession /
                # alays / o / bort:" -- four truncated words and no sign of
                # which key to press, on the one surface the whole safety model
                # depends on a human reading.
                answer = self.console.input(
                    "[bold]allow?[/bold] (y)es / (s)ession / al(w)ays / (n)o / (a)bort: "
                ).strip().lower()
            except EOFError:
                # Nobody is there. Fail closed.
                self.console.print("[yellow]no answer: denying[/yellow]")
                return ApprovalChoice.DENY
            except KeyboardInterrupt:
                # Somebody is there and wants to stop. Denying one operation and
                # resampling burned tokens against an explicit stop signal.
                self.console.print("[yellow]interrupted: aborting the task[/yellow]")
                return ApprovalChoice.ABORT
            if answer in prompts:
                return prompts[answer]
            if answer[:1] in prompts:
                return prompts[answer[:1]]
            self.console.print("[dim]please answer y, s, w, n, or a[/dim]")
