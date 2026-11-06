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
    Compacted,
    ErrorEvent,
    Event,
    MessageDelta,
    Notice,
    PlanStep,
    PlanUpdated,
    Reasoning,
    SessionStarted,
    StepStatus,
    TerminalStatus,
    Termination,
    TokenCount,
    ToolBegin,
    ToolEnd,
    ToolRejected,
    TurnComplete,
)

PATCH_PREVIEW_LINES = 120
# Reasoning past this many lines is cut unless the renderer is verbose (D-044).
REASONING_LINES = 8
# A command's output under it (D-068): its last lines, the rest with -v.
COMMAND_OUTPUT_LINES = 5
COMMAND_OUTPUT_LINE_CHARS = 240

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


# Backslashes, so rich prints the brackets instead of reading them as tags.
_STEP_MARK = {
    StepStatus.COMPLETED: "[green]\\[x][/green]",
    StepStatus.IN_PROGRESS: "[cyan]\\[>][/cyan]",
    StepStatus.PENDING: "[dim]\\[ ][/dim]",
}


def print_plan(console, steps: tuple[PlanStep, ...], explanation: str = "") -> None:
    """The model's plan as a checklist (D-037). Its text is the model's: escaped
    and stripped of control sequences like any other model output."""
    if not steps:
        console.print("[dim]plan cleared[/dim]")
        return
    done = sum(s.status is StepStatus.COMPLETED for s in steps)
    head = f"[bold]plan[/bold] [dim]{done}/{len(steps)} done[/dim]"
    if explanation:
        head += f"  {safe(explanation)}"
    console.print(head, highlight=False)
    for step in steps:
        text = safe(step.step)
        if step.status is StepStatus.COMPLETED:
            text = f"[dim]{text}[/dim]"
        elif step.status is StepStatus.IN_PROGRESS:
            text = f"[bold]{text}[/bold]"
        console.print(f"  {_STEP_MARK[step.status]} {text}", highlight=False)


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

        if isinstance(event, Reasoning):
            self._reasoning(event.text)
        elif isinstance(event, ToolBegin):
            # The plan it sets is drawn instead; a line saying so adds nothing.
            if event.tool != "update_plan":
                self.console.print(f"[cyan]->[/cyan] {safe(event.display)}")
        elif isinstance(event, ToolEnd):
            if event.tool == "update_plan" and event.ok:
                return
            mark = "[green]ok[/green]" if event.ok else "[red]failed[/red]"
            # A command's exit code is always worth the few words (D-068).
            said = self.verbose or event.tool == "run_command"
            detail = f" {safe(event.summary)}" if said and event.summary else ""
            self.console.print(f"   {mark}{detail}")
            if event.output:
                self._command_output(event.output)
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
        elif isinstance(event, PlanUpdated):
            print_plan(self.console, event.steps, event.explanation)
        elif isinstance(event, Compacted):
            how = "automatically" if event.auto else "on request"
            self.console.print(
                f"[dim]context compacted {how}: about {event.tokens_before:,} tokens "
                f"down to {event.tokens_after:,}[/dim]")
        elif isinstance(event, SessionStarted):
            again = "resumed " if event.resumed else ""
            self.console.print(f"[dim]{again}session {safe(event.session_id)}[/dim]")
        elif isinstance(event, TurnComplete):
            pass  # the text already streamed

    def _command_output(self, text: str) -> None:
        """The last lines a command printed (D-068), dim and indented under
        it. A command prints anything: control sequences are stripped, and it
        goes out as plain text, so a bracket in it is never read as markup."""
        lines = sanitize(text).splitlines()
        shown = lines if self.verbose else lines[-COMMAND_OUTPUT_LINES:]
        if len(shown) < len(lines):
            self.console.print(Text("     ...", style="dim"))
        for line in shown:
            line = line.rstrip()
            if len(line) > COMMAND_OUTPUT_LINE_CHARS:
                line = line[:COMMAND_OUTPUT_LINE_CHARS - 3] + "..."
            self.console.print(Text(f"     {line}", style="dim"), soft_wrap=True)

    def _reasoning(self, text: str) -> None:
        """Dim and indented, so it reads as the model's aside rather than its
        answer; cut to a few lines unless verbose. Model text: escaped."""
        lines = sanitize(text).strip().splitlines()
        if not lines:
            return
        shown = lines if self.verbose else lines[:REASONING_LINES]
        self.console.print("[dim italic]thinking[/dim italic]", highlight=False)
        for line in shown:
            self.console.print(f"[dim]  {escape(line)}[/dim]", highlight=False)
        if len(shown) < len(lines):
            rest = len(lines) - len(shown)
            self.console.print(
                f"[dim]  ... {rest} more line{'s' if rest != 1 else ''}[/dim]",
                highlight=False)

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
