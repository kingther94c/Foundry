"""The interactive session: its prompt, and the commands typed at it (D-039).

A line that starts with ``/name`` is a command, one that starts with ``!`` a
shell command the user runs (D-055); anything else is a task for the
model. Commands are read from the user's input and nowhere else -- nothing
the model writes and nothing a tool prints reaches this table -- so the ones
that change what the session may do (/approve, /undo) are the user's alone.

Built-in commands come first, then the user's custom prompts, then a trusted
repository's (core/commands.py). An unknown command is said to be unknown
rather than sent to the model as a task: "/udno" should not start a turn.
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from foundry.cli.copying import copy_answer
from foundry.cli.exporting import export_conversation
from foundry.cli.render import print_plan, safe, sanitize
from foundry.core.commands import PROMPTS, builtin_prompt, expand, parse_command, usage_hint
from foundry.core.config import effort_levels
from foundry.core.context import SUMMARY_PREFIX
from foundry.core.conversation import Message, Role, TextBlock
from foundry.core.errors import Cancelled, FoundryError, ImageRejected, PromptError, ToolError
from foundry.core.images import MAX_IMAGES_PER_MESSAGE, from_bytes as image_from_bytes
from foundry.core.images import load as load_image
from foundry.core.events import Notice, TerminalStatus
from foundry.core.hooks import HOOKS_FILE, UNMATCHED_EVENTS, events_named
from foundry.core.policy.engine import Mode, Verdict
from foundry.core.prompts import doc_names, mode_line, system_prompt
from foundry.core.resume import fork_journal, session_name
from foundry.core.session import EventType, SessionStore
from foundry.core.skills import LOAD_SKILL, REPOSITORY_SKILLS, YOURS
from foundry.core.tools.base import ToolKind, truncate_middle
from foundry.core.tools.command import combined_output, run_process
from foundry.core.tools.files import ReadFile
from foundry.core.tools.git import collect_evidence, resolve_commit, run_git
from foundry.core.winapi import CREATE_NO_WINDOW, IS_WINDOWS, child_environment, find_program
from foundry.core.workspace import PathRejected
from foundry.core.undo import (
    Outcome,
    Plan,
    apply_undo,
    plan_undo,
    recorded_changes,
    undo_note,
    undo_payload,
)

if TYPE_CHECKING:
    from foundry.cli.app import Wiring

# What repl() returns when the user typed /new: the caller closes this session
# and starts another.
NEW_SESSION = "new_session"
# /fork (D-126): the session goes on as the fork the wiring names.
FORK_SESSION = "fork_session"
_EXIT = "exit"

DIFF_MAX_LINES = 400
REVIEW_READ_ONLY = "a review changes nothing: edits and commands are refused until it ends"


@dataclass(frozen=True, slots=True)
class RunTask:
    """A task for the model, from the user's line or from a command."""

    text: str
    # Set: the policy refuses every change while this task runs (/review).
    read_only: str = ""
    # What the user typed for it -- the line, or the command (D-108); set
    # by the prompt, which has it.
    typed: str = ""


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    summary: str
    run: Callable[["Wiring", Console, str], object]
    arguments: str = ""          # the usage hint; empty: the command takes none


# --- the loop -----------------------------------------------------------------------


def with_note(wiring: "Wiring", task: str) -> str:
    """The first task after a resume carries the note; later ones do not."""
    note, wiring.note = wiring.note, ""
    return f"{note}\n\n{task}" if note else task


def attach(wiring: "Wiring", console: Console, raw: str, then: str = "") -> bool:
    """Queue an image for the next message (D-045): `/image PATH`, and `-i`.
    The path is the user's own, so it need not be in the workspace."""
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        console.print("[yellow]usage: /image PATH[/yellow]")
        return False
    if not _may_attach(wiring, console):
        return False
    path = Path(raw).expanduser()
    try:
        picture = load_image(path if path.is_absolute() else Path.cwd() / path, raw)
    except ImageRejected as exc:
        console.print(f"[red]cannot attach {escape(raw)}: {escape(str(exc))}[/red]")
        return False
    wiring.images.append(picture.block)
    console.print(f"[dim]attached {escape(raw)} ({picture.describe()}){then}[/dim]")
    return True


def _may_attach(wiring: "Wiring", console: Console) -> bool:
    if not wiring.config.backend.images:
        console.print("[yellow]this backend is set up without images "
                      "(backend.images = false)[/yellow]")
        return False
    if len(wiring.images) >= MAX_IMAGES_PER_MESSAGE:
        console.print(f"[yellow]at most {MAX_IMAGES_PER_MESSAGE} images go with one "
                      "message[/yellow]")
        return False
    return True


