"""The commands that run a task with nobody at the terminal: ``exec``,
``review`` and ``record`` -- a task from the command line or stdin, an
outcome on stdout, ``--json`` events, and an exit code that says how it went.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from foundry.cli.repl import REVIEW_READ_ONLY, review_task
from foundry.cli.repl import attach as _attach
from foundry.cli.repl import take_images as _take_images
from foundry.cli.repl import with_note as _with_note
from foundry.cli.session_commands import _forget, _fork
from foundry.cli.wiring import Wiring, _announce_resume, _close, _config_args, build
from foundry.core.backends.recording import RecordingBackend
from foundry.core.config import user_dir
from foundry.core.errors import FoundryError, ToolError
from foundry.core.events import EXIT_CODES, SessionStarted, TerminalStatus, Termination
from foundry.core.json_schema import check_schema
from foundry.core.policy.engine import Mode
from foundry.core.prompts import system_prompt
from foundry.core.redaction import default_redactor
from foundry.core.resume import CONTINUE_PROMPT, resolve_session
from foundry.core.runtime import AgentRuntime
from foundry.core.session import SessionStore
from foundry.core.winapi import decode_output


def _plain(value):
    """Make one event field JSON-safe without inventing a schema for it."""
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _event_payload(event) -> dict:
    """Everything the event carries, `kind` first."""
    payload = {"kind": getattr(event, "kind", "event")}
    if dataclasses.is_dataclass(event):
        for field in dataclasses.fields(event):
            if field.name != "kind":
                payload[field.name] = _plain(getattr(event, field.name))
    return payload


FINISH_REMINDER = (
    "This run is unattended: nobody will read a reply. Call finish now to report "
    "the outcome, citing the event_id of each command that supports it."
)

RESULT_REMINDER = " Pass the result it asks for as `result`, matching the schema given for it."


# Why a run that ended without finish is partial.
NO_FINISH = ("the model ended its turn without calling finish, even when reminded; only "
             "finish reports completion, with the evidence for it")


def unattended(wiring: Wiring) -> None:
    """A session nobody watches -- exec's, or foundry mcp-server's (D-103):
    there is no one to ask, so what policy would ask about is refused."""
    wiring.runtime.approval = None  # nobody to ask
    wiring.runtime.policy.interactive = False
    # The permissions paragraph says so, in a mode whose own line speaks of
    # approval.
    wiring.runtime.context.system_prompt = system_prompt(wiring.runtime.policy)


def announce_start(wiring: Wiring) -> None:
    """The session's id and settings, then what a resume found."""
    backend = wiring.config.backend
    wiring.runtime.events.emit(SessionStarted(
        session_id=wiring.session.session_id, workspace=str(wiring.workspace.root),
        model=backend.model, resumed=wiring.resumed is not None,
        protocol=backend.protocol, profile=backend.name,
        mode=wiring.runtime.policy.mode.value, effort=backend.effort,
        reasoning_summary=backend.reasoning_summary, verbosity=backend.verbosity,
        ephemeral=wiring.ephemeral is not None))
    _announce_resume(wiring)


def _run_unattended(runtime: AgentRuntime, task: str, images: tuple = ()):
    """One task; a reply in prose gets a single reminder to call finish.

    Only finish reports an outcome, and nobody reads a reply in an unattended
    run. Running on its own repository, Foundry finished the work, described
    it in prose -- with the event ids -- and exited partial. Asked once, not
    in a loop: a model that cannot call tools must still end.
    """
    outcome = runtime.run_turn(task, images)
    if outcome.status is None:
        asks_result = getattr(runtime.registry.tools.get("finish"), "result_schema", None)
        outcome = runtime.run_turn(FINISH_REMINDER + (RESULT_REMINDER if asks_result is not None
                                                      else ""))
    return outcome


# The most a schema file may hold: it goes into every request, in finish.
MAX_SCHEMA_BYTES = 64_000

# The most `exec -` reads from stdin: a task longer than this is a mistake.
MAX_STDIN_TASK_BYTES = 1_000_000

_BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))


def _task_from_stdin(command: str = "exec", what: str = "task") -> str:
    """`exec -` (D-053): the task, read whole from stdin, as `codex exec -`
    reads it -- or `review -`'s instructions (D-131). UTF-8, or what a
    byte-order mark says (PowerShell 5.1's files are UTF-16), or else the
    console's code page. ValueError when there is nothing to read, or nobody
    piped anything in."""
    stream = sys.stdin
    if stream is None or stream.isatty():
        raise ValueError(f"{command} - reads the {what} from stdin; pipe it in, as "
                         f"`Get-Content {what}.md | foundry {command} -`")
    buffer = getattr(stream, "buffer", None)
    data = (buffer.read(MAX_STDIN_TASK_BYTES + 1) if buffer is not None
            else stream.read(MAX_STDIN_TASK_BYTES + 1).encode("utf-8"))
    if len(data) > MAX_STDIN_TASK_BYTES:
        raise ValueError(f"the task on stdin is larger than {MAX_STDIN_TASK_BYTES // 1_000_000} MB")
    for mark, encoding in _BOMS:
        if data.startswith(mark):
            text = data[len(mark):].decode(encoding, errors="replace")
            break
    else:
        text = decode_output(data).text
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise ValueError(f"stdin held no {what}")
    return text


