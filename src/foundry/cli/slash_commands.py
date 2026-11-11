"""The commands typed at the prompt (D-039): the table, and what each does.

Commands are read from the user's input and nowhere else -- nothing the model
writes and nothing a tool prints reaches this table -- so the ones that change
what the session may do (/approve, /undo) are the user's alone.

Built-in commands come first, then the user's custom prompts, then a trusted
repository's (core/commands.py). An unknown command is said to be unknown
rather than sent to the model as a task: "/udno" should not start a turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from foundry.cli.copying import copy_answer
from foundry.cli.exporting import export_conversation
from foundry.cli.goals import goal_command
from foundry.cli.memories import memories_command
from foundry.cli.render import print_plan, safe, sanitize
from foundry.cli.attachments import attach, attach_clipboard
from foundry.cli.session_settings import (
    API_DEFAULT_EFFORT,
    set_effort,
    set_mode,
    set_model,
    show_hooks,
    show_mcp,
    show_skills,
    tell_model,
)
from foundry.core.commands import PROMPTS, builtin_prompt, expand, usage_hint
from foundry.core.config_sections import TERMINAL_TITLE_ITEMS, title_items
from foundry.core.context import SUMMARY_PREFIX
from foundry.core.conversation import Message, Role, TextBlock
from foundry.core.errors import Cancelled, ConfigError, FoundryError, PromptError, ToolError
from foundry.core.events import Notice
from foundry.core.goal import usage_line
from foundry.core.policy.engine import Mode
from foundry.core.prompts import doc_names, mode_line, system_prompt
from foundry.core.resume import fork_journal, session_name
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import truncate_middle
from foundry.core.tools.git import collect_evidence, resolve_commit, run_git
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
    from foundry.cli.wiring import Wiring


# What repl() returns when the user typed /new: the caller closes this session
# and starts another.
NEW_SESSION = "new_session"

# /fork (D-126): the session goes on as the fork the wiring names.
FORK_SESSION = "fork_session"

# /archive and /delete (D-128): the session ends, then is archived or deleted.
ARCHIVE_SESSION = "archive_session"

DELETE_SESSION = "delete_session"

# /resume (D-134): the session goes on as another, the one the wiring names or
# one the user picks; this one ends, and can be resumed.
RESUME_SESSION = "resume_session"


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
    # A side question (/side, D-129): answered, then dropped from the
    # conversation.
    side: bool = False
    # The model it runs on, the session's own again after it: /review's,
    # backend.review_model (D-143). "" for the session's.
    model: str = ""


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    summary: str
    run: Callable[["Wiring", Console, str], object]
    arguments: str = ""          # the usage hint; empty: the command takes none


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
    # codex's that Foundry has another way, or no need, for (D-130).
    "permissions": "here it is /mode NAME -- default, accept_edits, plan or dont_ask -- or "
                   "/mode alone to see the session's",
    "cd": "a session works in one workspace: foundry -C DIR starts one in another",
    "usage": "/status shows this session's tokens; Foundry keeps no account's usage",
    "debug-config": "foundry doctor checks the configuration and the environment",
    "ps": "Foundry runs no background terminals: each command runs to its end",
    "stop": "Foundry runs no background terminals: Ctrl+C stops the running task",
    # The rest of codex's (D-140): what Foundry has instead, or that it has none.
    "clean": "Foundry runs no background terminals: Ctrl+C stops the running task",
    "config": "Foundry's settings are in config.toml in the Foundry folder: foundry doctor "
              "names it and checks them, and /status shows this session's",
    "vim": "the prompt edits with your terminal's own keys; Foundry has no Vim mode",
    "keymap": "the prompt edits with your terminal's own keys; Foundry has none to remap",
    "raw": "Foundry writes into the terminal's own scrollback, so text is selected there "
           "as usual; /copy copies an answer as the model wrote it",
    "theme": "code is coloured with the terminal's own colours, so its theme is the one "
             "used",
    "statusline": "the prompt shows the context left; /status shows the model, mode, "
                  "tokens and the rest",
    "ide": "Foundry has no IDE connection: mention a file with @path",
    "app": "Foundry runs in the terminal only",
    "apps": "Foundry has no apps: tools come from MCP servers, added with foundry mcp add",
    "plugins": "Foundry has no plugins: tools come from MCP servers, added with foundry mcp "
               "add, and skills from the skills folder",
    "daemon": "Foundry runs no background server: each session is a process of its own",
    "voice": "Foundry has no voice input",
    "pets": "Foundry has no terminal pet",
    "import": "Foundry imports nothing: it reads AGENTS.md and FOUNDRY.md, and the skills "
              "in .agents/skills, as they are",
    "tui": "Foundry has one terminal interface",
    "presets": "a custom prompt is a saved one: ~/.foundry/prompts/NAME.md is /NAME",
    "experimental": "Foundry has no experimental features; codex's [features] are read "
                    "as Foundry's own settings, and foundry features list says what each is "
                    "here",
    "agents": "Foundry's sub-agents work while the session waits, each line of theirs shown "
              "as 'agent N:' -- ask for one in a message; /side asks on the side, /fork goes "
              "on in a copy, and another terminal runs another session",
    "setup-default-sandbox": "Foundry has no sandbox (docs/threat-model.md says what that "
                             "leaves)",
    "worktree": "git worktree add ../DIR makes one; foundry -C ../DIR starts a session in it",
    "daybreak": "Foundry has no Daybreak",
}

CODEX_COMMANDS["pet"] = CODEX_COMMANDS["pets"]

CODEX_COMMANDS["subagents"] = CODEX_COMMANDS["agents"]


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


def _resume(wiring: "Wiring", console: Console, arguments: str) -> str:
    """``/resume [SESSION]``: another of this workspace's sessions, by id or
    name or picked from the recent ones, goes on, and this one ends, as
    codex's /resume (D-134)."""
    wiring.resuming = arguments
    return RESUME_SESSION