# The clipboard's image, as PNG in base64 on stdout (D-065); exit 3 when it
# holds none. -Sta: the clipboard is not reachable from a multithreaded
# apartment.
CLIPBOARD_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
    "$image = [System.Windows.Forms.Clipboard]::GetImage(); "
    "if ($null -eq $image) { exit 3 }; "
    "$stream = New-Object System.IO.MemoryStream; "
    "$image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png); "
    "[Convert]::ToBase64String($stream.ToArray())")
CLIPBOARD_EMPTY = 3
CLIPBOARD_TIMEOUT_S = 15
CLIPBOARD_NAME = "the clipboard's image"


def clipboard_image() -> bytes | None:
    """The image on the clipboard as PNG bytes, or None when it holds none
    (D-065). Raises ImageRejected when the clipboard cannot be read."""
    if not IS_WINDOWS:
        raise ImageRejected("the clipboard is read on Windows only; /image PATH attaches "
                            "a file")
    # powershell.exe from PATH, not a copy in the current directory (D-049).
    shell = find_program("powershell.exe")
    if shell is None:
        raise ImageRejected("powershell.exe was not found on PATH")
    try:
        done = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Sta", "-Command", CLIPBOARD_SCRIPT],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="ascii",
            errors="replace", timeout=CLIPBOARD_TIMEOUT_S, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ImageRejected(f"reading the clipboard took over {CLIPBOARD_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise ImageRejected(f"the clipboard could not be read ({exc})") from exc
    if done.returncode == CLIPBOARD_EMPTY:
        return None
    if done.returncode != 0:
        raise ImageRejected(f"the clipboard could not be read (PowerShell exit "
                            f"{done.returncode})")
    try:
        return base64.b64decode(done.stdout.strip(), validate=True)
    except ValueError as exc:
        raise ImageRejected("what the clipboard gave back is not an image") from exc


def attach_clipboard(wiring: "Wiring", console: Console, then: str = "") -> bool:
    """``/image`` alone: the image on the clipboard -- a screenshot, as
    codex's Ctrl+V pastes one (D-065) -- goes with the next message, checked
    as a file's would be."""
    if not _may_attach(wiring, console):
        return False
    try:
        data = clipboard_image()
        if data is None:
            console.print("[yellow]the clipboard holds no image; /image PATH attaches a "
                          "file[/yellow]")
            return False
        picture = image_from_bytes(data, CLIPBOARD_NAME)
    except ImageRejected as exc:
        console.print(f"[red]cannot attach {CLIPBOARD_NAME}: {escape(str(exc))}[/red]")
        return False
    wiring.images.append(picture.block)
    console.print(f"[dim]attached {CLIPBOARD_NAME} ({picture.describe()}){then}[/dim]")
    return True


def take_images(wiring: "Wiring") -> tuple:
    images, wiring.images = tuple(wiring.images), []
    return images


def run_task(wiring: "Wiring", task: RunTask):
    policy = wiring.runtime.policy
    policy.read_only = task.read_only
    try:
        return wiring.runtime.run_turn(with_note(wiring, task.text), take_images(wiring),
                                       typed=task.typed or None)
    except KeyboardInterrupt:
        # Ctrl+C stops the turn, not the session, as codex's does (D-062); a
        # second one, while this is said, still ends the session.
        return wiring.runtime.interrupted()
    finally:
        policy.read_only = ""
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
    while True:
        try:
            if first is not None:
                line, first = first, None
            else:
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
            task: object = RunTask(attach_skills(wiring, console, line,
                                                 attach_mentions(wiring, console, line)),
                                   typed=line)
        else:
            task = dispatch(wiring, console, *parsed)
            if task == _EXIT:
                return TerminalStatus.CANCELLED
            if task in (NEW_SESSION, FORK_SESSION):
                return task
            if not isinstance(task, RunTask):
                continue
            task = replace(task, typed=line)
        outcome = run_task(wiring, task)
        # The conversation goes on, so what the task left is answered (D-063).
        wiring.runtime.answer_left_calls()
        if outcome.status is None:
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


# `@path`, or `@"a path"`, in a message the user typed attaches that file
# (D-058). Not after a word character, so an e-mail address is not one.
_MENTION = re.compile(r'(?<![\w@])@(?:"([^"\r\n]+)"|([^\s"\'`]+))')
# What ends a sentence rather than a path: "see @a.py, then".
_MENTION_TRAILING = ".,;:!?)]}"
# What looks meant as a file, so a miss is worth saying: a separator or an
# extension. `@property` in pasted code is not.
_LOOKS_LIKE_A_FILE = re.compile(r"[/\\]|\.\w{1,10}$")
MAX_MENTIONS = 10
MENTION_NOTE = "The user attached this file with @, as read_file shows it:\n{content}"
# How many of the files a name matches are said when it matches several.
MENTION_CHOICES = 5


def attach_mentions(wiring: "Wiring", console: Console, text: str) -> str:
    """``@path`` in a message the user typed: the file goes with the
    message, as read_file would show it -- numbered, the first window, cut to
    a tool result's size -- and counts as read (D-058). Only a file in the
    workspace, at most ten, each once; a policy rule that refuses reading it
    refuses this too, while one that would ask is answered by the user having
    typed it. What is attached is scrubbed; the message itself is unchanged.
    """
    attached: list[tuple[str, str]] = []
    seen: set[str] = set()
    files: list[str] | None = None
    for match in _MENTION.finditer(text):
        quoted, bare = match.group(1), match.group(2)
        name = quoted if quoted is not None else bare.rstrip(_MENTION_TRAILING)
        if not name:
            continue
        resolved = _file_at(wiring, name)
        if resolved is None:
            if quoted is None and not _LOOKS_LIKE_A_FILE.search(name):
                continue
            # Not a path here: a file the name ends, as codex's @ finds one
            # by its name (D-107).
            if files is None:
                files = workspace_files(wiring.workspace.root)
            named = files_named(name, files)
            if len(named) > 1:
                shown = ", ".join(named[:MENTION_CHOICES])
                more = ", ..." if len(named) > MENTION_CHOICES else ""
                console.print(f"[dim]@{escape(name)}: {len(named)} files are named so "
                              f"({escape(shown)}{more}); write more of the path; sent as "
                              "written[/dim]", soft_wrap=True)
                continue
            resolved = _file_at(wiring, named[0]) if named else None
            if resolved is None:
                console.print(f"[dim]@{escape(name)}: no such file in the workspace; "
                              "sent as written[/dim]", soft_wrap=True)
                continue
        if resolved.relative in seen:
            continue
        if len(seen) == MAX_MENTIONS:
            console.print(f"[yellow]at most {MAX_MENTIONS} files go with one message; "
                          f"@{escape(name)} does not[/yellow]", soft_wrap=True)
            continue
        seen.add(resolved.relative)
        tool = ReadFile()
        operation = tool.validate({"path": resolved.relative})
        decision, _ = wiring.runtime.policy.evaluate(operation)
        if decision.verdict is Verdict.DENY:
            console.print(f"[yellow]@{escape(name)}: not attached, the policy refuses "
                          f"reading it ({escape(decision.reason)})[/yellow]", soft_wrap=True)
            continue
        try:
            output = tool.execute(operation, wiring.runtime.tool_ctx)
        except ToolError as exc:
            console.print(f"[yellow]@{escape(name)}: not attached, {escape(str(exc))}[/yellow]",
                          soft_wrap=True)
            continue
        attached.append((resolved.relative, output.content))
    if not attached:
        return text
    console.print(f"[dim]attached {escape(', '.join(path for path, _ in attached))}[/dim]",
                  soft_wrap=True)
    scrub = wiring.runtime.context.redactor.scrub
    return "\n\n".join([text, *(MENTION_NOTE.format(content=scrub(content))
                                 for _, content in attached)])


# $name, where name is a skill's (D-113): as codex's mention of one.
_SKILL_MENTION = re.compile(r"(?<![\w$])\$([a-z0-9][a-z0-9-]{0,63})(?![\w-])")
SKILL_NOTE = "The user asked for the skill {name} with ${name}; {tool} gives:\n{content}"
MAX_SKILL_MENTIONS = 3


def attach_skills(wiring: "Wiring", console: Console, typed: str, text: str) -> str:
    """``$name`` in a message the user typed, where name is a skill's: its
    instructions go with the message, as load_skill gives them (D-113) -- at
    most three, each once. Any other ``$word`` is left as it was written."""
    named: list[str] = []
    for match in _SKILL_MENTION.finditer(typed):
        name = match.group(1)
        if name in wiring.skills.found and name not in named:
            named.append(name)
    if not named:
        return text
    if len(named) > MAX_SKILL_MENTIONS:
        console.print(f"[yellow]at most {MAX_SKILL_MENTIONS} skills go with one message; "
                      f"{escape(', '.join(named[MAX_SKILL_MENTIONS:]))} do not[/yellow]",
                      soft_wrap=True)
        named = named[:MAX_SKILL_MENTIONS]
    tool = wiring.runtime.registry.get(LOAD_SKILL)
    attached: list[tuple[str, str]] = []
    for name in named:
        operation = tool.validate({"name": name})
        decision, _ = wiring.runtime.policy.evaluate(operation)
        if decision.verdict is Verdict.DENY:
            console.print(f"[yellow]${escape(name)}: not attached, the policy refuses "
                          f"loading it ({escape(decision.reason)})[/yellow]", soft_wrap=True)
            continue
        try:
            output = tool.execute(operation, wiring.runtime.tool_ctx)
        except ToolError as exc:
            console.print(f"[yellow]${escape(name)}: not attached, {escape(str(exc))}"
                          "[/yellow]", soft_wrap=True)
            continue
        attached.append((name, output.content))
    if not attached:
        return text
    console.print(f"[dim]using the skill {escape(', '.join(n for n, _ in attached))}[/dim]",
                  soft_wrap=True)
    scrub = wiring.runtime.context.redactor.scrub
    return "\n\n".join([text, *(SKILL_NOTE.format(name=name, tool=LOAD_SKILL,
                                                  content=scrub(content))
                                for name, content in attached)])


def _file_at(wiring: "Wiring", name: str):
    """The workspace's file at ``name``, or None."""
    try:
        resolved = wiring.workspace.resolve(name)
        return resolved if resolved.absolute.is_file() else None
    except (PathRejected, OSError):
        return None


def workspace_files(root: Path) -> list[str]:
    """The files git knows in the workspace: tracked, and new ones it does
    not ignore -- so a file ignored, a secret's usual place, is never found
    by its name alone. Nothing when git cannot say."""
    try:
        code, out, _ = run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                               root)
    except ToolError:
        return []
    return [path for path in out.split("\0") if path] if code == 0 else []


