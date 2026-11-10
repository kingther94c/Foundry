"""The commands that run a session at the terminal and look after the ones
kept: ``run``, ``resume``, ``fork``, ``undo``, ``sessions``, ``archive`` and
``delete``.

The interactive prompt and its slash commands are in cli/repl.py.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from foundry.cli.render import safe
from foundry.cli.transcript import show_conversation
from foundry.cli.slash_commands import ARCHIVE_SESSION, DELETE_SESSION, FORK_SESSION, NEW_SESSION
from foundry.cli.slash_commands import RESUME_SESSION
from foundry.cli.attachments import attach as _attach
from foundry.cli.attachments import take_images as _take_images
from foundry.cli.slash_commands import confirm as _confirm
from foundry.cli.repl import repl as _repl
from foundry.cli.slash_commands import show_outcome as _show_outcome
from foundry.cli.slash_commands import show_undo_plan as _show_plan
from foundry.cli.repl import with_note as _with_note
from foundry.cli.setup_commands import start_memory_pass
from foundry.cli.wiring import Wiring, _announce_found, _close, _config_args, _say_farewell, build
from foundry.core.config import load_config, user_dir
from foundry.core.errors import FoundryError, ResumeError, SessionLocked, UndoError
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.redaction import default_redactor
from foundry.core.resume import (
    ARCHIVED,
    find_journal,
    fork_journal,
    latest_session,
    named_session,
    recent_sessions,
    recorded_workspace,
    resolve_session,
    same_path,
    session_named,
    summarize,
)
from foundry.core.resume import delete_session as _delete_session
from foundry.core.resume import move_session as _move_session
from foundry.core.retention import expired, remove
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.git import capture_baseline, is_git_repository
from foundry.core.undo import apply_undo, other_changes, plan_undo, read_source, undo_payload
from foundry.core.workspace import PathRejected, Workspace


# How many sessions the resume picker offers, and how much of each one's
# first message it shows (D-057).
PICK_LIMIT = 10

TASK_PREVIEW = 60


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _preview(task: str) -> str:
    """A message's first line, cut to fit a listing."""
    line = task.strip().splitlines()[0] if task.strip() else "(no message)"
    return line if len(line) <= TASK_PREVIEW else line[:TASK_PREVIEW - 3] + "..."


def _session_line(summary) -> str:
    style = "green" if summary.status is TerminalStatus.COMPLETED else "yellow"
    busy = "  [cyan]in use[/cyan]" if summary.in_use else ""
    named = f"[bold]{safe(summary.name)}[/bold]  " if summary.name else ""
    return (f"{summary.session_id}  [{style}]{summary.status.value}[/{style}]{busy}  "
            f"{named}[dim]{safe(_preview(summary.first_task))}[/dim]")


def pick_session(console, workspace: Path | None) -> str | None:
    """Which of this workspace's recent sessions to resume, as codex's
    `resume` asks (D-057): Enter is the newest. None: the user chose none.
    With one session or none there is nothing to choose, and ``last`` says so.
    With no workspace, every workspace's, each with where it was (D-088)."""
    recent = recent_sessions(user_dir() / "sessions", workspace, PICK_LIMIT)
    if len(recent) < 2:
        return "last"
    return _ask_which(console, recent, workspace)


