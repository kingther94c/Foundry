"""The interactive session: its prompt and its loop (D-039).

A line that starts with ``/name`` is a command (cli/slash_commands.py), one
that starts with ``!`` a shell command the user runs (D-055); anything else
is a task for the model, with the images, files and skills it attaches
(cli/attachments.py).
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from foundry.cli.goals import GoalTurns
from foundry.cli.render import sanitize
from foundry.cli.attachments import attach_mentions, attach_skills, take_images
from foundry.cli.session_settings import tell_model
from foundry.cli.slash_commands import (
    ARCHIVE_SESSION,
    DELETE_SESSION,
    FORK_SESSION,
    NEW_SESSION,
    RESUME_SESSION,
    RunTask,
    _EXIT,
    dispatch,
)
from foundry.core.commands import parse_command
from foundry.core.errors import FoundryError
from foundry.core.events import Notice, TerminalStatus
from foundry.core.goal import GOAL_TURN, usage_line
from foundry.core.session import EventType
from foundry.core.tools.base import truncate_middle
from foundry.core.tools.command import combined_output, run_process
from foundry.core.winapi import child_environment

if TYPE_CHECKING:
    from foundry.cli.wiring import Wiring


_ENDS = (NEW_SESSION, FORK_SESSION, ARCHIVE_SESSION, DELETE_SESSION, RESUME_SESSION)


def with_note(wiring: "Wiring", task: str) -> str:
    """The first task after a resume carries the note; later ones do not."""
    note, wiring.note = wiring.note, ""
    return f"{note}\n\n{task}" if note else task


def _switch_model(wiring: "Wiring", name: str, by: str) -> None:
    """The model the next request goes to, journaled so a resume goes on
    with the one the session last had."""
    before, wiring.runtime.model = wiring.runtime.model, name
    wiring.session.append(EventType.MODEL_CHANGE, {"from": before, "to": name, "by": by})


def run_task(wiring: "Wiring", task: RunTask):
    policy = wiring.runtime.policy
    policy.read_only = task.read_only
    own = wiring.runtime.model if task.model else ""
    other = task.model if task.model and task.model != own else ""
    if other:
        _switch_model(wiring, other, "review")
        wiring.runtime.events.emit(Notice(f"reviewing on {other} (review_model)", level="info"))
    try:
        if task.side:
            # What is owed to the conversation -- a note, the images queued
            # for the next message -- waits for it: the side question is
            # dropped (D-129).
            return wiring.runtime.run_turn(task.text, typed=task.typed or None)
        return wiring.runtime.run_turn(with_note(wiring, task.text), take_images(wiring),
                                       typed=task.typed or None)
    except KeyboardInterrupt:
        # Ctrl+C stops the turn, not the session, as codex's does (D-062); a
        # second one, while this is said, still ends the session.
        return wiring.runtime.interrupted()
    finally:
        policy.read_only = ""
        if other:
            _switch_model(wiring, own, "review")
        wiring.renderer.settle()


STOPPED = ("[yellow]stopped; the session is still open (Ctrl+C twice at the prompt, "
           "or /exit, ends it)[/yellow]")

TASK_ENDED = "[dim]the session is still open: ask more, /new for a fresh one, /exit to end it[/dim]"

STOPPED_NOTE = "The user stopped your previous turn before it finished."


PROMPT = "\n[bold cyan]foundry>[/bold cyan] "

BLOCK = '"""'

# How long a paste may pause between lines: far shorter than anyone types.
PASTE_GAP_S = 0.03


def input_waiting(timeout: float = PASTE_GAP_S) -> bool:
    """Whether more input is already waiting at the terminal (D-052).

    A paste of several lines arrives at once, faster than anyone types, so
    the lines after the first belong to the same message. Not a terminal --
    input piped in -- is read a line at a time, as before.
    """
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform == "win32":
        import msvcrt

        deadline = time.monotonic() + timeout
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True
    import select

    try:
        return bool(select.select([sys.stdin], [], [], timeout)[0])
    except (OSError, ValueError):
        return False


# Below this much of the window left, the line turns yellow.
CONTEXT_LOW_PERCENT = 20

# What one Ctrl+C at the prompt says (D-077).
CTRL_C_AGAIN = ("[dim]Ctrl+C again to end the session (it can be resumed), "
                "or /exit[/dim]")