def _goal(wiring: "Wiring", console: Console, arguments: str) -> RunTask | None:
    """``/goal`` (D-127): the turn it starts, if any."""
    if not wiring.config.goals:
        # codex's goals feature, off (D-144).
        console.print("[yellow]goals are off in your configuration (features.goals = "
                      "false)[/yellow]")
        return None
    prompt = goal_command(wiring, console, arguments, confirm)
    return RunTask(prompt) if prompt is not None else None


SIDE_READ_ONLY = "a side question changes nothing: edits and commands are refused"

SIDE_PROMPT = ("The user asks a question on the side. Answer it from the conversation so "
               "far and what you can read; change nothing, and do not go on with the "
               "work in this answer. The question and your answer are then dropped from "
               "the conversation.\n\nThe question: {question}")


def _side(wiring: "Wiring", console: Console, arguments: str) -> RunTask | None:
    """``/side QUESTION`` (and ``/btw``): a question answered read-only, from
    the conversation so far, then dropped from it, as codex's side
    conversation in an ephemeral fork (D-129)."""
    question = arguments.strip()
    if not question:
        console.print("[yellow]usage: /side QUESTION -- asked on the side, then dropped "
                      "from the conversation[/yellow]")
        return None
    return RunTask(SIDE_PROMPT.format(question=question), read_only=SIDE_READ_ONLY,
                   side=True)


def _archive(wiring: "Wiring", console: Console, arguments: str) -> str:
    """``/archive`` (D-128): the session ends and is archived, as codex's."""
    return ARCHIVE_SESSION


def _delete(wiring: "Wiring", console: Console, arguments: str) -> str | None:
    """``/delete`` (D-128): the session ends and is deleted for good, once
    the user says so."""
    if not confirm(console, "delete this session for good, its journal and artifacts?"):
        console.print("[dim]nothing was deleted[/dim]")
        return None
    return DELETE_SESSION


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
        *([("reviews", f"on {config.backend.review_model} (review_model)")]
          if config.backend.review_model else []),
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
    goal = runtime.goal_now()
    if goal is not None:
        rows.append(("goal", f"{goal.status} ({usage_line(goal)}): "
                             f"{goal.objective.splitlines()[0][:60]}"))
    if runtime.git_baseline is not None:
        try:
            evidence = collect_evidence(wiring.workspace.root, runtime.git_baseline)
            changed = (f"{len(evidence.session_changed)} file(s) since the session started, "
                       f"{len(evidence.preexisting_changed)} already changed before it")
            if evidence.outside_changed:
                # A resumed session's, written while it was stopped (D-177).
                changed += f", {len(evidence.outside_changed)} changed outside it since"
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
        whose = ("this session" if path in evidence.session_changed
                 else "outside this session" if path in evidence.outside_changed
                 else "before this session")
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


RECAP = ("Recap this conversation for the user, now: what they asked for, what was done "
         "-- the files changed, the commands run and what they showed -- what was decided, "
         "and what is left. Plainly and briefly, from the conversation alone: read nothing, "
         "change nothing.")

RECAP_READ_ONLY = "a recap changes nothing"


