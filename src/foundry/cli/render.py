"""Terminal rendering: one subscriber to the runtime's event stream.

Two hardening rules apply to everything printed here, because model output and
tool output are untrusted text: rich markup is escaped so an emitted ``[bold]``
cannot forge UI, and ANSI/OSC sequences are stripped so tool output cannot
rewrite the terminal title or push data into the clipboard (OSC 52).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from foundry.cli.file_links import file_link
from foundry.cli.markdown import MarkdownLines
from foundry.cli.title import compose as compose_title
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
    ToolOutputDelta,
    ToolRejected,
    TurnComplete,
    TurnStarted,
)

PATCH_PREVIEW_LINES = 120
# Reasoning past this many lines is cut unless the renderer is verbose (D-044).
REASONING_LINES = 8
# A command's output under it (D-068): its last lines, the rest with -v.
COMMAND_OUTPUT_LINES = 5
COMMAND_OUTPUT_LINE_CHARS = 240
# Foundry's own tools that only read the workspace: under their line, nothing
# when they worked (D-076). An MCP server's tools are not among them: what one
# does is the server's to say.
QUIET_WHEN_OK = frozenset({"read_file", "list_files", "search_text", "read_artifact",
                           "git_status", "git_diff", "view_image", "repo_map"})
# What a patch changed, under it (D-075): its first lines, the rest with -v.
PATCH_DIFF_LINES = 40
_DIFF_STYLE = (("@@", "cyan"), ("+", "green"), ("-", "red"), (" ", "dim"), ("(", "dim"))

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


def run_settings(event: SessionStarted) -> list[tuple[str, str]]:
    """What an unattended run runs with (D-092), as codex's exec lists its
    settings before it starts: nothing for an event that does not say."""
    if not event.protocol:
        return []
    rows = [("workspace", event.workspace),
            ("model", f"{event.model} ({event.protocol}, profile {event.profile})"),
            ("mode", event.mode),
            ("effort", event.effort or "the API's default")]
    rows += [(label, value) for label, value in (("summary", event.reasoning_summary),
                                                 ("verbosity", event.verbosity)) if value]
    return rows


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


def elapsed(seconds: float) -> str:
    """``7s``, ``2m 05s``, ``1h 02m``."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02}m"


# The line that says Foundry is working (D-069), redrawn this often.
WORKING_REFRESH_PER_S = 4


class Working:
    """``working (12s, Ctrl+C to stop)``: what a live display redraws while
    the model is asked or a tool runs, the time counted from ``started``."""

    def __init__(self, what: str, started: float, clock=time.monotonic) -> None:
        self.what = what
        self.started = started
        self.clock = clock
        self.spinner = Spinner("line", style="cyan")
        # The newest line a running command printed (D-074).
        self.latest = ""

    def text(self) -> str:
        text = f"{self.what} ({elapsed(self.clock() - self.started)}, Ctrl+C to stop)"
        return f"{text}: {self.latest}" if self.latest else text

    def __rich_console__(self, console, options):
        # One line, however long the command's: the spinner and a space take 2.
        text = Text(self.text(), style="dim")
        text.truncate(max(options.max_width - 2, 1), overflow="ellipsis")
        self.spinner.update(text=text)
        yield self.spinner


# The title a reasoning summary opens with -- **Exploring the parser** --
# which the working line shows while the model goes on (D-109), as codex's
# status line does.
_TITLE = re.compile(r"\*\*([^*\n]{1,80})\*\*")


def reasoning_title(text: str) -> str:
    """The summary's first bold words, or nothing."""
    match = _TITLE.search(sanitize(text))
    return " ".join(match.group(1).split()) if match else ""


# How many warnings and errors a session keeps for /warnings (D-138).
KEPT_WARNINGS = 200