def _load_output_schema(path: Path) -> Any:
    """exec --output-schema (D-051): a JSON Schema file of the subset
    json_schema enforces. OSError or ValueError, with what is wrong."""
    if path.stat().st_size > MAX_SCHEMA_BYTES:
        raise ValueError(f"larger than {MAX_SCHEMA_BYTES // 1000} KB")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    check_schema(schema)
    return schema


def _result_text(outcome) -> str | None:
    """The run's result as the JSON a script reads, scrubbed; None when
    finish passed none."""
    if outcome is None or outcome.result_json is None:
        return None
    return json.dumps(default_redactor().scrub_obj(json.loads(outcome.result_json)),
                      ensure_ascii=False)


def cmd_exec(args: argparse.Namespace, read_only: str = "") -> int:
    """Headless: one task, no prompts, ASK resolves to DENY.

    Fail-closed is the whole point. An unattended run that silently approved
    whatever it was asked would be strictly worse than one that stops.
    ``read_only``, when given, refuses every edit and command for the whole
    run, saying why -- as `foundry review`'s is (D-131).
    """
    console = _stderr_console(args)
    if args.fork is not None and args.resume is not None:
        console.print("[red]--fork and --resume each name the session to go on from; "
                      "give one[/red]")
        return 2
    # A fork goes on from its source as a resume does (D-133).
    source = args.resume if args.fork is None else args.fork
    if args.task is None and source is None:
        console.print("[red]exec needs a task, or --resume SESSION to continue one[/red]")
        return 2
    if args.ephemeral and source is not None:
        console.print("[red]--ephemeral starts a session that is not kept; it cannot "
                      "continue one[/red]")
        return 2
    if args.task == "-":
        try:
            args.task = _task_from_stdin()
        except (OSError, ValueError) as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            return 2
    schema = None
    if getattr(args, "output_schema", None):
        try:
            schema = _load_output_schema(Path(args.output_schema))
        except (OSError, ValueError) as exc:
            console.print(f"[red]--output-schema {escape(args.output_schema)}: "
                          f"{escape(str(exc))}[/red]")
            return 2
    forked = None
    try:
        if source is not None and args.task is None:
            # Continuing a finished session needs to be told what to do next;
            # "continue where it stopped" would only finish it again.
            journal = resolve_session(user_dir() / "sessions", source,
                                      Path(args.workspace).resolve())
            if SessionStore.terminal_status(journal) is TerminalStatus.COMPLETED:
                console.print("[red]that session completed; give the task to continue "
                              "it with[/red]")
                return 2
        if args.fork is not None:
            # The copy is made, then resumed with every check a resume makes.
            args.resume = args.fork
            stop = _fork(args, console)
            if stop is not None:
                return stop
            forked = args.resume
        # dont_ask unless --mode chose plan or accept_edits; a configured mode
        # never decides how much an unattended run may do.
        wiring = build(Path(args.workspace).resolve(), console=console, interactive=False,
                       resume=args.resume, ephemeral=args.ephemeral,
                       trust=getattr(args, "trust", False),
                       review=read_only == REVIEW_READ_ONLY,
                       **_config_args(args, mode=Mode(args.mode or Mode.DONT_ASK.value)))
    except FoundryError as exc:
        if forked is not None:
            _forget(user_dir() / "sessions", forked)
        console.print(f"[red]{exc}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]

    unattended(wiring)
    wiring.runtime.policy.read_only = read_only
    if schema is not None:
        wiring.runtime.registry.get("finish").result_schema = schema

    if args.json:
        # Serialize whatever the event actually carries. A hand-maintained
        # attribute list silently dropped every field nobody remembered to add:
        # token_count arrived as `{"kind": "token_count"}` with no numbers, and
        # tool_begin/tool_end had no call_id, so a consumer could not pair them.
        def as_json(event) -> None:
            print(json.dumps(_event_payload(event), ensure_ascii=False), flush=True)

        wiring.runtime.events.subscribe(as_json)
    # First, so a script knows what to resume before anything can go wrong.
    announce_start(wiring)
    for raw in args.image or []:
        if not _attach(wiring, console, raw):
            _close(wiring, TerminalStatus.CANCELLED, "an image could not be attached")
            return 2

    status = TerminalStatus.FAILED
    reason = "headless run ended"
    outcome = None
    try:
        outcome = _run_unattended(wiring.runtime,
                                  _with_note(wiring, args.task or CONTINUE_PROMPT),
                                  _take_images(wiring))
        # Only the finish gate can produce 'completed'. A turn that simply
        # ended is unfinished work, not success.
        status = outcome.status or TerminalStatus.PARTIAL
        if outcome.status is None:
            # Saying "headless run ended" told the operator nothing about why a
            # correct-looking answer exited 10. Against a model that cannot call
            # tools at all, this is the *only* reachable outcome.
            reason = NO_FINISH
        if schema is not None:
            # stdout is the result and nothing else, for a script to parse;
            # without one the summary goes to stderr and the exit code says why.
            result = _result_text(outcome)
            if result is not None:
                if not args.json:
                    print(result)
                if args.output_last_message:
                    _write_last_message(Path(args.output_last_message), result, console)
            elif not args.json and outcome.text:
                print(outcome.text, file=sys.stderr)
        else:
            if not args.json and outcome.text:
                print(outcome.text)
            if args.output_last_message:
                _write_last_message(Path(args.output_last_message), outcome.text, console)
    except KeyboardInterrupt:
        wiring.runtime.cancel()
        status = TerminalStatus.CANCELLED
        reason = "interrupted"
        if outcome is None:
            # Stopped while it ran: the Interrupt hooks are told (D-117).
            wiring.runtime.interrupt_hooks()
    finally:
        # The event protocol documents Termination as the terminal event, but a
        # headless run that ended normally never reached runtime._terminate, so
        # the --json stream just stopped: a CI consumer had no final status in
        # the stream at all and had to infer it from the exit code.
        if outcome is None or outcome.status is None:
            wiring.runtime.events.emit(Termination(
                status=status, reason=reason,
                summary=(outcome.text if outcome else "")))
        _close(wiring, status, reason)
    return EXIT_CODES.get(status, 0)


# What a headless review is asked to end with (D-131): only finish reports
# an outcome, and the review is what it reports.
REVIEW_FINISH = ("Nobody reads a reply in this run: once the review is done, call `finish` "
                 "with status `completed` and the whole review as its summary -- the summary "
                 "is what is printed.")

REVIEW_TARGETS = ("review needs one of --uncommitted, --base BRANCH, --commit SHA, or the "
                  "review's instructions")


def cmd_review(args: argparse.Namespace) -> int:
    """``foundry review``, as ``codex review`` (D-131): the uncommitted
    changes, a branch's since it left BRANCH, one commit's, or what the
    user's instructions ask, reviewed headlessly as exec runs a task, with
    every edit and command refused. The review is the run's finish summary,
    printed, and written by -o."""
    console = _stderr_console(args)
    # argparse lets one at most be given.
    if not (args.uncommitted or args.base is not None or args.commit is not None
            or args.prompt is not None):
        console.print(f"[red]{REVIEW_TARGETS}[/red]")
        return 2
    if args.title is not None and args.commit is None:
        console.print("[red]--title names a commit: it goes with --commit[/red]")
        return 2
    instructions = (args.prompt or "").strip()
    if args.prompt == "-":
        try:
            instructions = _task_from_stdin("review", "instructions")
        except (OSError, ValueError) as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            return 2
    elif args.prompt is not None and not instructions:
        console.print("[red]the review's instructions are empty[/red]")
        return 2
    try:
        text, what = review_task(Path(args.workspace).resolve(), base=args.base,
                                 commit=args.commit, title=args.title or "",
                                 instructions=instructions)
    except ToolError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return 2
    console.print(f"[dim]reviewing {escape(what)}; nothing is changed while it runs[/dim]",
                  soft_wrap=True)
    args.task = f"{text}\n\n{REVIEW_FINISH}"
    args.resume = args.fork = args.output_schema = args.mode = None
    args.image = []
    return cmd_exec(args, read_only=REVIEW_READ_ONLY)


def _write_last_message(path: Path, text: str, console: Console) -> None:
    """exec --output-last-message (D-042): the run's last word -- the final
    summary or reply -- for a script to read, scrubbed like the journal. A
    failure to write it is reported, not fatal: the run itself is done."""
    try:
        path.write_text(default_redactor().scrub(text), encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]could not write {path}: {exc}[/red]")


def cmd_record(args: argparse.Namespace) -> int:
    """Capture a session as a replay fixture for the offline suite."""
    console = Console()
    try:
        wiring = build(Path(args.workspace).resolve(), console=console, **_config_args(args))
    except FoundryError as exc:
        console.print(f"[red]{exc}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]

    recorder = RecordingBackend(inner=wiring.runtime.backend,
                                fixture_path=Path(args.output))
    wiring.runtime.backend = recorder
    wiring.renderer.show_disclosure(str(wiring.workspace.root),
                                    wiring.config.backend.model, wiring.config.mode.value)
    try:
        wiring.runtime.run_turn(args.task)
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "recording ended")
        path = recorder.save()
        console.print(f"[green]recorded[/green] {len(recorder.captured)} turns to {path}")
    return 0


def _stderr_console(args: argparse.Namespace) -> Console:
    """An unattended run's console, on stderr, coloured as ``--color`` says,
    as codex exec's (D-136): ``auto`` only on a terminal (and not with
    NO_COLOR set), ``always`` even into a pipe or a CI log, ``never`` plain
    text."""
    color = getattr(args, "color", "auto")
    if color == "never":
        return Console(stderr=True, color_system=None)
    if color == "always":
        return Console(stderr=True, color_system="standard", no_color=False,
                       legacy_windows=False)
    return Console(stderr=True)