def _recap(wiring: "Wiring", console: Console, arguments: str) -> RunTask:
    """``/recap``: the conversation summarized for the user now, as codex's
    (D-130) -- an answer in the conversation, unlike /compact, which replaces
    it."""
    return RunTask(RECAP, read_only=RECAP_READ_ONLY)


def _clear(wiring: "Wiring", console: Console, arguments: str) -> str:
    """``/clear``: the terminal cleared and a fresh session started, as
    codex's (D-130); this one ends, and can be resumed, as with /new."""
    console.clear()
    return NEW_SESSION


def _rollout(wiring: "Wiring", console: Console, arguments: str) -> None:
    """``/rollout``: where this session's journal is, as codex prints its
    rollout file (D-130)."""
    console.print(escape(str(wiring.session.path)), soft_wrap=True)
    return None


def _pwd(wiring: "Wiring", console: Console, arguments: str) -> None:
    """``/pwd``: the folder the session works in (D-130)."""
    console.print(escape(str(wiring.workspace.root)), soft_wrap=True)
    return None


def _warnings(wiring: "Wiring", console: Console, arguments: str) -> None:
    """``/warnings``: the warnings and errors this session has shown, again
    (D-138), as codex's -- one may have scrolled away unread."""
    renderer = wiring.renderer
    if not renderer.warnings:
        console.print("[dim]no warnings or errors in this session[/dim]")
        return None
    if renderer.warnings_dropped:
        console.print(f"[dim]{renderer.warnings_dropped} earlier ones are not kept[/dim]")
    for when, level, text in renderer.warnings:
        said = (f"[red]error[/red] {escape(text)}" if level == "error"
                else f"[yellow]{escape(text)}[/yellow]")
        console.print(f"[dim]{when}[/dim] {said}", soft_wrap=True)
    return None


def _title(wiring: "Wiring", console: Console, arguments: str) -> None:
    """``/title [ITEM...|none]``: what the terminal's title shows, or which
    items it shows for the rest of this session (D-139), as codex's /title."""
    renderer = wiring.renderer
    words = arguments.split()
    keep = "[runtime] terminal_title in config.toml keeps them"
    if not words:
        console.print(f"the terminal title shows {', '.join(renderer.title_items) or 'nothing'}",
                      soft_wrap=True)
        for item, what in TERMINAL_TITLE_ITEMS.items():
            console.print(f"  [bold]{item}[/bold]  [dim]{escape(what)}[/dim]", soft_wrap=True)
        console.print(f"[dim]/title ITEM... picks them for this session, /title none shows "
                      f"none; {escape(keep)}[/dim]", soft_wrap=True)
        return None
    try:
        items = () if words == ["none"] else title_items(words)
    except ConfigError as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]", soft_wrap=True)
        return None
    renderer.title_items = items
    if items:
        renderer.title()
        console.print(f"[dim]the terminal title now shows {', '.join(items)} in this session; "
                      f"{escape(keep)}[/dim]", soft_wrap=True)
    else:
        renderer.clear_title()
        console.print("[dim]the terminal title is left alone in this session[/dim]")
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


# git_diff shows the files git tracks: told the two "show what changed", a
# review's model found a new file only because git_status listed it (D-179).
REVIEW_UNCOMMITTED = ("Review the uncommitted changes in this repository, as a careful "
                      "code reviewer would. `git_status` lists every changed file, new ones "
                      "included; `git_diff` shows the changes to the files git tracks, not a "
                      "new file's contents -- read those with `read_file`.")

REVIEW_USAGE = "usage: /review [--base REV | --commit REV] [focus]"

REVIEW_AS_ASKED = ("Review this repository as the user asks below, as a careful code reviewer "
                   "would. `git_status` lists what is not committed, new files included, and "
                   "`git_diff` shows those changes to the files git tracks (read a new file "
                   'with `read_file`); `git_diff` with {"base": REV} or {"commit": REV} shows '
                   "what a branch or a commit changed.")