@dataclass(slots=True)
class Renderer:
    console: Console = field(default_factory=Console)
    verbose: bool = False
    # The editor a cited file opens in (D-106), as codex's file_opener, and
    # the workspace a citation is read in: without one, nothing is linked.
    file_opener: str = "vscode"
    workspace: Path | None = None
    _streaming: bool = False
    # The working line while it is up (D-069), and what it draws; None
    # otherwise.
    _live: Live | None = None
    _working: Working | None = None
    # The model's text being rendered as Markdown (D-070), while it streams.
    _markdown: MarkdownLines | None = None
    # When the task now running started (D-073); None between tasks.
    _task_started: float | None = None
    clock: Callable[[], float] = time.monotonic
    # The terminal's title (D-139): its items -- none outside an interactive
    # session -- what they show, read as it is written, and what was written.
    title_items: tuple[str, ...] = ()
    title_facts: Callable[[], dict] | None = None
    _title_state: str = "ready"
    _title_shown: str | None = None
    # The warnings and errors shown (D-138), for /warnings: (time, level,
    # text), the latest KEPT_WARNINGS of them; ``warnings_dropped`` counts
    # the older ones let go.
    warnings: list = field(default_factory=list)
    warnings_dropped: int = 0

    def _keep(self, level: str, text: str) -> None:
        """A warning or an error, kept for /warnings (D-138): it may have
        scrolled away before anyone read it."""
        self.warnings.append((time.strftime("%H:%M:%S"), level, text))
        if len(self.warnings) > KEPT_WARNINGS:
            del self.warnings[0]
            self.warnings_dropped += 1

    def title(self, state: str | None = None) -> None:
        """The terminal's title, as its items say while the session is
        ``state`` -- ready, working, or action, waiting on an answer --
        written on a terminal only, and only when it changes (D-139)."""
        if state is not None:
            self._title_state = state
        if (not self.title_items or self.title_facts is None
                or not self.console.is_terminal or self.console.is_dumb_terminal):
            return
        text = compose_title(self.title_items, self.title_facts(), self._title_state)
        if text == (self._title_shown or ""):
            return
        self.console.set_window_title(text)
        self._title_shown = text

    def clear_title(self) -> None:
        """As the session ends, the title it wrote is cleared, as codex's
        is: what was there before cannot be read back."""
        if self._title_shown is not None:
            self.console.set_window_title("")
            self._title_shown = None

    def work(self, what: str) -> None:
        """Put up the working line (D-069): only on a terminal, where it can
        be redrawn in place and taken down without a trace."""
        self.idle()
        self.title("working")
        if not self.console.is_terminal or self.console.is_dumb_terminal:
            return
        self._working = Working(what, time.monotonic())
        self._live = Live(self._working, console=self.console,
                          transient=True, refresh_per_second=WORKING_REFRESH_PER_S,
                          redirect_stdout=False, redirect_stderr=False)
        self._live.start()

    def idle(self) -> None:
        """Take the working line down, before anything else is printed or
        asked: a line being redrawn cannot share the screen with a prompt."""
        live, self._live = self._live, None
        self._working = None
        if live is not None:
            live.stop()

    def show_disclosure(self, workspace: str, model: str, mode: str) -> None:
        self.console.print(Panel(
            Text(DISCLOSURE, style="yellow"),
            title="[bold]no sandbox[/bold]", border_style="yellow",
        ))
        self.console.print(
            f"[dim]workspace[/dim] {safe(workspace)}   "
            f"[dim]model[/dim] {safe(model)}   [dim]mode[/dim] {safe(mode)}"
        )

    def settle(self) -> None:
        """A task is over: the text still held is printed, and the working
        line taken down."""
        self.idle()
        self._end_text()
        # However it ended, the next task's time starts with the next task.
        self._task_started = None

    def _worked(self) -> None:
        """``worked for 1m 12s`` as a task ends (D-073), as codex says it;
        nothing for an answer that took under a second."""
        started, self._task_started = self._task_started, None
        if started is None:
            return
        seconds = self.clock() - started
        if seconds >= 1:
            self.console.print(f"[dim]worked for {elapsed(seconds)}[/dim]")

    def handle(self, event: Event) -> None:
        if isinstance(event, ToolOutputDelta):
            # Into the working line, which stays up (D-074). Off a terminal
            # there is none, and the output comes with the command's end.
            if self._working is not None:
                self._working.latest = sanitize(event.text)
            return
        self.idle()
        if isinstance(event, MessageDelta):
            self._text(event.text)
            return
        self._end_text()
        self._handle(event)
        # Up again while the model is asked, or a tool runs (D-069).
        if isinstance(event, TurnStarted):
            if self._task_started is None:
                self._task_started = self.clock()
            self.work("working")
        elif isinstance(event, ToolBegin):
            self.work("running")

    def _text(self, text: str) -> None:
        """The model's text: on a terminal, as Markdown a line at a time
        (D-070); elsewhere -- a pipe, a log -- as it comes, exactly as the
        model wrote it. Either way stripped of control sequences, and never
        read as markup (escaping it as well printed list[int] as list\\[int])."""
        if self.console.is_terminal and not self.console.is_dumb_terminal:
            if self._markdown is None:
                self._markdown = MarkdownLines(sanitize, self._link)
            for renderable in self._markdown.feed(text):
                self.console.print(renderable)
            return
        self.console.print(sanitize(text), end="", markup=False, highlight=False)
        self._streaming = True

    def _link(self, code: str) -> str | None:
        if self.workspace is None:
            return None
        return file_link(code, self.workspace, self.file_opener)

    def show_answer(self, text: str) -> None:
        """An answer given whole -- a resumed session's, shown again (D-108)
        -- as it would have streamed."""
        self._text(text)
        self._end_text()

    def _end_text(self) -> None:
        """The text has ended: the rest of it, and the line it was on."""
        if self._markdown is not None:
            for renderable in self._markdown.finish():
                self.console.print(renderable)
        if self._streaming:
            self.console.print()
            self._streaming = False

    def _handle(self, event: Event) -> None:
        if isinstance(event, Reasoning):
            self._reasoning(event.text)
            # The model goes on after it: the line goes back up, saying what
            # the summary says it is doing (D-109).
            self.work(reasoning_title(event.text) or "working")
        elif isinstance(event, ToolBegin):
            # The plan it sets is drawn instead; a line saying so adds nothing.
            if event.tool != "update_plan":
                self.console.print(f"[cyan]->[/cyan] {safe(event.display)}")
        elif isinstance(event, ToolEnd):
            if event.tool == "update_plan" and event.ok:
                return
            # A read's own line says what it read; that it worked is not news
            # (D-076). -v still says so.
            if event.ok and event.tool in QUIET_WHEN_OK and not self.verbose:
                return
            mark = "[green]ok[/green]" if event.ok else "[red]failed[/red]"
            # A command's exit code is always worth the few words (D-068), and
            # so is why anything failed (D-076).
            said = self.verbose or event.tool == "run_command" or not event.ok
            detail = f" {safe(event.summary)}" if said and event.summary else ""
            self.console.print(f"   {mark}{detail}")
            if event.output and event.tool == "apply_patch":
                self._patch_diff(event.output)
            elif event.output:
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
            if event.level == "warning":
                self._keep("warning", event.text)
        elif isinstance(event, ErrorEvent):
            self.console.print(f"[red]error[/red] {safe(event.message)}")
            self._keep("error", event.message)
        elif isinstance(event, Termination):
            style = _STATUS_STYLE.get(event.status, "white")
            self.console.print(
                f"\n[{style}][bold]{event.status.value}[/bold][/{style}] "
                f"{safe(event.reason)}"
            )
            if event.summary:
                self.console.print(safe(event.summary))
            self._worked()
        elif isinstance(event, PlanUpdated):
            print_plan(self.console, event.steps, event.explanation)
        elif isinstance(event, Compacted):
            how = "automatically" if event.auto else "on request"
            self.console.print(
                f"[dim]context compacted {how}: about {event.tokens_before:,} tokens "
                f"down to {event.tokens_after:,}[/dim]")
        elif isinstance(event, SessionStarted):
            again = "resumed " if event.resumed else ""
            kept = " (ephemeral: not kept)" if event.ephemeral else ""
            self.console.print(f"[dim]{again}session {safe(event.session_id)}{kept}[/dim]")
            for label, value in run_settings(event):
                # Not wrapped: a path cut in two cannot be copied out of a log.
                self.console.print(f"[dim]  {label:<10}{safe(value)}[/dim]", soft_wrap=True)
        elif isinstance(event, TurnComplete):
            self._worked()      # the text already streamed

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

    def _patch_diff(self, text: str) -> None:
        """What a patch changed (D-075), as codex shows an edit: each file's
        name with its added and removed lines, then its diff, coloured. The
        files' text is anyone's: stripped, and printed as Text, never markup."""
        lines = sanitize(text).splitlines()
        shown = lines if self.verbose else lines[:PATCH_DIFF_LINES]
        for line in shown:
            style = next((s for mark, s in _DIFF_STYLE if line.startswith(mark)), "bold")
            line = line.rstrip()
            if len(line) > COMMAND_OUTPUT_LINE_CHARS:
                line = line[:COMMAND_OUTPUT_LINE_CHARS - 3] + "..."
            self.console.print(Text(f"     {line}", style=style), soft_wrap=True)
        if len(shown) < len(lines):
            self.console.print(Text(f"     ... {len(lines) - len(shown)} more lines "
                                    "(-v shows them)", style="dim"))

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
        """Asked at the terminal; the title says so while it waits (D-139)."""
        self.title("action")
        try:
            return self._ask_approval(request)
        finally:
            self.title("working")

    def _ask_approval(self, request: ApprovalRequest) -> ApprovalChoice:
        self.idle()
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
