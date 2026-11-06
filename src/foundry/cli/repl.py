"""The interactive session: its prompt, and the commands typed at it (D-039).

A line that starts with ``/name`` is a command; anything else is a task for
the model. Commands are read from the user's input and nowhere else -- nothing
the model writes and nothing a tool prints reaches this table -- so the ones
that change what the session may do (/approve, /undo) are the user's alone.

Built-in commands come first, then the user's custom prompts, then a trusted
repository's (core/commands.py). An unknown command is said to be unknown
rather than sent to the model as a task: "/udno" should not start a turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from foundry.cli.render import print_plan, sanitize
from foundry.core.commands import builtin_prompt, expand, parse_command
from foundry.core.errors import Cancelled, FoundryError, ImageRejected
from foundry.core.images import MAX_IMAGES_PER_MESSAGE, load as load_image
from foundry.core.events import Notice, TerminalStatus
from foundry.core.policy.engine import Mode
from foundry.core.prompts import PROJECT_DOC_NAMES, mode_line, system_prompt
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.git import collect_evidence, run_git
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
_EXIT = "exit"

DIFF_MAX_LINES = 400
REVIEW_READ_ONLY = "a review changes nothing: edits and commands are refused until it ends"


@dataclass(frozen=True, slots=True)
class RunTask:
    """A task for the model, from the user's line or from a command."""

    text: str
    # Set: the policy refuses every change while this task runs (/review).
    read_only: str = ""


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
    if not wiring.config.backend.images:
        console.print("[yellow]this backend is set up without images "
                      "(backend.images = false)[/yellow]")
        return False
    if len(wiring.images) >= MAX_IMAGES_PER_MESSAGE:
        console.print(f"[yellow]at most {MAX_IMAGES_PER_MESSAGE} images go with one "
                      "message[/yellow]")
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


def take_images(wiring: "Wiring") -> tuple:
    images, wiring.images = tuple(wiring.images), []
    return images


def run_task(wiring: "Wiring", task: RunTask):
    policy = wiring.runtime.policy
    policy.read_only = task.read_only
    try:
        return wiring.runtime.run_turn(with_note(wiring, task.text), take_images(wiring))
    finally:
        policy.read_only = ""