def files_named(name: str, files: list[str]) -> list[str]:
    """The files whose path ends with ``name`` -- whole names, a folder or
    more of the path if it says them -- in any case, as Windows reads one."""
    wanted = name.replace("\\", "/").lower()
    return sorted(path for path in files
                  if path.lower() == wanted or path.lower().endswith("/" + wanted))


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
    printed = combined_output(result).replace("\r\n", "\n")
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


def dispatch(wiring: "Wiring", console: Console, name: str, arguments: str) -> object:
    if name.startswith(PROMPTS):
        return _named_prompt(wiring, console, name, arguments)
    command = COMMANDS.get(name)
    if command is not None:
        if arguments and not command.arguments:
            console.print(f"[yellow]/{name} takes no arguments[/yellow]")
            return None
        return command.run(wiring, console, arguments)
    prompt = wiring.prompts.get(name)
    if prompt is not None:
        return _send_prompt(console, name, prompt, arguments)
    hint = CODEX_COMMANDS.get(name)
    if hint is not None:
        console.print(f"[yellow]/{name} is codex's; {hint}[/yellow]", soft_wrap=True)
        return None
    console.print(f"[yellow]unknown command /{escape(name)}; /help lists the commands[/yellow]")
    return None


def _named_prompt(wiring: "Wiring", console: Console, name: str, arguments: str) -> object:
    """``/prompts:NAME``, as codex calls a custom prompt (D-112): only the
    user's own prompts, and the one way to call one a built-in command's name
    hides."""
    wanted = name[len(PROMPTS):]
    prompt = wiring.prompts.get(wanted)
    if prompt is not None:
        return _send_prompt(console, name, prompt, arguments)
    if not wiring.prompts:
        folder = f" -- Markdown files in {wiring.home / 'prompts'}" if wiring.home else ""
        console.print(f"[yellow]you have no custom prompts{escape(folder)}[/yellow]",
                      soft_wrap=True)
    elif not wanted:
        console.print("[yellow]say which: /prompts:NAME; /help lists yours[/yellow]")
    else:
        console.print(f"[yellow]no custom prompt is named {escape(wanted)}; /help lists "
                      "yours[/yellow]", soft_wrap=True)
    return None