def _ask_which(console, recent: list, workspace: Path | None) -> str | None:
    """The one of ``recent`` the user picks; Enter is the first. None: none."""
    where = f" in {safe(str(workspace))}" if workspace is not None else ""
    console.print(f"[bold]recent sessions{where}[/bold]")
    for number, summary in enumerate(recent, 1):
        place = f"  [dim]{safe(summary.workspace)}[/dim]" if workspace is None else ""
        console.print(f" {number:>2}  {_session_line(summary)}{place}", soft_wrap=True)
    while True:
        try:
            answer = console.input("resume which? [1] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None
        if not answer:
            return recent[0].session_id
        if answer.isdigit() and 1 <= int(answer) <= len(recent):
            return recent[int(answer) - 1].session_id
        console.print(f"[yellow]a number from 1 to {len(recent)}, or Enter for the "
                      "newest; Ctrl+C resumes none[/yellow]")


def _choose_resume(args: argparse.Namespace, console) -> int | None:
    """Which session `foundry resume` continues, and in which workspace
    (D-057; ``--last`` and ``--all`` as codex spells them, D-088). None to go
    on; an exit code to stop."""
    sessions = user_dir() / "sessions"
    everywhere = bool(getattr(args, "all", False))
    if getattr(args, "last", False):
        if args.resume is not None and args.task is not None:
            console.print("[red]--last resumes the newest session: give it a task, not a "
                          "session id[/red]")
            return 2
        if args.resume is not None:
            # `resume --last TASK`: the one word given is the task.
            args.task = args.resume
        args.resume = "last"
    elif args.resume is None:
        # A person at a terminal picks; a script gets the newest (D-057).
        if not _interactive():
            args.resume = "last"
        else:
            args.resume = pick_session(console, None if everywhere
                                       else Path(args.workspace).resolve())
            if args.resume is None:
                return EXIT_CODES[TerminalStatus.CANCELLED]
    if not everywhere:
        return None
    # From any workspace: the session is continued where it was recorded.
    if args.resume == "last":
        args.resume = latest_session(sessions, None)
        if args.resume is None:
            console.print("[red]no recorded session[/red]")
            return EXIT_CODES[TerminalStatus.FAILED]
    journal = find_journal(sessions, args.resume)
    if journal is None:
        # A name /rename gave it, in any workspace (D-125).
        named = named_session(sessions, args.resume, None)
        journal = find_journal(sessions, named) if named is not None else None
        if journal is None:
            console.print(f"[red]no such session: {safe(args.resume)}[/red]")
            return EXIT_CODES[TerminalStatus.FAILED]
        args.resume = named
    recorded = recorded_workspace(journal.parent)
    if not recorded:
        console.print(f"[red]session {safe(args.resume)} does not say where it was "
                      "recorded[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]
    args.workspace = recorded
    console.print(f"[dim]resuming {safe(args.resume)} in {safe(recorded)}[/dim]")
    return None


def _fork(args: argparse.Namespace, console) -> int | None:
    """`foundry fork`: the session chosen as `resume` chooses one, copied
    into a new one that is then continued (D-126). None to go on."""
    sessions = user_dir() / "sessions"
    try:
        journal = resolve_session(sessions, args.resume, Path(args.workspace).resolve())
        args.resume = fork_journal(sessions, journal)
    except (ResumeError, OSError) as exc:
        console.print(f"[red]cannot fork: {escape(str(exc))}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]
    console.print(f"[dim]forked {journal.parent.name} into {args.resume}; "
                  f"{journal.parent.name} is kept as it was[/dim]", soft_wrap=True)
    return None


def _forget(sessions: Path, session_id: str) -> None:
    """A fork that could not be continued: nothing was done in it."""
    shutil.rmtree(sessions / session_id, ignore_errors=True)


def cmd_session(args: argparse.Namespace) -> int:
    """`run`; `resume`, which is the same session continued from its
    journal; and `fork`, a copy of one continued apart from it (D-126)."""
    console = Console()
    command = getattr(args, "command", None)
    if command in ("resume", "fork"):
        stop = _choose_resume(args, console)
        if stop is None and command == "fork":
            stop = _fork(args, console)
        if stop is not None:
            return stop
    try:
        flags = _config_args(args)
        wiring = build(Path(args.workspace).resolve(), console=console,
                       resume=getattr(args, "resume", None), **flags)
    except FoundryError as exc:
        console.print(f"[red]{exc}[/red]")
        if command == "fork":
            _forget(user_dir() / "sessions", args.resume)
        return EXIT_CODES[TerminalStatus.FAILED]

    wiring.renderer.show_disclosure(str(wiring.workspace.root),
                                    wiring.config.backend.model,
                                    wiring.config.mode.value)
    if _interactive():
        # Where the conversation was, as codex's resume shows it (D-108).
        show_conversation(wiring)
    _announce_found(wiring)
    for raw in getattr(args, "image", None) or []:
        if not _attach(wiring, console, raw):
            _close(wiring, TerminalStatus.CANCELLED, "an image could not be attached")
            return 2
    status = TerminalStatus.CANCELLED
    # /archive or /delete (D-128): done once the session is closed.
    put_away, reason = "", "session ended"
    try:
        if args.task and not _interactive():
            # Nobody at a terminal to go on with (D-078): the task, then the
            # end, its exit code the task's.
            outcome = wiring.runtime.run_turn(_with_note(wiring, args.task),
                                              _take_images(wiring), typed=args.task)
            # Only the finish gate can produce 'completed'. A turn that simply
            # ended is unfinished work, not success.
            status = outcome.status or TerminalStatus.PARTIAL
        else:
            # At a terminal a task given here is the first message, and the
            # session goes on after it, as `codex "task"` does (D-078).
            first = args.task
            if _interactive() and wiring.memory_folder is not None:
                # codex reads sessions for memories as one starts (D-145).
                start_memory_pass(wiring, args.profile)
            while (ended := _repl(wiring, console, first)) in (NEW_SESSION, FORK_SESSION,
                                                                RESUME_SESSION):
                first = None
                if ended == FORK_SESSION:
                    wiring = _switch_to_fork(wiring, console, flags)
                    continue
                if ended == RESUME_SESSION:
                    wiring = _switch_to_resumed(wiring, console, flags)
                    continue
                # /new: this session ends, and a fresh one starts in the same
                # workspace -- a new journal, an empty conversation, no grants.
                _close(wiring, TerminalStatus.CANCELLED, "the user started a new session")
                _say_farewell(console, wiring)
                # The model and mode chosen with /model and /mode carry over, as
                # if they had been given on the command line (D-047).
                flags = {**flags, "overrides": {**(flags["overrides"] or {}),
                                                **wiring.chosen} or None}
                try:
                    wiring = build(wiring.workspace.root, home=wiring.home, console=console,
                                   **flags)
                except FoundryError as exc:
                    console.print(f"[red]{exc}[/red]")
                    return EXIT_CODES[TerminalStatus.FAILED]
                console.print(f"[dim]new session {wiring.session.session_id}[/dim]")
                _announce_found(wiring)
            if ended in (ARCHIVE_SESSION, DELETE_SESSION):
                put_away, ended = ended, TerminalStatus.CANCELLED
                reason = ("the user archived this session" if put_away == ARCHIVE_SESSION
                          else "the user deleted this session")
            status = ended
    except KeyboardInterrupt:
        wiring.runtime.cancel()
        console.print("\n[yellow]cancelled[/yellow]")
    finally:
        _close(wiring, status, reason)
        if not put_away:
            _say_farewell(console, wiring)
    if put_away:
        return _put_away(wiring.session.dir, put_away == DELETE_SESSION, console)
    return EXIT_CODES.get(status, 0)


def _put_away(folder: Path, delete: bool, console) -> int:
    """/archive and /delete (D-128): the session, closed, archived or deleted."""
    session_id = folder.name
    try:
        if delete:
            _delete_session(folder)
        else:
            _move_session(folder, folder.parent.parent / ARCHIVED / session_id)
    except (ResumeError, OSError) as exc:
        console.print(f"[red]cannot {'delete' if delete else 'archive'} session "
                      f"{session_id}: {escape(str(exc))}[/red]", soft_wrap=True)
        return EXIT_CODES[TerminalStatus.FAILED]
    console.print(f"deleted session {session_id}" if delete else
                  f"archived session {session_id}; foundry unarchive {session_id} brings it "
                  "back", soft_wrap=True)
    return EXIT_CODES[TerminalStatus.CANCELLED]


def cmd_archive(args: argparse.Namespace) -> int:
    """`foundry archive SESSION` and `foundry unarchive SESSION` (D-128), as
    codex's: the session, by id or name, out of every list and lookup -- or
    back in them -- kept whole."""
    console = Console()
    live, archived = user_dir() / "sessions", user_dir() / ARCHIVED
    archive = args.command == "archive"
    source, destination = (live, archived) if archive else (archived, live)
    session_id = session_named(source, args.session)
    if session_id is None:
        elsewhere = session_named(destination, args.session) is not None
        console.print(f"[red]{escape(_unfound(args.session, archive, elsewhere))}[/red]",
                      soft_wrap=True)
        return 1
    try:
        _move_session(source / session_id, destination / session_id)
    except (ResumeError, OSError) as exc:
        console.print(f"[red]cannot {args.command} {session_id}: {escape(str(exc))}[/red]",
                      soft_wrap=True)
        return 1
    console.print(f"archived session {session_id}; foundry unarchive {session_id} brings it "
                  "back" if archive else
                  f"unarchived session {session_id}; foundry resume {session_id} continues it",
                  soft_wrap=True)
    return 0


def _unfound(target: str, archive: bool, elsewhere: bool) -> str:
    if not elsewhere:
        return f"no such session: {target}"
    return f"session {target} is already archived" if archive else \
        f"session {target} is not archived"


def cmd_delete(args: argparse.Namespace) -> int:
    """`foundry delete SESSION` (D-128), as codex's: a session, archived or
    not, deleted for good -- so asked first; --force skips the question, and
    takes only an id, since a name could name another session than meant."""
    console = Console()
    for root in (user_dir() / "sessions", user_dir() / ARCHIVED):
        session_id = session_named(root, args.session)
        if session_id is not None:
            break
    else:
        console.print(f"[red]no such session: {escape(args.session)}[/red]")
        return 1
    if args.force and session_id != args.session:
        console.print("[red]--force takes a session id; a session named is deleted only "
                      "once you say so[/red]")
        return 2
    if not args.force:
        console.print(_session_line(summarize(root / session_id)), soft_wrap=True)
        if not _confirm(console, f"delete session {session_id} for good, its journal and "
                                 "artifacts?"):
            console.print("[dim]nothing was deleted[/dim]")
            return 1
    try:
        _delete_session(root / session_id)
    except (ResumeError, OSError) as exc:
        console.print(f"[red]cannot delete {session_id}: {escape(str(exc))}[/red]",
                      soft_wrap=True)
        return 1
    console.print(f"deleted session {session_id}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """`foundry prune` (D-163): the sessions last written more than N days
    ago, removed now -- N from --days, else session_retention_days. Listed
    and asked first, as foundry delete asks; --dry-run only lists them, and
    --force does not ask. Archived sessions are not among them."""
    console = Console()
    days = args.days
    if days is None:
        try:
            days = load_config(home=user_dir()).session_retention_days
        except FoundryError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]", soft_wrap=True)
            return 1
        if days == 0:
            console.print("[dim]session_retention_days is 0: every session is kept. "
                          "foundry prune --days N removes those last written more than N "
                          "days ago[/dim]", soft_wrap=True)
            return 0
    if days < 1:
        console.print("[red]--days takes a whole number of days, at least 1[/red]")
        return 2
    folders = expired(user_dir() / "sessions", days)
    if not folders:
        console.print(f"[dim]no session was last written more than {days} day(s) ago[/dim]")
        return 0
    for folder in folders:
        console.print(_session_line(summarize(folder)), soft_wrap=True)
    if args.dry_run:
        console.print(f"[dim]{len(folders)} session(s) would be removed; nothing was[/dim]")
        return 0
    if not args.force and not _confirm(
            console, f"delete these {len(folders)} session(s) for good, their journals and "
                     "artifacts?"):
        console.print("[dim]nothing was deleted[/dim]")
        return 1
    swept = remove(folders)
    console.print(f"deleted {len(swept.removed)} session(s)")
    if swept.left:
        console.print(f"[yellow]left {len(swept.left)}, in use or not removable: "
                      f"{', '.join(swept.left)}[/yellow]", soft_wrap=True)
        return 1
    return 0


def _switch_to_fork(wiring: Wiring, console, flags: dict) -> Wiring:
    """/fork (D-126): the fork the REPL made is continued, and this session
    ends, kept as it was. If the fork cannot be continued -- its HEAD has
    moved, say -- it is dropped and this session goes on."""
    source, forked = wiring.session.session_id, wiring.forked
    flags = {**flags, "overrides": {**(flags["overrides"] or {}), **wiring.chosen} or None}
    try:
        fork = build(wiring.workspace.root, home=wiring.home, console=console,
                     resume=forked, **flags)
    except FoundryError as exc:
        _forget(wiring.session.dir.parent, forked)
        console.print(f"[red]cannot continue the fork: {escape(str(exc))}[/red]; "
                      "this session goes on", soft_wrap=True)
        return wiring
    _close(wiring, TerminalStatus.CANCELLED, f"the user forked this session into {forked}")
    _say_farewell(console, wiring)
    fork.chosen = dict(wiring.chosen)
    _announce_found(fork)
    console.print(f"[dim]now in {forked}, a fork of {source}, which is kept as it "
                  "was[/dim]", soft_wrap=True)
    return fork


def _switch_to_resumed(wiring: Wiring, console, flags: dict) -> Wiring:
    """/resume (D-134): the session /resume named -- or the one picked from
    this workspace's recent others -- is continued, with every check a
    resume makes, and this one ends, recorded and resumable. One that cannot
    be continued is not, and this session goes on."""
    sessions = (wiring.home or user_dir()) / "sessions"
    this, target = wiring.session.session_id, wiring.resuming
    if not target:
        others = [s for s in recent_sessions(sessions, wiring.workspace.root, PICK_LIMIT + 1)
                  if s.session_id != this][:PICK_LIMIT]
        if not others:
            console.print("[dim]no other session in this workspace to resume[/dim]")
            return wiring
        target = _ask_which(console, others, wiring.workspace.root)
        if target is None:
            return wiring
    try:
        journal = resolve_session(sessions, target, wiring.workspace.root)
        if journal.parent.name == this:
            console.print("[dim]that is this session[/dim]")
            return wiring
        flags = {**flags, "overrides": {**(flags["overrides"] or {}), **wiring.chosen} or None}
        resumed = build(wiring.workspace.root, home=wiring.home, console=console,
                        resume=journal.parent.name, **flags)
    except FoundryError as exc:
        console.print(f"[red]cannot resume {safe(target)}: {escape(str(exc))}[/red]; this "
                      "session goes on", soft_wrap=True)
        return wiring
    _close(wiring, TerminalStatus.CANCELLED,
           f"the user resumed session {resumed.session.session_id}")
    _say_farewell(console, wiring)
    resumed.chosen = dict(wiring.chosen)
    if _interactive():
        # Where its conversation was, as `foundry resume` shows it (D-108).
        show_conversation(resumed)
    _announce_found(resumed)
    console.print(f"[dim]now in {resumed.session.session_id}; {this} is kept, and can be "
                  "resumed[/dim]", soft_wrap=True)
    return resumed


def cmd_undo(args: argparse.Namespace) -> int:
    """Revert a recorded session's patches (D-036): 0 when all of it was undone,
    1 when some or all of it could not be, 2 when asked to without a way to ask."""
    console = Console()
    sessions = user_dir() / "sessions"
    try:
        workspace = Workspace(Path(args.workspace).resolve())
        journal = resolve_session(sessions, args.session, workspace.root)
        store = SessionStore(sessions, session_id=journal.parent.name,
                             redactor=default_redactor(), resume=True)
    except SessionLocked as exc:
        console.print(f"[red]{escape(str(exc))}; type /undo in that session instead[/red]")
        return 1
    except (FoundryError, PathRejected, OSError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return 1
    try:
        source = read_source(store.path)
        if not source.workspace or not same_path(source.workspace, workspace.root):
            raise UndoError(f"session {source.session_id} worked in {source.workspace or '?'}, "
                            f"not {workspace.root}; undo it with --workspace pointing there")
        plan = plan_undo(list(source.changes), workspace, store.artifacts, last=args.last)
        others: list[str] = []
        if is_git_repository(workspace.root):
            current = capture_baseline(workspace.root)
            others = other_changes(source, current)
            if source.baseline is not None and current.head != source.baseline.head:
                console.print("[yellow]HEAD moved since this session started; undo still "
                              "touches only files that hold exactly what it wrote[/yellow]")
        task = source.first_task.splitlines()[0][:80] if source.first_task else "?"
        console.print(f"session {source.session_id} ({escape(repr(task))})", soft_wrap=True)
        if plan.empty:
            console.print("nothing to undo: no patch of this session is left to revert")
            _show_plan(console, plan, others)
            return 0
        _show_plan(console, plan, others)
        if not plan.steps:
            return 1
        if args.dry_run:
            console.print("[dim]dry run: nothing was changed[/dim]")
            return 0
        if not args.yes:
            if not sys.stdin or not sys.stdin.isatty():
                console.print("[red]nobody to ask: pass --yes to undo without a prompt[/red]")
                return 2
            if not _confirm(console, "undo these changes?"):
                console.print("[dim]nothing was changed[/dim]")
                return 1
        outcome = apply_undo(plan, workspace, store.artifacts)
        store.append(EventType.UNDO, undo_payload(plan, outcome,
                                                  scope="last" if args.last else "all", by="cli"))
        _show_outcome(console, outcome)
        return 0 if not plan.skipped and not outcome.failed else 1
    except FoundryError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return 1
    finally:
        store.close()


def cmd_sessions(args: argparse.Namespace) -> int:
    console = Console()
    root = user_dir() / (ARCHIVED if args.archived else "sessions")
    if not root.is_dir():
        console.print("[dim]no archived sessions[/dim]" if args.archived
                      else "[dim]no sessions yet[/dim]")
        return 0

    entries = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    if args.session_id:
        # A session id is a name, not a path. `foundry sessions ../notes` used to
        # read whatever it landed on and print a traceback when the lines were
        # not journal records.
        target = find_journal(root, args.session_id)
        if target is None:
            named = named_session(root, args.session_id, None)
            target = find_journal(root, named) if named is not None else None
        if target is None:
            console.print(f"[red]no such session: {args.session_id}[/red]")
            return 1
        for record in SessionStore.read_records(target):
            console.print(f"[dim]{record.ordinal:>4}[/dim] {record.type}")
        return 0

    for entry in entries[:args.limit]:
        if not (entry / "events.jsonl").is_file():
            continue
        console.print(_session_line(summarize(entry)), soft_wrap=True)
    return 0