def repl(wiring: "Wiring", console: Console) -> TerminalStatus | str:
    """Read lines until the session ends; NEW_SESSION if the user asked for one."""
    while True:
        try:
            line = console.input("\n[bold cyan]foundry>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return TerminalStatus.CANCELLED
        if not line:
            continue
        task: object = RunTask(line)
        parsed = parse_command(line)
        if parsed is not None:
            task = dispatch(wiring, console, *parsed)
            if task == _EXIT:
                return TerminalStatus.CANCELLED
            if task == NEW_SESSION:
                return NEW_SESSION
            if not isinstance(task, RunTask):
                continue
        outcome = run_task(wiring, task)
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
        return outcome.status


def dispatch(wiring: "Wiring", console: Console, name: str, arguments: str) -> object:
    command = COMMANDS.get(name)
    if command is not None:
        if arguments and not command.arguments:
            console.print(f"[yellow]/{name} takes no arguments[/yellow]")
            return None
        return command.run(wiring, console, arguments)
    prompt = wiring.prompts.get(name)
    if prompt is not None:
        source = "this repository's" if prompt.from_repository else "your"
        console.print(f"[dim]/{name}: sending {source} prompt {escape(str(prompt.path))}[/dim]",
                      soft_wrap=True)
        return RunTask(expand(prompt, arguments))
    console.print(f"[yellow]unknown command /{escape(name)}; /help lists the commands[/yellow]")
    return None


# --- the built-in commands -------------------------------------------------------------


def _help(wiring: "Wiring", console: Console, arguments: str) -> None:
    width = max(len(f"/{c.name} {c.arguments}") for c in COMMANDS.values())
    for command in COMMANDS.values():
        usage = f"/{command.name} {command.arguments}".rstrip()
        console.print(f"  [cyan]{escape(usage):<{width}}[/cyan]  {command.summary}")
    custom = [p for name, p in sorted(wiring.prompts.items()) if name not in COMMANDS]
    if custom:
        console.print("custom prompts:")
        for prompt in custom:
            source = "repository" if prompt.from_repository else "yours"
            usage = f"/{prompt.name} {prompt.argument_hint}".rstrip()
            console.print(f"  [cyan]{escape(usage):<{width}}[/cyan]  "
                          f"{escape(prompt.description or '(no description)')} [dim]({source})[/dim]",
                          soft_wrap=True)
    console.print("[dim]anything else you type is a task for the model[/dim]")
    return None


def _exit(wiring: "Wiring", console: Console, arguments: str) -> str:
    return _EXIT


def _new(wiring: "Wiring", console: Console, arguments: str) -> str:
    return NEW_SESSION


def _status(wiring: "Wiring", console: Console, arguments: str) -> None:
    runtime, config = wiring.runtime, wiring.config
    context = runtime.context
    usage = context.session_usage
    used, limit = context.estimated_context_tokens(), context.max_context_tokens
    rows = [
        ("session", wiring.session.session_id + (" (resumed)" if wiring.resumed else "")),
        ("workspace", str(wiring.workspace.root)),
        ("model", f"{config.backend.model} ({config.backend.protocol}, profile "
                  f"{config.backend.name})"),
        ("mode", runtime.policy.mode.value),
        ("tokens", f"{usage.input_tokens:,} in ({usage.cached_input_tokens:,} cached), "
                   f"{usage.output_tokens:,} out"),
        ("context", f"about {used:,} of {limit:,} tokens ({100 * used // max(limit, 1)}%)"),
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
    except Cancelled:
        failed = "compaction cancelled"
    if failed:
        console.print(f"[yellow]{escape(failed)}; the conversation is unchanged[/yellow]")
    return None


def _image(wiring: "Wiring", console: Console, arguments: str) -> None:
    attach(wiring, console, arguments, "; it goes with your next message")
    return None


def _init(wiring: "Wiring", console: Console, arguments: str) -> RunTask | None:
    root = wiring.workspace.root
    existing = [name for name in PROJECT_DOC_NAMES if (root / name).exists()]
    if existing:
        console.print(f"[yellow]{existing[0]} already exists; edit it, or ask the model to "
                      "improve it[/yellow]")
        return None
    console.print("[dim]asking the model to write FOUNDRY.md; sessions read it from the next "
                  "one on" + ("" if wiring.trusted else ", once you trust this workspace")
                  + "[/dim]")
    return RunTask(builtin_prompt("init"))


def _review(wiring: "Wiring", console: Console, arguments: str) -> RunTask:
    text = builtin_prompt("review")
    if arguments:
        text += f"\n\nThe user asks you to focus on: {arguments}"
    console.print("[dim]reviewing the uncommitted changes; nothing is changed while it "
                  "runs[/dim]")
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


COMMANDS: dict[str, Command] = {c.name: c for c in (
    Command("help", "this list", _help),
    Command("status", "session, model, mode, tokens, context and plan", _status),
    Command("diff", "every uncommitted change, and which of them this session made", _diff),
    Command("plan", "the model's current plan", _plan),
    Command("compact", "summarise the conversation so far to free context", _compact,
            arguments="[focus]"),
    Command("image", "attach an image to your next message", _image, arguments="PATH"),
    Command("approve", "in plan mode: approve the plan and let the work start", approve_plan),
    Command("mode", "show or change what the session may do without asking", set_mode,
            arguments="[NAME]"),
    Command("model", "show or change the model, on the same endpoint", set_model,
            arguments="[NAME]"),
    Command("undo", "revert this session's newest patch", undo_last),
    Command("review", "a read-only review of the uncommitted changes", _review,
            arguments="[focus]"),
    Command("init", "ask the model to write FOUNDRY.md for this repository", _init),
    Command("new", "end this session and start a fresh one", _new),
    Command("exit", "end the session", _exit),
    Command("quit", "end the session", _exit),
)}
