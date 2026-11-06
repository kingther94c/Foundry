"""CLI entry points: session, resume, undo, login, logout, sessions, doctor.

The interactive prompt and its slash commands are in cli/repl.py.

The CLI owns no agent behaviour. It builds the runtime, subscribes to the event
stream, and answers approval requests -- which is what makes a headless runner or
another frontend a matter of swapping this file.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from foundry import __version__
from foundry.cli.render import Renderer
from foundry.cli.repl import NEW_SESSION
from foundry.cli.repl import confirm as _confirm
from foundry.cli.repl import repl as _repl
from foundry.cli.repl import show_outcome as _show_outcome
from foundry.cli.repl import show_undo_plan as _show_plan
from foundry.cli.repl import with_note as _with_note
from foundry.core.auth import ApiKeySource, CredentialVault, StaticTokenSource
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.recording import RecordingBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.commands import CustomPrompt, load_custom_prompts
from foundry.core.config import ANTHROPIC_PROTOCOL, Config, load_config, user_dir
from foundry.core.context import ContextManager
from foundry.core.errors import (
    AuthError,
    ConfigError,
    FoundryError,
    ResumeError,
    SessionLocked,
    UndoError,
)
from foundry.core.events import (
    EXIT_CODES,
    EventSink,
    Notice,
    PlanUpdated,
    TerminalStatus,
    Termination,
)
from foundry.core.httpc import HttpClient
from foundry.core.policy.engine import Mode, PolicyEngine, builtin_rules
from foundry.core.prompts import environment_paragraph, load_project_doc, system_prompt
from foundry.core.redaction import default_redactor
from foundry.core.resume import (
    CONTINUE_PROMPT,
    JournalState,
    find_journal,
    outside_changes,
    read_journal,
    resolve_session,
    resume_note,
    same_path,
)
from foundry.core.runtime import AgentRuntime, Budget
from foundry.core.session import AuditLog, EventType, SessionStore, is_locked
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.git import capture_baseline, is_git_repository
from foundry.core.tools.registry import default_registry
from foundry.core.undo import apply_undo, other_changes, plan_undo, read_source, undo_payload
from foundry.core.workspace import PathRejected, Workspace

TRUST_FILE = "trusted.json"


@dataclass(slots=True)
class Wiring:
    workspace: Workspace
    config: Config
    runtime: AgentRuntime
    renderer: Renderer
    session: SessionStore
    # Set for a resumed session (D-035).
    resumed: JournalState | None = None
    outside: frozenset[str] = frozenset()
    # Prepended to the first task after a resume, then cleared.
    note: str = ""
    # For /new, and the custom prompts the REPL offers (D-039).
    home: Path | None = None
    trusted: bool = False
    prompts: dict[str, CustomPrompt] = field(default_factory=dict)


def _trusted_paths(home: Path) -> set[str]:
    import json

    path = home / TRUST_FILE
    if not path.is_file():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("trusted", []))
    except (ValueError, OSError):
        return set()


def _remember_trust(home: Path, workspace: Path) -> None:
    import json

    path = home / TRUST_FILE
    trusted = _trusted_paths(home)
    trusted.add(str(workspace))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trusted": sorted(trusted)}, indent=2), encoding="utf-8")


def _confirm_trust(console: Console, home: Path, workspace: Path, *,
                   interactive: bool = True) -> bool:
    """Trust-on-first-use before repository-supplied config or docs are read.

    Without a person to ask -- a headless run -- the answer is no. Prompting
    into a closed stdin would either hang or read EOF as consent.
    """
    if str(workspace) in _trusted_paths(home):
        return True
    has_input = (workspace / ".foundry" / "config.toml").is_file() or any(
        (workspace / name).is_file() for name in ("FOUNDRY.md", "AGENTS.md")
    ) or (workspace / ".foundry" / "prompts").is_dir()  # custom prompts (D-039)
    if not has_input:
        return False
    if not interactive or not sys.stdin or not sys.stdin.isatty():
        console.print(
            f"[dim]{workspace} provides Foundry instructions; not loading them "
            "because this run is unattended. Run interactively once to trust it.[/dim]"
        )
        return False
    console.print(
        f"[yellow]{workspace} provides Foundry instructions or settings.[/yellow]\n"
        "[dim]These are content from the repository, not from you. Load them?[/dim]"
    )
    try:
        answer = console.input("trust this folder? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer.startswith("y"):
        _remember_trust(home, workspace)
        return True
    return False


def _rule_writer(home: Path, policy: PolicyEngine):
    """Append an allow rule to the user's own config when they answer 'always'.

    Written to the user layer, never into the workspace: a rule stored inside
    the repository would be a repo granting itself permissions, which the
    config layering explicitly refuses.
    """

    def persist(op) -> None:
        from foundry.core.policy.engine import Layer, Rule, Verdict

        pattern = op.target.replace("\\", "/")
        config_path = home / "config.toml"
        home.mkdir(parents=True, exist_ok=True)
        existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
        entry = (f'\n[[permissions]]\ntool = "{op.tool}"\n'
                 f'pattern = "{pattern}"\ndecision = "allow"\n'
                 f'reason = "approved with \'always\'"\n')
        if entry.strip() in existing:
            return
        config_path.write_text(existing + entry, encoding="utf-8")
        # Effective immediately, so the same command is not asked again in this
        # session either.
        policy.add_rule(Rule(tool=op.tool, pattern=pattern, verdict=Verdict.ALLOW,
                             layer=Layer.USER, rule_id="user.always",
                             reason="approved with 'always'"))

    return persist


def build(workspace_path: Path, *, home: Path | None = None,
          overrides: dict | None = None, console: Console | None = None,
          interactive: bool = True, resume: str | None = None) -> Wiring:
    """``resume`` names a recorded session to continue: an id, or ``last``."""
    console = console or Console()
    home = home or user_dir()

    try:
        workspace = Workspace(workspace_path)
    except PathRejected as exc:
        raise ConfigError(str(exc)) from exc

    if not is_git_repository(workspace.root):
        raise ConfigError(
            f"{workspace.root} is not inside a Git repository. Foundry records a Git "
            "baseline so it can tell your changes from its own; run 'git init' or "
            "point it at a repository."
        )

    trusted = _confirm_trust(console, home, workspace.root, interactive=interactive)
    config = load_config(workspace.root if trusted else None, overrides=overrides, home=home)

    current = capture_baseline(workspace.root)
    state: JournalState | None = None
    outside: frozenset[str] = frozenset()
    if resume is None:
        baseline = current
        session = SessionStore(home / "sessions", redactor=default_redactor())
        session.write_header(workspace=str(workspace.root), profile=config.backend.name,
                             model=config.backend.model, foundry_version=__version__)
        session.append("git_baseline", baseline.as_payload())
    else:
        session, state, outside = _reopen(home / "sessions", resume, workspace.root, current)
        # The baseline the session started from, not a fresh one: its account of
        # what it changed, and its system prompt, are relative to that.
        baseline = state.baseline
    try:
        return _assemble(workspace, config, trusted, baseline, session, state, outside,
                         home=home, console=console)
    except BaseException:
        session.close()
        raise


def _reopen(sessions: Path, session_id: str, root: Path,
            current) -> tuple[SessionStore, JournalState, frozenset[str]]:
    """Lock the journal, then read it, so nothing is appended in between."""
    journal = resolve_session(sessions, session_id, root)
    session = SessionStore(sessions, session_id=journal.parent.name,
                           redactor=default_redactor(), resume=True)
    try:
        state = read_journal(session.path)
        if not state.workspace or not same_path(state.workspace, root):
            raise ResumeError(f"session {state.session_id} worked in {state.workspace or '?'}, "
                              f"not {root}; resume it with --workspace pointing there")
        return session, state, outside_changes(state, current)
    except BaseException:
        session.close()
        raise


def _assemble(workspace: Workspace, config: Config, trusted: bool, baseline,
              session: SessionStore, state: JournalState | None, outside: frozenset[str], *,
              home: Path, console: Console) -> Wiring:
    policy = PolicyEngine(
        rules=list(builtin_rules()) + list(config.rules),
        mode=config.mode,
        dirty_files=set(baseline.dirty_paths) | set(baseline.untracked_paths) | set(outside),
    )

    registry = default_registry(recorder=session)
    dead = policy.dead_rules(registry.names())
    for rule in dead:
        console.print(f"[yellow]warning:[/yellow] rule for unknown tool {rule.tool!r} "
                      "can never match")

    vault = CredentialVault(home / "auth.json")
    source = _credential_source(config, vault)
    api_key = ""
    missing_credential = ""
    try:
        api_key = source.acquire().reveal()
    except AuthError as exc:
        # Still deferred so `doctor` and `sessions` work without a credential --
        # but the message is kept. Discarding it meant Foundry sent an
        # Authorization-less request, waited out a full round trip, and reported
        # the server "rejecting authentication", which pointed the user at their
        # token instead of at the fact that they had never set one.
        missing_credential = str(exc)

    backend_class = _PROTOCOLS.get(config.backend.protocol)
    if backend_class is None:
        raise ConfigError(
            f"unknown protocol {config.backend.protocol!r}; "
            f"supported: {', '.join(_PROTOCOLS)}"
        )
    backend_kwargs: dict = {}
    if backend_class is AnthropicBackend:
        backend_kwargs["effort"] = config.backend.effort
        backend_kwargs["fallbacks"] = (None if config.backend.fallbacks == "off"
                                       else config.backend.fallbacks)
        if config.backend.max_context_tokens is not None:
            backend_kwargs["caps"] = dataclasses.replace(
                AnthropicBackend().caps,
                max_context_tokens=config.backend.max_context_tokens)
    backend = backend_class(
        base_url=config.backend.base_url, model=config.backend.model, api_key=api_key,
        extra_headers=config.backend.headers, stream=config.backend.stream,
        client=HttpClient(read_timeout=config.backend.stream_idle_timeout_ms / 1000),
        max_retries=config.backend.request_max_retries,
        **backend_kwargs,
    )

    context = ContextManager(
        system_prompt=system_prompt(policy),
        project_doc=load_project_doc(workspace.root, trusted=trusted),
        environment=environment_paragraph(workspace.root, baseline, config.backend.model),
        max_context_tokens=backend.capabilities().max_context_tokens,
    )

    renderer = Renderer(console=console)
    sink = EventSink(redactor=default_redactor())
    sink.subscribe(renderer.handle)

    runtime = AgentRuntime(
        backend=backend, registry=registry, policy=policy, context=context,
        tool_ctx=ToolContext(workspace=workspace, artifacts=session.artifacts,
                             read_tracker=ReadTracker(),
                             max_output_bytes=config.max_output_bytes,
                             max_command_timeout_s=config.command_timeout_s),
        session=session, events=sink, approval=renderer.ask_approval,
        budget=Budget(max_tool_rounds=config.max_tool_rounds,
                      max_tool_calls=config.max_tool_calls),
        model=config.backend.model,
        audit=AuditLog(home / "audit.jsonl", default_redactor()),
        git_baseline=baseline,
        credentials=source,
        persist_rule=_rule_writer(home, policy),
    )
    if missing_credential:
        # Say it before spending a round trip on a request that cannot succeed.
        sink.emit(Notice(missing_credential, level="warning"))
    wiring = Wiring(workspace=workspace, config=config, runtime=runtime,
                    renderer=renderer, session=session, home=home, trusted=trusted,
                    prompts=load_custom_prompts(home, workspace.root, trusted=trusted))
    if state is not None:
        # Evidence starts over (D-035): the command history, the files read and
        # the session grants are this run's own, so nothing from before the
        # resume can vouch for the workspace as it is now.
        context.restore(list(state.history), list(state.turns), state.turn, state.usage)
        if context.context_exhausted():
            raise ResumeError(f"session {state.session_id}'s context is full; start a new session")
        session.append(EventType.RESUMED, {
            "previous_status": state.status.value,
            "previous_reason": state.reason,
            "head": baseline.head,
            "outside_changes": sorted(outside),
            "repaired": [r.as_payload() for r in state.repairs],
            "model": config.backend.model,
            "profile": config.backend.name,
            "foundry_version": __version__,
            "legacy": state.legacy,
        })
        wiring.resumed, wiring.outside = state, outside
        wiring.note = resume_note(state, outside, config.mode.value)
        runtime.plan, runtime.plan_explanation = state.plan, state.plan_explanation
    return wiring


def _announce_resume(wiring: Wiring) -> None:
    """Said through the event stream, so a --json consumer hears it too."""
    state = wiring.resumed
    if state is None:
        return
    events = wiring.runtime.events
    ended = state.status.value + (f" ({state.reason})" if state.reason else "")
    task = state.first_task.splitlines()[0][:80] if state.first_task else "?"
    events.emit(Notice(
        f"resumed session {state.session_id} ({task!r}); it had ended {ended}. "
        f"{len(state.repairs)} tool call(s) without a recorded result were given one; "
        f"{len(wiring.outside)} file(s) changed outside it since it started.", level="info"))
    if (state.profile, state.model) != (wiring.config.backend.name, wiring.config.backend.model):
        events.emit(Notice(
            f"this session ran on {state.profile} ({state.model}); it continues on "
            f"{wiring.config.backend.name} ({wiring.config.backend.model})", level="warning"))
    if state.legacy:
        events.emit(Notice(
            "this session was recorded before resume existed: the output of the calls it "
            "made last was not kept, and a provider may reject its last reply's reasoning",
            level="warning"))
    if state.undone:
        events.emit(Notice(
            f"{len(state.undone)} file(s) were undone after it stopped; the model is told which",
            level="info"))
    if state.plan:
        # No call set it this time: an empty call_id says it was restored.
        events.emit(PlanUpdated("", state.plan, state.plan_explanation))
    mode = wiring.config.mode
    if state.mode and state.mode != mode.value:
        again = "; type /approve to approve its plan again" if mode is Mode.PLAN else ""
        events.emit(Notice(
            f"it had been switched to {state.mode} mode before it stopped; a resume starts "
            f"in {mode.value} mode, as configured{again}", level="info"))


def _close(wiring: Wiring, status: TerminalStatus, reason: str) -> None:
    # A journal write can fail (full volume, a backup agent holding the
    # file); losing the exit code over it would be worse than the lost line.
    try:
        if not wiring.session._terminated:
            wiring.session.record_termination(status, reason)
    except OSError:
        pass
    wiring.session.close()


def _credential_source(config: Config, vault: CredentialVault):
    """The credential this configuration uses. anthropic_messages has its own
    variable and vault field: an OpenAI key must never reach Anthropic, nor the
    reverse."""
    if config.backend.credential_source == "gateway_token":
        return StaticTokenSource(vault)
    if config.backend.protocol == ANTHROPIC_PROTOCOL:
        return ApiKeySource.anthropic(vault)
    return ApiKeySource(vault)


# --- commands -------------------------------------------------------------


def cmd_session(args: argparse.Namespace) -> int:
    """`run`, and `resume`, which is the same session continued from its journal."""
    console = Console()
    try:
        wiring = build(Path(args.workspace).resolve(), console=console,
                       resume=getattr(args, "resume", None))
    except FoundryError as exc:
        console.print(f"[red]{exc}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]

    wiring.renderer.show_disclosure(str(wiring.workspace.root),
                                    wiring.config.backend.model,
                                    wiring.config.mode.value)
    _announce_resume(wiring)
    status = TerminalStatus.CANCELLED
    try:
        if args.task:
            outcome = wiring.runtime.run_turn(_with_note(wiring, args.task))
            # Only the finish gate can produce 'completed'. A turn that simply
            # ended is unfinished work, not success.
            status = outcome.status or TerminalStatus.PARTIAL
        else:
            while (ended := _repl(wiring, console)) == NEW_SESSION:
                # /new: this session ends, and a fresh one starts in the same
                # workspace -- a new journal, an empty conversation, no grants.
                _close(wiring, TerminalStatus.CANCELLED, "the user started a new session")
                try:
                    wiring = build(wiring.workspace.root, home=wiring.home, console=console)
                except FoundryError as exc:
                    console.print(f"[red]{exc}[/red]")
                    return EXIT_CODES[TerminalStatus.FAILED]
                console.print(f"[dim]new session {wiring.session.session_id}[/dim]")
            status = ended
    except KeyboardInterrupt:
        wiring.runtime.cancel()
        console.print("\n[yellow]cancelled[/yellow]")
    finally:
        _close(wiring, status, "session ended")

    return EXIT_CODES.get(status, 0)


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


def cmd_login(args: argparse.Namespace) -> int:
    console = Console()
    home = user_dir()
    vault = CredentialVault(home / "auth.json")

    # Which credential this machine actually uses. Storing an api_key while the
    # configured source reads a token left the user with a "saved" message and
    # an AuthError on the next run, and the error told them to run this very
    # command again.
    config = load_config(home=home)
    source = _credential_source(config, vault)
    gateway = isinstance(source, StaticTokenSource)
    if gateway:
        label = "gateway token"
    elif source.vault_field == "anthropic_api_key":
        label = "Anthropic API key"
    else:
        label = "OpenAI API key"

    console.print(
        f"Paste a{'n' if label[0] in 'AEIOU' else ''} {label}. Foundry stores it "
        "encrypted for your Windows account (DPAPI) and never puts it in prompts or logs."
    )

    # getpass.win_getpass reads the console directly through msvcrt and ignores
    # stdin entirely, so the EOFError handler below could never fire and a
    # piped or detached `foundry login` hung forever instead of failing.
    if not sys.stdin.isatty():
        env_var = source.env_var
        console.print(
            f"[red]stdin is not a terminal[/red], so there is nowhere safe to type a "
            f"secret. Set {env_var} in the environment instead."
        )
        return 1

    try:
        import getpass

        secret = getpass.getpass(f"{label.lower()}: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]cancelled[/yellow]")
        return 1
    except OSError as exc:                       # no console attached at all
        console.print(f"[red]cannot read from the console:[/red] {exc}")
        return 1
    if not secret:
        console.print("[red]nothing entered[/red]")
        return 1

    source.store(secret)
    console.print(f"[green]saved[/green] {home / 'auth.json'} ({label})")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    vault = CredentialVault(user_dir() / "auth.json")
    vault.clear()
    Console().print("[green]credentials removed[/green]")
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    console = Console()
    root = user_dir() / "sessions"
    if not root.is_dir():
        console.print("[dim]no sessions yet[/dim]")
        return 0

    entries = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    if args.session_id:
        # A session id is a name, not a path. `foundry sessions ../notes` used to
        # read whatever it landed on and print a traceback when the lines were
        # not journal records.
        target = find_journal(root, args.session_id)
        if target is None:
            console.print(f"[red]no such session: {args.session_id}[/red]")
            return 1
        for record in SessionStore.read_records(target):
            console.print(f"[dim]{record.ordinal:>4}[/dim] {record.type}")
        return 0

    for entry in entries[:args.limit]:
        journal = entry / "events.jsonl"
        if not journal.is_file():
            continue
        status = SessionStore.terminal_status(journal)
        style = "green" if status is TerminalStatus.COMPLETED else "yellow"
        busy = "  [cyan]in use[/cyan]" if is_locked(entry) else ""
        console.print(f"{entry.name}  [{style}]{status.value}[/{style}]{busy}")
    return 0


# One table, so `doctor` validates exactly what `build` accepts.
_PROTOCOLS = {
    "openai_compat": OpenAICompatBackend,
    "responses": ResponsesBackend,
    ANTHROPIC_PROTOCOL: AnthropicBackend,
}


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


def _run_unattended(runtime: AgentRuntime, task: str):
    """One task; a reply in prose gets a single reminder to call finish.

    Only finish reports an outcome, and nobody reads a reply in an unattended
    run. Running on its own repository, Foundry finished the work, described
    it in prose -- with the event ids -- and exited partial. Asked once, not
    in a loop: a model that cannot call tools must still end.
    """
    outcome = runtime.run_turn(task)
    if outcome.status is None:
        outcome = runtime.run_turn(FINISH_REMINDER)
    return outcome


def cmd_exec(args: argparse.Namespace) -> int:
    """Headless: one task, no prompts, ASK resolves to DENY.

    Fail-closed is the whole point. An unattended run that silently approved
    whatever it was asked would be strictly worse than one that stops.
    """
    console = Console(stderr=True)
    if args.task is None and args.resume is None:
        console.print("[red]exec needs a task, or --resume SESSION to continue one[/red]")
        return 2
    try:
        if args.resume is not None and args.task is None:
            # Continuing a finished session needs to be told what to do next;
            # "continue where it stopped" would only finish it again.
            journal = resolve_session(user_dir() / "sessions", args.resume,
                                      Path(args.workspace).resolve())
            if SessionStore.terminal_status(journal) is TerminalStatus.COMPLETED:
                console.print("[red]that session completed; give the task to continue "
                              "it with[/red]")
                return 2
        wiring = build(Path(args.workspace).resolve(),
                       overrides={"mode": Mode.DONT_ASK}, console=console,
                       interactive=False, resume=args.resume)
    except FoundryError as exc:
        console.print(f"[red]{exc}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]

    wiring.runtime.approval = None  # nobody to ask
    wiring.runtime.policy.interactive = False

    if args.json:
        # Serialize whatever the event actually carries. A hand-maintained
        # attribute list silently dropped every field nobody remembered to add:
        # token_count arrived as `{"kind": "token_count"}` with no numbers, and
        # tool_begin/tool_end had no call_id, so a consumer could not pair them.
        def as_json(event) -> None:
            print(json.dumps(_event_payload(event), ensure_ascii=False), flush=True)

        wiring.runtime.events.subscribe(as_json)
    _announce_resume(wiring)

    status = TerminalStatus.FAILED
    reason = "headless run ended"
    outcome = None
    try:
        outcome = _run_unattended(wiring.runtime,
                                  _with_note(wiring, args.task or CONTINUE_PROMPT))
        # Only the finish gate can produce 'completed'. A turn that simply
        # ended is unfinished work, not success.
        status = outcome.status or TerminalStatus.PARTIAL
        if outcome.status is None:
            # Saying "headless run ended" told the operator nothing about why a
            # correct-looking answer exited 10. Against a model that cannot call
            # tools at all, this is the *only* reachable outcome.
            reason = ("the model ended its turn without calling finish, even when "
                      "reminded; only finish reports completion, with the evidence for it")
        if not args.json and outcome.text:
            print(outcome.text)
    except KeyboardInterrupt:
        wiring.runtime.cancel()
        status = TerminalStatus.CANCELLED
        reason = "interrupted"
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


def cmd_record(args: argparse.Namespace) -> int:
    """Capture a session as a replay fixture for the offline suite."""
    console = Console()
    try:
        wiring = build(Path(args.workspace).resolve(), console=console)
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


def cmd_report(args: argparse.Namespace) -> int:
    from foundry.cli.report import render

    root = user_dir() / "sessions"
    if not root.is_dir():
        Console().print("[dim]no sessions recorded yet[/dim]")
        return 0
    print(render(root, as_json=args.json))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    console = Console()
    ok = True

    console.print(f"foundry {__version__}")
    console.print(f"python  {sys.version.split()[0]}")
    # Every checkout reports the same version; where the code was imported from
    # is what tells this clone from an editable install of another worktree.
    import foundry

    # soft_wrap: a long path stays on one line, so it can be copied.
    console.print(f"source  {Path(foundry.__file__).resolve().parent}", soft_wrap=True)

    if sys.version_info[:2] != (3, 12):
        console.print("[yellow]warning:[/yellow] Foundry targets Python 3.12")

    from foundry.core.tools.git import run_git

    try:
        code, out, _ = run_git(["--version"], Path.cwd())
        console.print(f"git     {out.strip() if code == 0 else 'not working'}")
    except FoundryError:
        console.print("[red]git is not on PATH[/red]")
        ok = False

    if sys.platform == "win32":
        import subprocess

        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
                capture_output=True, timeout=20,
            )
            console.print(f"powershell {result.stdout.decode('utf-8', 'replace').strip()}")
        except (OSError, subprocess.SubprocessError):
            console.print("[red]powershell.exe not available[/red]")
            ok = False

        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
                enabled, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            if not enabled:
                console.print("[yellow]long paths are disabled[/yellow] "
                              "(deep node_modules trees may fail)")
        except OSError:
            pass

    home = user_dir()
    console.print(f"home    {home}")
    console.print(f"auth    {'present' if (home / 'auth.json').is_file() else 'not signed in'}")

    # The things build() hard-fails on. Reporting green while every session dies
    # immediately is worse than not having the command. There are three, not two:
    # an unrecognised protocol also stops build() dead, and doctor used to print
    # a healthy-looking config line for one.
    try:
        config = load_config(home=home)
        console.print(f"config  {config.backend.model} via {config.backend.base_url} "
                      f"(mode {config.mode.value}, {len(config.rules)} permission rules)")
        if config.backend.protocol not in _PROTOCOLS:
            console.print(f"[red]protocol  {config.backend.protocol!r} is not one of "
                          f"{', '.join(sorted(_PROTOCOLS))}; every session would "
                          "fail to start[/red]")
            ok = False
        else:
            console.print(f"protocol  {config.backend.protocol}")
    except FoundryError as exc:
        console.print(f"[red]config  {exc}[/red]")
        ok = False

    from foundry.core.tools.git import is_git_repository

    cwd = Path.cwd()
    if is_git_repository(cwd):
        console.print(f"repo    {cwd}")
    else:
        console.print(f"[yellow]repo    {cwd} is not a Git repository; "
                      "a session here would refuse to start[/yellow]")

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    console.print(f"proxy   {proxy or 'none'}")
    ca = os.environ.get("FOUNDRY_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    console.print(f"ca      {ca or 'Windows system store'}")

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foundry",
                                     description="A local coding-agent runtime.")
    parser.add_argument("--version", action="version", version=f"foundry {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start a session (default)")
    run.add_argument("task", nargs="?", help="run one task then exit")
    run.add_argument("--workspace", default=".", help="repository to work in")
    run.set_defaults(func=cmd_session)

    resume = sub.add_parser("resume", help="continue a recorded session")
    resume.add_argument("resume", nargs="?", default="last", metavar="session",
                        help="the session id (default: last, the newest in this workspace)")
    resume.add_argument("task", nargs="?", help="run one task then exit")
    resume.add_argument("--workspace", default=".", help="repository the session worked in")
    resume.set_defaults(func=cmd_session)

    undo = sub.add_parser("undo", help="revert the files a recorded session patched")
    undo.add_argument("session", nargs="?", default="last",
                      help="the session id (default: last, the newest in this workspace)")
    undo.add_argument("--last", action="store_true",
                      help="only the session's newest patch")
    undo.add_argument("--yes", action="store_true", help="do not ask first")
    undo.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    undo.add_argument("--workspace", default=".", help="repository the session worked in")
    undo.set_defaults(func=cmd_undo)

    execute = sub.add_parser("exec", help="run one task headlessly (approvals are denied)")
    execute.add_argument("task", nargs="?")
    execute.add_argument("--workspace", default=".")
    execute.add_argument("--json", action="store_true", help="emit the event stream as JSONL")
    execute.add_argument("--resume", metavar="SESSION",
                         help="continue a recorded session (an id, or last); the task "
                              "defaults to carrying on where it stopped")
    execute.set_defaults(func=cmd_exec)

    record = sub.add_parser("record", help="capture a session as a replay fixture")
    record.add_argument("task")
    record.add_argument("--output", required=True, help="fixture file to write")
    record.add_argument("--workspace", default=".")
    record.set_defaults(func=cmd_record)

    login = sub.add_parser("login", help="store an API key")
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="remove stored credentials")
    logout.set_defaults(func=cmd_logout)

    sessions = sub.add_parser("sessions", help="list or show recorded sessions")
    sessions.add_argument("session_id", nargs="?")
    sessions.add_argument("--limit", type=int, default=20)
    sessions.set_defaults(func=cmd_sessions)

    report = sub.add_parser("report", help="summarize recorded sessions")
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    doctor = sub.add_parser("doctor", help="check the environment")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args((argv or []) + ["run"])
    try:
        return args.func(args)
    except FoundryError as exc:
        Console().print(f"[red]{exc}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]