def _send_prompt(console: Console, name: str, prompt, arguments: str) -> object:
    try:
        text = expand(prompt, arguments)
    except PromptError as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]", soft_wrap=True)
        return None
    source = "this repository's" if prompt.from_repository else "your"
    console.print(f"[dim]/{name}: sending {source} prompt {escape(str(prompt.path))}[/dim]",
                  soft_wrap=True)
    return RunTask(text)


# codex's commands that are spelled otherwise here, or have no counterpart
# (D-101), told after the user's own prompts, which may take any name.
CODEX_COMMANDS = {
    "approvals": "here it is /mode NAME -- default, accept_edits, plan or dont_ask -- or "
                 "/mode alone to see the session's",
    "mention": "here a file is mentioned by writing @path in the message, as "
               "@src/app.py",
    "logout": "here it is foundry logout, run in your shell",
    "feedback": "Foundry has no feedback channel to send it to",
    "prompts": "a custom prompt is /NAME here, or /prompts:NAME as codex's; /help lists "
               "yours",
}


# --- the built-in commands -------------------------------------------------------------


def _help(wiring: "Wiring", console: Console, arguments: str) -> None:
    width = max(len(f"/{c.name} {c.arguments}") for c in COMMANDS.values())
    for command in COMMANDS.values():
        usage = f"/{command.name} {command.arguments}".rstrip()
        console.print(f"  [cyan]{escape(usage):<{width}}[/cyan]  {command.summary}")
    if wiring.prompts:
        console.print("custom prompts (each also /prompts:NAME, as codex's):")
        for _, prompt in sorted(wiring.prompts.items()):
            source = "repository" if prompt.from_repository else "yours"
            # One a built-in's name hides is called by its namespace (D-112).
            called = prompt.name if prompt.name not in COMMANDS else PROMPTS + prompt.name
            usage = f"/{called} {usage_hint(prompt)}".rstrip()
            console.print(f"  [cyan]{escape(usage):<{width}}[/cyan]  "
                          f"{escape(prompt.description or '(no description)')} [dim]({source})[/dim]",
                          soft_wrap=True)
    console.print("[dim]!COMMAND runs COMMAND in the workspace and tells the model what "
                  "it printed. Anything else you type is a task for the model, and @path "
                  "in it attaches that file; a pasted block is one message, and so is "
                  'what you type between two lines of """[/dim]')
    return None