def review_task(root: Path, *, base: str | None = None, commit: str | None = None,
                title: str = "", focus: str = "", instructions: str = "") -> tuple[str, str]:
    """A review's prompt, and what it reviews in a few words: the
    uncommitted changes; what this branch changed since it left ``base``;
    what ``commit`` changed; or what the user's ``instructions`` ask
    (D-131). /review and ``foundry review`` both build theirs here. A
    revision is resolved first, and the model is given its id, so what it
    reviews is what the user named: ToolError when it does not resolve."""
    if instructions:
        scope, what = f"{REVIEW_AS_ASKED}\n\nThe user asks: {instructions}", "what you asked"
    elif base is not None:
        sha = resolve_commit(base, root)
        scope = (f"Review what this branch changed since it left {base} (commit "
                 f"{sha}): the commits since their merge base, and anything not yet "
                 "committed -- as a careful code reviewer would. `git_diff` with "
                 f'{{"base": "{sha}"}} shows them, and `git_status` what is not '
                 "committed; a new file not yet committed is not in the diff -- read it "
                 "with `read_file`.")
        what = f"the changes since {base} ({sha[:12]})"
    elif commit is not None:
        sha = resolve_commit(commit, root)
        titled = f' (the user calls it "{title}")' if title else ""
        scope = (f"Review what commit {sha}{titled} changed, as a careful code reviewer "
                 f'would. `git_diff` with {{"commit": "{sha}"}} shows its message '
                 "and its changes.")
        what = f"commit {sha[:12]}" + (f" ({title})" if title else "")
    else:
        scope, what = REVIEW_UNCOMMITTED, "the uncommitted changes"
    text = f"{scope}\n\n{builtin_prompt('review')}"
    if focus:
        text += f"\n\nThe user asks you to focus on: {focus}"
    return text, what


def _review(wiring: "Wiring", console: Console, arguments: str) -> RunTask | None:
    """``/review [focus]``: the uncommitted changes, read-only (D-039).
    ``--base REV``: what this branch changed since it left REV, as a pull
    request shows it; ``--commit REV``: what one commit changed (D-066)."""
    words = arguments.split(maxsplit=2)
    base = commit = None
    focus = arguments
    if words and words[0] in ("--base", "--commit"):
        if len(words) < 2:
            console.print(f"[yellow]{REVIEW_USAGE}[/yellow]")
            return None
        focus = words[2] if len(words) > 2 else ""
        if words[0] == "--base":
            base = words[1]
        else:
            commit = words[1]
    try:
        text, what = review_task(wiring.workspace.root, base=base, commit=commit, focus=focus)
    except ToolError as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]")
        return None
    console.print(f"[dim]reviewing {escape(what)}; nothing is changed while it runs[/dim]",
                  soft_wrap=True)
    return RunTask(text, read_only=REVIEW_READ_ONLY,
                   model=wiring.config.backend.review_model or "")


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
    # The user's choice, as /mode's is: /new, /fork and /resume start in it.
    # Left out, a fork of an approved session went back to plan mode, said
    # to be "as configured" when only the user's /mode plan had chosen it
    # (D-172).
    wiring.chosen["mode"] = mode
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
    Command("pwd", "the folder this session works in", _pwd),
    Command("cwd", "the same as /pwd", _pwd),
    Command("title", "what the terminal title shows, or pick it for this session", _title,
            arguments="[ITEM...|none]"),
    Command("rollout", "where this session's journal is", _rollout),
    Command("warnings", "the warnings and errors this session has shown, again", _warnings),
    Command("copy", "copy the latest answer to the clipboard, or its Nth code block",
            copy_answer, arguments="[N]"),
    Command("export", "the whole conversation as Markdown: on the clipboard, or in a new "
            "file at PATH", export_conversation, arguments="[PATH]"),
    Command("mcp", "the MCP servers, and the tools each offers", show_mcp),
    Command("hooks", "the lifecycle hooks this session runs, and from which file", show_hooks),
    Command("skills", "the skills the model may load; $name in a message asks for one",
            show_skills),
    Command("memories", "the workspace's memories, and whether this session uses them and "
            "is read for them", memories_command,
            arguments="[use on|off | generate on|off]"),
    Command("plan", "the model's current plan", _plan),
    Command("goal", "set a goal the session works toward, turn after turn, until it is "
            "done; alone, the goal", _goal, arguments="[OBJECTIVE]"),
    Command("side", "ask something on the side: answered read-only, then dropped from the "
            "conversation", _side, arguments="QUESTION"),
    Command("btw", "the same as /side", _side, arguments="QUESTION"),
    Command("compact", "summarise the conversation so far to free context", _compact,
            arguments="[focus]"),
    Command("recap", "the conversation so far, summed up for you now", _recap),
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
    Command("clear", "clear the terminal, end this session and start a fresh one", _clear),
    Command("fork", "go on in a copy of this session, keeping this one as it is", _fork),
    Command("resume", "go on in another of this workspace's sessions, picked from the "
            "recent ones or named; this one ends, and can be resumed", _resume,
            arguments="[SESSION]"),
    Command("archive", "end this session and archive it: out of every list, kept whole",
            _archive),
    Command("delete", "end this session and delete it for good", _delete),
    Command("exit", "end the session", _exit),
    Command("quit", "end the session", _exit),
)}
