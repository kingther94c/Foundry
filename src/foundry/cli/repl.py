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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from foundry.cli.render import print_plan, sanitize
from foundry.core.commands import builtin_prompt, expand, parse_command
from foundry.core.errors import Cancelled, FoundryError
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


def run_task(wiring: "Wiring", task: RunTask):
    policy = wiring.runtime.policy
    policy.read_only = task.read_only
    try:
        return wiring.runtime.run_turn(with_note(wiring, task.text))
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
    ]
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
    Command("approve", "in plan mode: approve the plan and let the work start", approve_plan),
    Command("undo", "revert this session's newest patch", undo_last),
    Command("review", "a read-only review of the uncommitted changes", _review,
            arguments="[focus]"),
    Command("init", "ask the model to write FOUNDRY.md for this repository", _init),
    Command("new", "end this session and start a fresh one", _new),
    Command("exit", "end the session", _exit),
    Command("quit", "end the session", _exit),
)}