def _resume_hint(name: str) -> str:
    """How to resume a session by its name, quoted where a shell needs it."""
    return (f"foundry resume {name}" if re.fullmatch(r"[\w.-]+", name)
            else f'foundry resume "{name}"')


def rename(wiring: "Wiring", console: Console, arguments: str) -> None:
    """``/rename NAME``: the session's name, to list it by and resume it by
    (D-125); alone, the name it has."""
    if not arguments.strip():
        if wiring.name:
            console.print(f"this session is named {safe(wiring.name)}; "
                          f"{safe(_resume_hint(wiring.name))} continues it", soft_wrap=True)
        else:
            console.print("[dim]this session has no name; /rename NAME gives it one[/dim]")
        return
    try:
        name = session_name(arguments)
    except ValueError as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]")
        return
    wiring.session.append(EventType.RENAMED, {"name": name})
    wiring.name = name
    console.print(f"[dim]this session is now named {safe(name)}; "
                  f"{safe(_resume_hint(name))} continues it[/dim]", soft_wrap=True)


def _exit(wiring: "Wiring", console: Console, arguments: str) -> str:
    return _EXIT


def _new(wiring: "Wiring", console: Console, arguments: str) -> str:
    return NEW_SESSION


def _fork(wiring: "Wiring", console: Console, arguments: str) -> str | None:
    """``/fork``: a copy of the session so far, to go on in apart from it,
    as codex's /fork (D-126); this one is kept as it is."""
    try:
        wiring.forked = fork_journal(wiring.session.dir.parent, wiring.session.path)
    except OSError as exc:
        console.print(f"[red]cannot fork this session: {escape(str(exc))}[/red]")
        return None
    return FORK_SESSION


def _status(wiring: "Wiring", console: Console, arguments: str) -> None:
    runtime, config = wiring.runtime, wiring.config
    context = runtime.context
    usage = context.session_usage
    used, limit = context.estimated_context_tokens(), context.max_context_tokens
    rows = [
        ("session", wiring.session.session_id + (" (resumed)" if wiring.resumed else "")),
        *([("name", wiring.name)] if wiring.name else []),
        ("workspace", str(wiring.workspace.root)),
        ("model", f"{config.backend.model} ({config.backend.protocol}, profile "
                  f"{config.backend.name})"),
        ("mode", runtime.policy.mode.value),
        ("effort", config.backend.effort or API_DEFAULT_EFFORT),
        *([("verbosity", config.backend.verbosity)] if config.backend.verbosity else []),
        ("tokens", f"{usage.input_tokens:,} in ({usage.cached_input_tokens:,} cached), "
                   f"{usage.output_tokens:,} out"),
        ("context", f"about {used:,} of {limit:,} tokens ({100 * used // max(limit, 1)}%)"
                    + (f"; compacts past {context.compaction_threshold():,}"
                       if runtime.auto_compact and context.compact_above_tokens else "")),
        ("notes", ", ".join(wiring.instructions) or "no FOUNDRY.md or AGENTS.md loaded"),
    ]
    if wiring.mcp is not None and wiring.mcp.summary():
        rows.append(("mcp", wiring.mcp.summary()))
    if runtime.plan:
        done = sum(step.status.value == "completed" for step in runtime.plan)
        rows.append(("plan", f"{done} of {len(runtime.plan)} steps completed"))
    if runtime.git_baseline is not None:
        try:
            evidence = collect_evidence(wiring.workspace.root, runtime.git_baseline)
            changed = (f"{len(evidence.session_changed)} file(s) since the session started, "
                       f"{len(evidence.preexisting_changed)} already changed before it")
            if evidence.head_moved:
                changed += "; HEAD has moved"
        except FoundryError as exc:
            changed = f"unknown ({exc})"
        rows.append(("changed", changed))
    for label, value in rows:
        console.print(f"[dim]{label:<9}[/dim] {escape(value)}", soft_wrap=True)
    return None


def _untracked(root) -> list[str]:
    code, out, _ = run_git(["ls-files", "--others", "--exclude-standard", "-z"], root)
    return sorted(p for p in out.split("\0") if p) if code == 0 else []


def _line_count(wiring: "Wiring", path: str) -> str:
    try:
        data = wiring.workspace.resolve(path).absolute.read_bytes()
    except (OSError, ValueError, FoundryError):
        return "unreadable"
    if b"\0" in data[:8000]:
        return "binary"
    lines = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    return f"{lines} lines"