def context_left(wiring: "Wiring") -> int:
    """The share of the context window still free, in percent, by the same
    estimate /status shows."""
    context = wiring.runtime.context
    used, limit = context.estimated_context_tokens(), context.max_context_tokens
    return max(0, 100 - 100 * used // max(limit, 1))


def prompt_for(wiring: "Wiring") -> str:
    """The prompt; once the conversation has begun, with how much of the
    context window is left above it, as codex shows it (D-054)."""
    if not wiring.runtime.context.history:
        return PROMPT
    left = context_left(wiring)
    style = "yellow" if left < CONTEXT_LOW_PERCENT else "dim"
    return f"\n[{style}]{left}% context left[/{style}]" + PROMPT


def read_message(console: Console, prompt: str = PROMPT) -> str:
    """One message from the user (D-052): a line; or every line of a paste,
    which arrive together; or the lines typed between two lines of three double quotes.
    Ctrl+C inside such a block discards it."""
    first = console.input(prompt)
    waiting = input_waiting()
    if first.strip() == BLOCK and not waiting:
        lines: list[str] = []
        try:
            while (line := console.input("[dim]...[/dim] ")).strip() != BLOCK:
                lines.append(line)
        except KeyboardInterrupt:
            console.print("\n[dim](discarded)[/dim]")
            return ""
        return "\n".join(lines).strip("\n")
    lines = [first]
    while waiting:
        lines.append(console.input(""))
        waiting = input_waiting()
    if len(lines) == 1:
        return first.strip()
    text = "\n".join(lines).strip("\n")
    console.print(f"[dim]({len(lines)} lines, sent as one message)[/dim]")
    return text


def repl(wiring: "Wiring", console: Console,
         first: str | None = None) -> TerminalStatus | str:
    """Read messages until the user ends the session; NEW_SESSION if they
    asked for another. A task's ending is not the session's (D-063).

    ``first`` is the task given on the command line (D-078): taken as if it
    had been typed at the prompt, and the prompt comes back after it, as
    ``codex "task"`` goes on.

    One Ctrl+C at the prompt only says how to end the session; two in a row
    end it (D-077), as codex asks for. A Ctrl+C meant for a task that had
    just ended, or a second one pressed to be sure, used to end the session.
    End of input ends it at once."""
    armed = False
    goals = GoalTurns()
    pending: RunTask | None = None
    while True:
        if pending is not None:
            # The goal goes on (D-127): its next turn starts without a prompt.
            task, pending = pending, None
            console.print(f"[dim]the goal goes on ({usage_line(wiring.runtime.goal_now())}); "
                          "Ctrl+C pauses it[/dim]")
        else:
            try:
                if first is not None:
                    line, first = first, None
                else:
                    wiring.renderer.title("ready")
                    line = read_message(console, prompt_for(wiring))
            except EOFError:
                console.print()
                return TerminalStatus.CANCELLED
            except KeyboardInterrupt:
                console.print()
                if armed:
                    return TerminalStatus.CANCELLED
                armed = True
                console.print(CTRL_C_AGAIN)
                continue
            armed = False
            if not line.strip():
                continue
            if line.startswith(SHELL):
                run_shell(wiring, console, line[len(SHELL):].strip())
                continue
            parsed = parse_command(line)
            if parsed is None:
                task = RunTask(attach_skills(wiring, console, line,
                                             attach_mentions(wiring, console, line)),
                               typed=line)
            else:
                command = dispatch(wiring, console, *parsed)
                if command == _EXIT:
                    return TerminalStatus.CANCELLED
                if command in _ENDS:
                    return command
                if not isinstance(command, RunTask):
                    continue
                task = replace(command, typed=line)
        if task.side:
            _ask_aside(wiring, console, task)
            continue
        outcome = run_task(wiring, task)
        # The conversation goes on, so what the task left is answered (D-063).
        wiring.runtime.answer_left_calls()
        prompt = goals.after(wiring, outcome)
        if prompt is not None:
            pending = RunTask(prompt, typed=GOAL_TURN)
        if outcome.status is None or pending is not None:
            continue
        # A dropped stream or an unreachable provider ends the *turn*, not the
        # session. Breaking here threw away the whole conversation over a
        # network blip -- and the code that raises it says "transient, so it is
        # retried", which no layer does once deltas have been shown. The user
        # keeps their context and can simply ask again. The blocked turn is then
        # not how the session ends: a later turn, or /exit, decides that.
        if outcome.status is TerminalStatus.BLOCKED:
            console.print("[yellow]that turn could not be completed; "
                          "the session is still open.[/yellow]")
            continue
        # Stopped by the user -- Ctrl+C, or (a)bort at an approval -- the task
        # ends and the conversation stays (D-062).
        if outcome.status is TerminalStatus.CANCELLED:
            console.print(STOPPED)
            tell_model(wiring, STOPPED_NOTE)
            continue
        # finish, a budget or a full context ended the task, which said how;
        # the user decides when the session ends, as codex's does (D-063).
        console.print(TASK_ENDED)


# A message that starts with this is a command the user runs (D-055).
SHELL = "!"

SHELL_USAGE = ("usage: !COMMAND runs COMMAND in the workspace; the model is told "
               "what it printed with your next message")

SHELL_NOTE = ("The user ran this command in the workspace themselves ({header}). It was "
              "not run by you, so it cannot support a claim in finish: run it yourself "
              "to cite it.\n$ {command}\n{output}")


def run_shell(wiring: "Wiring", console: Console, command: str) -> None:
    """``!COMMAND``: run COMMAND for the user, as codex's prompt does (D-055).

    It is the user's command, so no policy or approval stands in front of it,
    but it runs as the model's would: from the workspace root, with the same
    minimal environment and time limit, and nothing on its input. What it
    printed is shown, and the model is told it, scrubbed and cut to the same
    size, with the next message. It may have changed anything, so no command
    run before it can vouch for the workspace any more.
    """
    if not command:
        console.print(f"[yellow]{SHELL_USAGE}[/yellow]")
        return
    context = wiring.runtime.tool_ctx
    timeout_s = context.max_command_timeout_s
    try:
        result = run_process(command, cwd=str(wiring.workspace.root), timeout_s=timeout_s,
                             env=child_environment(policy=context.env_policy))
    except KeyboardInterrupt:
        _user_ran(wiring, {"command": command, "exit_code": None, "timed_out": False,
                           "stopped": True, "duration_ms": None})
        console.print("[yellow]stopped; the model is not told about it[/yellow]")
        return
    except FoundryError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return
    _user_ran(wiring, {"command": command, "exit_code": result.exit_code,
                       "timed_out": result.timed_out, "stopped": False,
                       "duration_ms": result.duration_ms})
    scrub = wiring.runtime.context.redactor.scrub
    printed = combined_output(result)
    output, _ = truncate_middle(scrub(printed).rstrip(),
                                context.max_output_bytes)
    header = (f"timed out after {timeout_s}s, process tree terminated" if result.timed_out
              else f"exit code {result.exit_code} in {result.duration_ms}ms")
    if output:
        console.print(escape(sanitize(output)), soft_wrap=True)
    console.print(f"[dim]({header}; the model sees this with your next message)[/dim]")
    tell_model(wiring, SHELL_NOTE.format(header=header, command=scrub(command),
                                         output=output or "(no output)"))


def _user_ran(wiring: "Wiring", record: dict) -> None:
    """Journal a command the user ran; it may have changed any file."""
    ordinal = wiring.session.append(EventType.USER_COMMAND, record)
    wiring.runtime.files_changed(ordinal, [])


SIDE_BACK = ("[dim]back where the conversation was: the side question and its answer are "
             "not in it[/dim]")


def _ask_aside(wiring: "Wiring", console: Console, task: RunTask) -> None:
    """The side question run, then the conversation back as it was --
    however the answer ended. Not compacted meanwhile: going back needs the
    history it left."""
    runtime = wiring.runtime
    point, compacting = runtime.aside(), runtime.auto_compact
    runtime.auto_compact = False
    try:
        outcome = run_task(wiring, task)
        runtime.answer_left_calls()
        if outcome.status is TerminalStatus.CANCELLED:
            console.print("[yellow]stopped[/yellow]")
    finally:
        runtime.auto_compact = compacting
        runtime.back_from_aside(point)
    console.print(SIDE_BACK)