def _diff(wiring: "Wiring", console: Console, arguments: str) -> None:
    """Every uncommitted change, and which of them this session made."""
    root, baseline = wiring.workspace.root, wiring.runtime.git_baseline
    try:
        evidence = collect_evidence(root, baseline)
    except FoundryError as exc:
        console.print(f"[red]cannot read the changes: {escape(str(exc))}[/red]")
        return None
    if not evidence.changed:
        console.print("[dim]no uncommitted changes[/dim]")
        return None
    if evidence.head_moved:
        console.print("[yellow]HEAD has moved since the session started[/yellow]")
    for path in sorted(evidence.changed):
        whose = "this session" if path in evidence.session_changed else "before this session"
        console.print(f"  {escape(path)} [dim]({whose})[/dim]", soft_wrap=True)

    code, out, err = run_git(["diff", "HEAD"] if evidence.head_now else ["diff"], root)
    if code != 0:
        console.print(f"[red]git diff failed: {escape(err.strip())}[/red]")
        return None
    # Repository text: control sequences stripped, as for any tool output.
    lines = sanitize(out).rstrip().splitlines()
    if len(lines) > DIFF_MAX_LINES:
        lines = lines[:DIFF_MAX_LINES] + [
            f"[... {len(lines) - DIFF_MAX_LINES} more lines; git diff shows them all ...]"]
    if lines:
        console.print(Syntax("\n".join(lines), "diff", theme="ansi_dark", word_wrap=True))
    for path in _untracked(root):
        console.print(f"[green]new file[/green] {escape(path)} "
                      f"[dim]({_line_count(wiring, path)})[/dim]", soft_wrap=True)
    return None


def _plan(wiring: "Wiring", console: Console, arguments: str) -> None:
    if wiring.runtime.plan:
        print_plan(console, wiring.runtime.plan, wiring.runtime.plan_explanation)
    else:
        console.print("[dim]no plan: the model has not set one in this session[/dim]")
    return None


def _compact(wiring: "Wiring", console: Console, arguments: str) -> None:
    """Summarise the conversation now rather than when it fills (D-040)."""
    try:
        failed = wiring.runtime.compact(auto=False, focus=arguments)
    except (Cancelled, KeyboardInterrupt):
        # Ctrl+C stops the summary, not the session (D-062); the history is
        # replaced only once a summary is in.
        failed = "compaction cancelled"
    if failed:
        console.print(f"[yellow]{escape(failed)}; the conversation is unchanged[/yellow]")
    return None


def _image(wiring: "Wiring", console: Console, arguments: str) -> None:
    then = "; it goes with your next message"
    if arguments.strip():
        attach(wiring, console, arguments, then)
    else:
        attach_clipboard(wiring, console, then)
    return None


def _init(wiring: "Wiring", console: Console, arguments: str) -> RunTask | None:
    root = wiring.workspace.root
    existing = [name for name in doc_names(wiring.config.project_doc_fallback_filenames)
                if (root / name).exists()]
    if existing:
        console.print(f"[yellow]{existing[0]} already exists; edit it, or ask the model to "
                      "improve it[/yellow]")
        return None
    console.print("[dim]asking the model to write FOUNDRY.md; sessions read it from the next "
                  "one on" + ("" if wiring.trusted else ", once you trust this workspace")
                  + "[/dim]")
    return RunTask(builtin_prompt("init"))


REVIEW_UNCOMMITTED = ("Review the uncommitted changes in this repository, as a careful "
                      "code reviewer would. `git_status` and `git_diff` show what changed.")
REVIEW_USAGE = "usage: /review [--base REV | --commit REV] [focus]"


def _review(wiring: "Wiring", console: Console, arguments: str) -> RunTask | None:
    """``/review [focus]``: the uncommitted changes, read-only (D-039).
    ``--base REV``: what this branch changed since it left REV, as a pull
    request shows it; ``--commit REV``: what one commit changed (D-066). The
    revision is resolved before the task starts, and the model is given its
    id, so what it reviews is what the user named."""
    words = arguments.split(maxsplit=2)
    if words and words[0] in ("--base", "--commit"):
        if len(words) < 2:
            console.print(f"[yellow]{REVIEW_USAGE}[/yellow]")
            return None
        flag, revision, focus = words[0], words[1], words[2] if len(words) > 2 else ""
        try:
            commit = resolve_commit(revision, wiring.workspace.root)
        except ToolError as exc:
            console.print(f"[yellow]{escape(str(exc))}[/yellow]")
            return None
        if flag == "--base":
            scope = (f"Review what this branch changed since it left {revision} (commit "
                     f"{commit}): the commits since their merge base, and anything not yet "
                     "committed -- as a careful code reviewer would. `git_diff` with "
                     f'{{"base": "{commit}"}} shows them, and `git_status` what is not '
                     "committed.")
            what = f"the changes since {revision} ({commit[:12]})"
        else:
            scope = (f"Review what commit {commit} changed, as a careful code reviewer "
                     f'would. `git_diff` with {{"commit": "{commit}"}} shows its message '
                     "and its changes.")
            what = f"commit {commit[:12]}"
    else:
        scope, what, focus = REVIEW_UNCOMMITTED, "the uncommitted changes", arguments
    text = f"{scope}\n\n{builtin_prompt('review')}"
    if focus:
        text += f"\n\nThe user asks you to focus on: {focus}"
    console.print(f"[dim]reviewing {escape(what)}; nothing is changed while it runs[/dim]",
                  soft_wrap=True)
    return RunTask(text, read_only=REVIEW_READ_ONLY)


# --- /approve (D-038) ------------------------------------------------------------------

_APPROVE_INTO = {"y": Mode.DEFAULT, "e": Mode.ACCEPT_EDITS}


def approve_plan(wiring: "Wiring", console: Console, arguments: str = "") -> RunTask | None:
    """``/approve``: end plan mode as the user chooses (D-038), and return the
    task that starts the work -- or None, with nothing changed.

    Only the user can do this, by typing it at the prompt: nothing the model
    writes, and nothing a tool prints, reaches the REPL's commands. The
    permissions paragraph is built again, so the model is never told rules that
    no longer hold, and the change is journaled.
    """
    runtime = wiring.runtime
    policy = runtime.policy
    if policy.mode is not Mode.PLAN:
        console.print(f"[dim]nothing to approve: this session is in {policy.mode.value} "
                      "mode, not plan mode[/dim]")
        return None
    if runtime.plan:
        print_plan(console, runtime.plan, runtime.plan_explanation)
    else:
        console.print("[dim]the model has not set a plan with update_plan; approving lets "
                      "it act on what it described[/dim]")
    try:
        answer = console.input("approve the plan? (y)es, ask before each edit / (e)dits "
                               "apply without asking / (n)o, keep planning: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        answer = ""
    mode = _APPROVE_INTO.get(answer[:1])
    if mode is None:
        console.print("[dim]still in plan mode[/dim]")
        return None

    policy.mode = mode
    runtime.context.system_prompt = system_prompt(policy)
    wiring.session.append(EventType.MODE_CHANGE, {"from": Mode.PLAN.value, "to": mode.value,
                                                  "by": "repl",
                                                  "reason": "the user approved the plan"})
    runtime.events.emit(Notice(f"plan approved: this session is in {mode.value} mode now"))
    # Approved, the work starts at once: what the user approved is the plan,
    # so the next turn carries it out.
    return RunTask("The user approved your plan, so plan mode is over. " + mode_line(mode)
                   + " Carry out the plan, and keep it current with update_plan.")


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
    hooks = wiring.config.hooks
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


# --- /undo (D-036) ---------------------------------------------------------------------


def show_undo_plan(console: Console, plan: Plan, others: list[str]) -> None:
    if plan.steps:
        console.print(f"will undo {len(plan.steps)} file(s):")
        for step in plan.steps:
            label = {"none": "unchanged"}.get(step.action, step.action)
            console.print(f"  {label:<9} {escape(step.path)}", soft_wrap=True)
    if plan.skipped:
        console.print("[yellow]cannot undo:[/yellow]")
        for skip in plan.skipped:
            console.print(f"  {escape(skip.path)}: {escape(skip.reason)}", soft_wrap=True)
    if others:
        console.print("[dim]not undone, changed by a command or outside the session: "
                      f"{escape(', '.join(others))}[/dim]", soft_wrap=True)


def show_outcome(console: Console, outcome: Outcome) -> None:
    if outcome.reverted:
        console.print(f"[green]undone:[/green] {len(outcome.reverted)} file(s)")
    for skip in outcome.failed:
        console.print(f"[red]not undone:[/red] {escape(skip.path)}: {escape(skip.reason)}",
                      soft_wrap=True)


def confirm(console: Console, question: str) -> bool:
    try:
        return console.input(f"{question} [y/N]: ").strip().lower().startswith("y")
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False


def undo_last(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/undo``: revert the session's newest patch, after asking.

    The model is told on its next turn, its command evidence from before the
    undo stops counting, and it reads each restored file again before patching.
    """
    records = list(SessionStore.read_records(wiring.session.path))
    plan = plan_undo(recorded_changes(records), wiring.workspace, wiring.session.artifacts,
                     last=True)
    if plan.empty:
        console.print("[dim]nothing to undo: no patch of this session is left to revert[/dim]")
        return None
    show_undo_plan(console, plan, [])
    if not plan.steps or not confirm(console, "undo these changes?"):
        console.print("[dim]nothing was changed[/dim]")
        return None
    outcome = apply_undo(plan, wiring.workspace, wiring.session.artifacts)
    ordinal = wiring.session.append(EventType.UNDO,
                                    undo_payload(plan, outcome, scope="last", by="repl"))
    wiring.runtime.files_changed(ordinal, [s.path for s in outcome.reverted])
    note = undo_note(outcome)
    if note:
        wiring.note = f"{wiring.note}\n\n{note}" if wiring.note else note
    show_outcome(console, outcome)
    return None


# How much of a message /rewind shows, so it can be sent again, edited.
REWIND_SHOWN = 2_000
REWIND_NOTE = ("The user went back to an earlier point in this conversation: what came "
               "after it is gone from your view, but nothing in the workspace was "
               "reverted.{changed} Commands run since may have changed other files; check "
               "the workspace before relying on it, and read a file again before patching it.")


def rewind_points(history: list[Message]) -> list[int]:
    """Where /rewind can go back to: before each of the user's messages since
    the conversation, or its last compaction, began."""
    start = 0
    for index, message in enumerate(history):
        if message.role is Role.USER and message.text_content.startswith(SUMMARY_PREFIX):
            start = index + 1
    return [index for index in range(start, len(history))
            if history[index].role is Role.USER
            and any(isinstance(b, TextBlock) for b in history[index].blocks)]


def rewind(wiring: "Wiring", console: Console, arguments: str = "") -> None:
    """``/rewind [N]``: go back to before your last message, or the Nth last
    (D-056), as codex's Esc Esc does. The model no longer sees that message
    or anything after it; the message is shown, to be sent again as it was
    or edited. Nothing in the workspace changes -- /undo reverts patches --
    and the model is told so with the next message, with the files it
    patched after that point.
    """
    arguments = arguments.strip()
    if arguments and not (arguments.isdigit() and int(arguments) >= 1):
        console.print("[yellow]usage: /rewind [N] -- N, from 1, counts your messages back "
                      "from the last[/yellow]")
        return None
    count = int(arguments or 1)
    history = wiring.runtime.context.history
    points = rewind_points(history)
    if not points:
        console.print("[dim]nothing to go back to: no message of yours since the "
                      "conversation, or its last compaction, began[/dim]")
        return None
    if count > len(points):
        console.print(f"[yellow]only {len(points)} of your messages can be gone back "
                      "to[/yellow]")
        return None
    index = points[-count]
    text, _ = truncate_middle(history[index].text_content, REWIND_SHOWN)
    changed = wiring.runtime.rewind(index)
    console.print("[dim]went back to before this message; the model no longer sees it or "
                  "what followed, and files are as they are (/undo reverts patches):[/dim]")
    console.print(escape(sanitize(text)), soft_wrap=True)
    tell_model(wiring, REWIND_NOTE.format(changed=(
        f" Files you patched after that point are still changed: {', '.join(changed)}."
        if changed else "")))
    return None


COMMANDS: dict[str, Command] = {c.name: c for c in (
    Command("help", "this list", _help),
    Command("status", "session, model, mode, tokens, context and plan", _status),
    Command("diff", "every uncommitted change, and which of them this session made", _diff),
    Command("copy", "copy the latest answer to the clipboard, or its Nth code block",
            copy_answer, arguments="[N]"),
    Command("export", "the whole conversation as Markdown: on the clipboard, or in a new "
            "file at PATH", export_conversation, arguments="[PATH]"),
    Command("mcp", "the MCP servers, and the tools each offers", show_mcp),
    Command("hooks", "the lifecycle hooks this session runs, and from which file", show_hooks),
    Command("skills", "the skills the model may load; $name in a message asks for one",
            show_skills),
    Command("plan", "the model's current plan", _plan),
    Command("compact", "summarise the conversation so far to free context", _compact,
            arguments="[focus]"),
    Command("image", "attach an image to your next message -- a file, or the clipboard's",
            _image, arguments="[PATH]"),
    Command("approve", "in plan mode: approve the plan and let the work start", approve_plan),
    Command("mode", "show or change what the session may do without asking", set_mode,
            arguments="[NAME]"),
    Command("effort", "show or change how hard the model reasons", set_effort,
            arguments="[LEVEL]"),
    Command("model", "show or change the model, on the same endpoint", set_model,
            arguments="[NAME]"),
    Command("undo", "revert this session's newest patch", undo_last),
    Command("rewind", "go back to before your last message, or the Nth last; files stay "
            "as they are", rewind, arguments="[N]"),
    Command("review", "a read-only review of the uncommitted changes, a branch's or a "
            "commit's", _review, arguments="[--base REV | --commit REV] [focus]"),
    Command("init", "ask the model to write FOUNDRY.md for this repository", _init),
    Command("rename", "name this session, to resume it by name; alone, its name", rename,
            arguments="[NAME]"),
    Command("new", "end this session and start a fresh one", _new),
    Command("fork", "go on in a copy of this session, keeping this one as it is", _fork),
    Command("exit", "end the session", _exit),
    Command("quit", "end the session", _exit),
)}
