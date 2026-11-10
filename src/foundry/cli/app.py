"""CLI entry points: session, resume, undo, login, logout, sessions, doctor.

The interactive prompt and its slash commands are in cli/repl.py.

The CLI owns no agent behaviour. It builds the runtime, subscribes to the event
stream, and answers approval requests -- which is what makes a headless runner or
another frontend a matter of swapping this file.
"""

from __future__ import annotations

import argparse
import shutil
import dataclasses
import json
import os
import re
import subprocess
import sys
import tempfile
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

import foundry
from foundry import __version__
from foundry.cli.codex_flags import hints as codex_hints
from foundry.cli.completion import SHELLS
from foundry.cli.completion import script as completion_script
from foundry.cli.render import Renderer, safe
from foundry.cli.transcript import show_conversation
from foundry.cli.agents import Spawner
from foundry.cli.repl import ARCHIVE_SESSION, DELETE_SESSION, FORK_SESSION, NEW_SESSION
from foundry.cli.repl import RESUME_SESSION
from foundry.cli.repl import REVIEW_READ_ONLY, review_task
from foundry.cli.repl import attach as _attach
from foundry.cli.repl import take_images as _take_images
from foundry.cli.repl import confirm as _confirm
from foundry.cli.repl import repl as _repl
from foundry.cli.repl import show_outcome as _show_outcome
from foundry.cli.repl import show_undo_plan as _show_plan
from foundry.cli.repl import with_note as _with_note
from foundry.core.auth import ApiKeySource, CredentialVault, EnvKeySource, StaticTokenSource
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.recording import RecordingBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.commands import CustomPrompt, load_custom_prompts
from foundry.core.config import (
    ANTHROPIC_PROTOCOL,
    Config,
    load_config,
    parse_settings,
    user_dir,
)
from foundry.core.config_edit import add_server, remove_server, set_feature, toml_string
from foundry.core.context import ContextManager
from foundry.core.features import ALIASES as FEATURE_ALIASES
from foundry.core.features import FEATURES, refusal
from foundry.core.features import state as feature_state
from foundry.core.mcp import McpServers, start_servers
from foundry.core.memories import SESSIONS_DIR as MEMORY_SESSIONS
from foundry.core.memories import MemoryTool, forget, load_state, run_pass
from foundry.core.memories import folder as memories_folder
from foundry.core.memories import instructions as memory_instructions
from foundry.core.memories import notes as memory_notes
from foundry.core.memories import summary as memory_summary
from foundry.core.errors import (
    AuthError,
    ConfigError,
    FoundryError,
    ResumeError,
    SessionLocked,
    ToolError,
    UndoError,
)
from foundry.core.goal import ACTIVE as GOAL_ACTIVE
from foundry.core.goal import HINTS as GOAL_HINTS
from foundry.core.goal import PAUSED as GOAL_PAUSED
from foundry.core.goal import usage_line as goal_usage
from foundry.core.events import (
    EXIT_CODES,
    EventSink,
    Notice,
    PlanUpdated,
    SessionStarted,
    TerminalStatus,
    Termination,
)
from foundry.core.httpc import HttpClient
from foundry.core.json_schema import check_schema
from foundry.core.hooks import HookSet
from foundry.core.notify import Notifier
from foundry.core.policy.engine import Mode, PolicyEngine, Verdict, builtin_rules
from foundry.core.prompts import (
    environment_paragraph,
    load_instructions,
    repository_docs,
    system_prompt,
)
from foundry.core.redaction import default_redactor
from foundry.core.resume import (
    ARCHIVED,
    CONTINUE_PROMPT,
    JournalState,
    find_journal,
    fork_journal,
    latest_session,
    named_session,
    outside_changes,
    read_journal,
    recent_sessions,
    recorded_workspace,
    resolve_session,
    resume_note,
    same_path,
    session_named,
    summarize,
)
from foundry.core.resume import delete_session as _delete_session
from foundry.core.resume import move_session as _move_session
from foundry.core.runtime import AgentRuntime, Budget
from foundry.core.session import AuditLog, EventType, SessionStore
from foundry.core.skills import (
    Skills,
    list_budget,
    load_skills,
    register as register_skills,
    repository_skill_folders,
    skill_list,
)
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.command import RunCommand
from foundry.core.tools.git import capture_baseline, is_git_repository
from foundry.core.tools.agent import SpawnAgent
from foundry.core.tools.registry import default_registry
from foundry.core.undo import apply_undo, other_changes, plan_undo, read_source, undo_payload
from foundry.core.winapi import (
    IS_WINDOWS,
    decode_output,
    looks_like_credential,
    register_included_secrets,
    withhold_variables,
)
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
    # The name /rename gave the session (D-125), "" when none.
    name: str = ""
    # The fork /fork made of it, for the loop to go on in (D-126).
    forked: str = ""
    # The session /resume named, "" to pick one (D-134).
    resuming: str = ""
    # exec --ephemeral (D-132): the folder its journal is in, removed as it
    # closes; None for a session that is kept.
    ephemeral: Path | None = None
    # For /new, and the custom prompts the REPL offers (D-039).
    home: Path | None = None
    trusted: bool = False
    prompts: dict[str, CustomPrompt] = field(default_factory=dict)
    # Where the system prompt's instructions came from (D-041), for /status.
    instructions: tuple[str, ...] = ()
    # The MCP servers this session started (D-043); closed with it.
    mcp: McpServers | None = None
    # The skills found (D-113), for /skills and $name.
    skills: Skills = field(default_factory=Skills)
    # Images for the next message: -i, then /image (D-045).
    images: list = field(default_factory=list)
    # What the user chose with /mode and /model (D-047): /new keeps it.
    chosen: dict = field(default_factory=dict)
    # The workspace's memories (D-145): their folder where the feature is on
    # and the workspace trusted, else None; and whether this session's
    # instructions carry their summary.
    memory_folder: Path | None = None
    memories_used: bool = False


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
                   interactive: bool = True, fallbacks: tuple[str, ...] = (),
                   read_docs: bool = True) -> bool:
    """Trust-on-first-use before repository-supplied config or docs are read.

    Without a person to ask -- a headless run -- the answer is no. Prompting
    into a closed stdin would either hang or read EOF as consent.
    ``fallbacks`` and ``read_docs`` are the user's project_doc settings
    (D-086): a file read only by a fallback name is asked about too, and none
    is when the user reads none.
    """
    if str(workspace) in _trusted_paths(home):
        return True
    has_input = (workspace / ".foundry" / "config.toml").is_file() or (read_docs and bool(
        repository_docs(workspace, fallbacks)  # from the repository's top down (D-041)
    )) or (workspace / ".foundry" / "prompts").is_dir() or any(  # custom prompts (D-039)
        folder.is_dir() for folder in repository_skill_folders(workspace))  # skills (D-113)
    if not has_input:
        return False
    if not interactive or not sys.stdin or not sys.stdin.isatty():
        console.print(
            f"[dim]{escape(str(workspace))} provides Foundry instructions; not loading them "
            "because this run is unattended. Run Foundry interactively once, or exec "
            "with --trust, to trust it.[/dim]",
            soft_wrap=True,
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


def _literal(text: str) -> str:
    """``text`` as a pattern that matches only itself: fnmatch's wildcards
    and sets each put in a set of their own."""
    return re.sub(r"([*?\[])", r"[\1]", text)


def _rule_writer(home: Path, policy: PolicyEngine):
    """Append an allow rule to the user's own config when they answer 'always'.

    Written to the user layer, never into the workspace: a rule stored inside
    the repository would be a repo granting itself permissions, which the
    config layering explicitly refuses.
    """

    def persist(op) -> None:
        from foundry.core.policy.engine import Layer, Rule, Verdict

        # The call approved, exactly (D-151): written as it was, a '*' or '?'
        # in what the model sent became a wildcard, so 'always' for
        # {"query": "*"} allowed every later call with any arguments.
        pattern = _literal(op.target.replace("\\", "/"))
        config_path = home / "config.toml"
        home.mkdir(parents=True, exist_ok=True)
        existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
        # Quoted as TOML: a command with a quote in it, written as it was,
        # left a config.toml that no longer parsed (found with D-082).
        entry = (f'\n[[permissions]]\ntool = {toml_string(op.tool)}\n'
                 f'pattern = {toml_string(pattern)}\ndecision = "allow"\n'
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


def _title_facts(wiring: Wiring) -> dict:
    """What the terminal title's items show (D-139), read each time it is
    written: /rename and /model change them."""
    root = wiring.workspace.root
    return {"app-name": "Foundry", "project-name": root.name, "current-dir": str(root),
            "thread-name": wiring.name, "thread-id": wiring.session.session_id,
            "model": wiring.runtime.model}


def build(workspace_path: Path, *, home: Path | None = None,
          overrides: dict | None = None, console: Console | None = None,
          interactive: bool = True, resume: str | None = None,
          profile: str | None = None, settings: dict | None = None,
          client_settings: dict | None = None,
          ephemeral: bool = False, trust: bool = False, review: bool = False) -> Wiring:
    """``resume`` names a recorded session to continue: an id, or ``last``.
    ``profile`` and ``settings`` come from ``--profile`` and ``-c`` (D-042);
    ``client_settings`` from a foundry mcp-server client, and only tighten
    (D-150).
    ``ephemeral``: the journal goes in a folder of its own, outside the
    sessions, and is removed as the session closes (D-132). ``trust``: the
    user trusts the workspace by name, as codex's ``--trust`` (D-137).
    ``review``: the run is foundry review's, on backend.review_model unless
    -m names a model (D-143)."""
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

    if trust and str(workspace.root) not in _trusted_paths(home):
        # Asked for on the command line (D-137): kept for later sessions too,
        # as a yes at the trust prompt is, and kept if this run is not.
        _remember_trust(home, workspace.root)
        console.print(f"[dim]trusting {escape(str(workspace.root))} from now on: its "
                      "instructions and settings are read[/dim]", soft_wrap=True)

    # The user's own settings first: which files are instructions, and so
    # whether there is anything to ask about, is theirs to say (D-086).
    config = load_config(None, overrides=overrides, home=home, profile=profile,
                         settings=settings, client_settings=client_settings)
    trusted = _confirm_trust(console, home, workspace.root, interactive=interactive,
                             fallbacks=config.project_doc_fallback_filenames,
                             read_docs=config.project_doc_max_bytes > 0)
    if trusted:
        config = load_config(workspace.root, overrides=overrides, home=home,
                             profile=profile, settings=settings,
                             client_settings=client_settings)
    if review and config.backend.review_model and "model" not in (overrides or {}):
        # codex's review_model (D-143): the review's whole run is on it.
        config.backend.model = config.backend.review_model
    _guard_credentials(config)

    current = capture_baseline(workspace.root)
    state: JournalState | None = None
    outside: frozenset[str] = frozenset()
    discard: Path | None = None
    if resume is None:
        baseline = current
        if ephemeral:
            discard = Path(tempfile.mkdtemp(prefix="foundry-ephemeral-"))
        session = SessionStore(discard or home / "sessions", redactor=default_redactor())
        session.write_header(workspace=str(workspace.root), profile=config.backend.name,
                             model=config.backend.model, foundry_version=__version__)
        session.append("git_baseline", baseline.as_payload())
        if not config.memory_settings.generate:
            # codex's generate_memories = false (D-145): a session started now
            # is never read for memories.
            session.append(EventType.MEMORY_MODE, {"mode": "disabled",
                                                   "by": "generate_memories"})
    else:
        session, state, outside = _reopen(home / "sessions", resume, workspace.root, current)
        # The baseline the session started from, not a fresh one: its account of
        # what it changed, and its system prompt, are relative to that.
        baseline = state.baseline
    try:
        wiring = _assemble(workspace, config, trusted, baseline, session, state, outside,
                           home=home, console=console)
    except BaseException:
        session.close()
        _discard(discard)
        raise
    wiring.ephemeral = discard
    # The terminal's title is an interactive session's (D-139).
    wiring.renderer.title_items = config.terminal_title if interactive else ()
    return wiring


def _guard_credentials(config: Config) -> None:
    """Before anything starts a child (D-153): the variables this
    configuration reads a credential from are kept from every command, hook
    and server whatever they are called, and the credentials the user passes
    to commands by name are scrubbed from the first output that shows one."""
    names = [config.backend.env_key or "", *config.backend.env_http_headers.values()]
    for server in config.mcp_servers.values():
        names.append(server.bearer_token_env_var)
        names.extend(variable for _, variable in server.env_http_headers)
    withhold_variables(names)
    register_included_secrets(config.shell_environment)


def _discard(folder: Path | None) -> None:
    """An ephemeral session's journal folder, removed (D-132). A file still
    held open -- a hook's process outliving the run -- is left for the
    system's temporary-file cleanup rather than failing the run's exit."""
    if folder is not None:
        shutil.rmtree(folder, ignore_errors=True)


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


def make_backend(config: Config, api_key: str):
    """The model backend a configuration names: a session's, and the memory
    pass's (D-145)."""
    backend_class = _PROTOCOLS.get(config.backend.protocol)
    if backend_class is None:
        raise ConfigError(
            f"unknown protocol {config.backend.protocol!r}; "
            f"supported: {', '.join(_PROTOCOLS)}"
        )
    # Every protocol takes an effort (D-061); load_config checked it is one
    # this protocol knows.
    backend_kwargs: dict = {"effort": config.backend.effort}
    if backend_class is not AnthropicBackend:
        # GPT-5's answer length (D-090); load_config refused it for Claude.
        backend_kwargs["verbosity"] = config.backend.verbosity
    if backend_class is AnthropicBackend:
        backend_kwargs["fallbacks"] = (None if config.backend.fallbacks == "off"
                                       else config.backend.fallbacks)
        if config.backend.reasoning_summary == "none":
            backend_kwargs["thinking_display"] = None
    elif (backend_class is ResponsesBackend
          and config.backend.reasoning_summary not in (None, "none")):
        backend_kwargs["reasoning_summary"] = config.backend.reasoning_summary
    backend = backend_class(
        base_url=config.backend.base_url, model=config.backend.model, api_key=api_key,
        extra_headers=request_headers(config.backend), stream=config.backend.stream,
        query_params=dict(config.backend.query_params),
        client=HttpClient(read_timeout=config.backend.stream_idle_timeout_ms / 1000),
        max_retries=config.backend.request_max_retries,
        **backend_kwargs,
    )
    if config.backend.max_context_tokens is not None:
        # Every protocol's window (D-095), as codex's model_context_window:
        # Claude's alone was settable, and the OpenAI shapes planned masking
        # and compaction against 128,000 tokens whatever the model held.
        backend.caps = dataclasses.replace(
            backend.caps, max_context_tokens=config.backend.max_context_tokens)
    return backend

def _assemble(workspace: Workspace, config: Config, trusted: bool, baseline,
              session: SessionStore, state: JournalState | None, outside: frozenset[str], *,
              home: Path, console: Console) -> Wiring:
    # view_image only where the backend takes images (D-045) and codex's
    # view_image feature is on (D-144).
    registry = default_registry(recorder=session,
                                images=config.backend.images and config.view_image)
    if config.subagents:
        # Its spawner is given the session once there is one (D-141).
        registry.register(SpawnAgent(Spawner()))
    skills = load_skills(home, workspace.root, trusted=trusted,
                         switches=config.all_skill_switches())
    register_skills(skills, registry)
    # Started before the rules are checked, so a rule for a server's tool is
    # not reported as one that can never match (D-043).
    mcp = (start_servers(config.mcp_servers, workspace.root, registry)
           if config.mcp_servers else McpServers())
    try:
        return _assemble_with(workspace, config, trusted, baseline, session, state, outside,
                              home=home, console=console, registry=registry, mcp=mcp,
                              skills=skills)
    except BaseException:
        mcp.close()
        raise


def _assemble_with(workspace: Workspace, config: Config, trusted: bool, baseline,
                   session: SessionStore, state: JournalState | None, outside: frozenset[str],
                   *, home: Path, console: Console, registry, mcp: McpServers,
                   skills: Skills) -> Wiring:
    # codex's memories (D-145): kept and given only in a workspace the user
    # trusts, and only the workspace's own; read in full with read_memories.
    memory_folder = memories_folder(home, workspace.root) if config.memories and trusted else None
    remembered = (memory_summary(memory_folder)
                  if memory_folder is not None and config.memory_settings.use else "")
    if remembered:
        registry.register(MemoryTool(memory_folder))
    policy = PolicyEngine(
        rules=list(builtin_rules()) + list(config.rules),
        mode=config.mode,
        dirty_files=set(baseline.dirty_paths) | set(baseline.untracked_paths) | set(outside),
    )

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

    backend = make_backend(config, api_key)
    window = backend.capabilities().max_context_tokens
    if (config.backend.max_output_tokens is not None
            and config.backend.max_output_tokens > window // 2):
        raise ConfigError(
            f"backend.max_output_tokens ({config.backend.max_output_tokens:,}) is more than "
            f"half the model's {window:,}-token window, leaving the conversation too little "
            "room; lower it, or set backend.max_context_tokens if the window is larger")

    instructions = load_instructions(workspace.root, home, trusted=trusted,
                                     max_chars=config.project_doc_max_bytes,
                                     fallbacks=config.project_doc_fallback_filenames)
    context = ContextManager(
        system_prompt=system_prompt(policy),
        project_doc=instructions.text,
        developer_instructions=config.developer_instructions,
        environment=environment_paragraph(workspace.root, baseline, config.backend.model),
        # codex's skills.max_context_tokens (D-114) at four characters a
        # token, or its share of the window.
        skills=skill_list(skills, config.skills_max_context_tokens * 4
                          if config.skills_max_context_tokens else list_budget(window)),
        memories=memory_instructions(remembered),
        max_context_tokens=backend.capabilities().max_context_tokens,
        compact_above_tokens=config.auto_compact_token_limit,
    )
    if config.backend.max_output_tokens is not None:
        # The room kept for the answer is at least what it may be (D-096):
        # less, and a full context plus a full answer overran the window.
        context.reserve_output_tokens = max(context.reserve_output_tokens,
                                            config.backend.max_output_tokens)

    renderer = Renderer(console=console, file_opener=config.file_opener,
                        workspace=workspace.root)
    sink = EventSink(redactor=default_redactor())
    sink.subscribe(renderer.handle)

    runtime = AgentRuntime(
        backend=backend, registry=registry, policy=policy, context=context,
        tool_ctx=ToolContext(workspace=workspace, artifacts=session.artifacts,
                             read_tracker=ReadTracker(),
                             max_output_bytes=config.max_output_bytes,
                             max_command_timeout_s=config.command_timeout_s,
                             images=config.backend.images,
                             env_policy=config.shell_environment),
        session=session, events=sink, approval=renderer.ask_approval,
        budget=Budget(max_tool_rounds=config.max_tool_rounds,
                      max_tool_calls=config.max_tool_calls),
        model=config.backend.model,
        max_output_tokens=config.backend.max_output_tokens,
        stream_max_retries=config.backend.stream_max_retries,
        audit=AuditLog(home / "audit.jsonl", default_redactor()),
        git_baseline=baseline,
        credentials=source,
        persist_rule=_rule_writer(home, policy),
        auto_compact=config.auto_compact,
        compact_prompt=config.compact_prompt or config.compact_prompt_file,
        show_reasoning=config.show_reasoning,
        notifier=Notifier(tuple(config.notify), workspace.root) if config.notify else None,
        scrub=default_redactor().scrub,
        hooks=(HookSet(config.active_hooks(), cwd=workspace.root,
                       env_policy=config.shell_environment,
                       mcp={client.server.name: client for client in mcp.clients})
               if config.active_hooks() else None),
        start_sources=["resume" if state is not None else "startup"],
    )
    # A cancel from another thread (foundry mcp-server, D-103) reaches a
    # running command, search or MCP call, which poll this, not only the next
    # step.
    runtime.tool_ctx.cancelled = lambda: runtime._cancelled
    if missing_credential:
        # Say it before spending a round trip on a request that cannot succeed.
        sink.emit(Notice(missing_credential, level="warning"))
    wiring = Wiring(workspace=workspace, config=config, runtime=runtime,
                    renderer=renderer, session=session, home=home, trusted=trusted,
                    prompts=load_custom_prompts(home, workspace.root, trusted=trusted),
                    instructions=(("developer_instructions",) if config.developer_instructions
                                  else ()) + tuple(instructions.sources),
                    mcp=mcp, skills=skills, memory_folder=memory_folder,
                    memories_used=bool(remembered))
    renderer.title_facts = lambda: _title_facts(wiring)
    if "spawn_agent" in registry.names():
        registry.get("spawn_agent").spawner.wiring = wiring
    _announce_mcp(mcp, session, sink)
    _announce_skills(skills, sink)
    if state is not None:
        # Evidence starts over (D-035): the command history, the files read and
        # the session grants are this run's own, so nothing from before the
        # resume can vouch for the workspace as it is now.
        context.restore(list(state.history), list(state.turns), state.turn, state.usage,
                        sent=state.last_sent)
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
        wiring.resumed, wiring.outside, wiring.name = state, outside, state.name
        wiring.note = resume_note(state, outside, config.mode.value)
        runtime.plan, runtime.plan_explanation = state.plan, state.plan_explanation
        if state.goal is not None and config.goals:
            # With features.goals off (D-144) the goal stays in the journal,
            # for a session that has goals on to go on with.
            # Not pursued on its own once resumed: /goal resume goes on (D-127).
            runtime.restore_goal(state.goal if state.goal.status != GOAL_ACTIVE
                                 else dataclasses.replace(state.goal, status=GOAL_PAUSED))
    return wiring


def _announce_skills(skills: Skills, sink: EventSink) -> None:
    """What about the skills is not as their folders would have it (D-113):
    one that is not read, one another of its name hides, those the model's
    list had no room for."""
    for folder, why in skills.skipped:
        sink.emit(Notice(f"skill folder {folder} is not used: {why}", level="warning"))
    for skill in skills.hidden:
        sink.emit(Notice(f"skill {skill.name} in {skill.folder} is hidden by another of "
                         "that name", level="warning"))
    if skills.left_out:
        sink.emit(Notice(f"{len(skills.left_out)} skill(s) left out of the model's list for "
                         "room; it can still load them by name, and /skills lists them",
                         level="warning"))


def _announce_mcp(mcp: McpServers, session: SessionStore, sink: EventSink) -> None:
    """Which servers came up with which tools, in the journal and on screen:
    the tools a session was offered are part of its record."""
    for name, tools in mcp.tools.items():
        record = {"mcp_server": name, "tools": tools}
        said = f"MCP server {name}: {len(tools)} tool(s)"
        if name in mcp.resources:
            # Its resources, listed and read by URI (D-102).
            record["resources"] = True
            said += " and resources"
        left_out = mcp.left_out.get(name, [])
        if left_out:
            # The user's own choice (D-080), recorded with what was offered.
            record["left_out"] = left_out
            said += f", {len(left_out)} left out by your config"
        session.append(EventType.CAPABILITY_PROBE, record)
        sink.emit(Notice(said, level="info"))
    for name, why in mcp.failures.items():
        session.append(EventType.CAPABILITY_PROBE, {"mcp_server": name, "tools": [],
                                                    "error": why})
        sink.emit(Notice(f"MCP server {name} is not available: {why}", level="warning"))
    for skipped in mcp.skipped:
        sink.emit(Notice(f"MCP tool not offered: {skipped}", level="warning"))


def _announce_resume(wiring: Wiring) -> None:
    """Said through the event stream, so a --json consumer hears it too."""
    state = wiring.resumed
    if state is None:
        return
    events = wiring.runtime.events
    ended = state.status.value + (f" ({state.reason})" if state.reason else "")
    task = state.first_task.splitlines()[0][:80] if state.first_task else "?"
    events.emit(Notice(
        (f"forked session {state.fork_of} into {state.session_id} ({task!r}); it goes on "
         "apart from it. " if state.fork_of else
         f"resumed session {state.session_id} ({task!r}); it had ended {ended}. ") +
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
    goal = wiring.runtime.goal
    if goal is not None:
        hint = f"; {GOAL_HINTS[goal.status]}" if goal.status in GOAL_HINTS else ""
        events.emit(Notice(f"its goal is {goal.status} ({goal_usage(goal)}){hint}",
                           level="info"))
    mode = wiring.config.mode
    if state.mode and state.mode != mode.value:
        again = "; type /approve to approve its plan again" if mode is Mode.PLAN else ""
        events.emit(Notice(
            f"it had been switched to {state.mode} mode before it stopped; a resume starts "
            f"in {mode.value} mode, as configured{again}", level="info"))
    model = wiring.config.backend.model
    if state.switched_model and state.switched_model != model:
        events.emit(Notice(
            f"it had been switched to the model {state.switched_model} before it stopped; a "
            f"resume continues on {model}, as configured (-m {state.switched_model} goes on "
            "with the other)", level="info"))


def _close(wiring: Wiring, status: TerminalStatus, reason: str) -> None:
    wiring.renderer.settle()
    wiring.renderer.clear_title()
    # A journal write can fail (full volume, a backup agent holding the
    # file); losing the exit code over it would be worse than the lost line.
    try:
        if not wiring.session._terminated:
            wiring.session.record_termination(status, reason)
    except OSError:
        pass
    try:
        # Told before the journal closes, so their runs are in it (D-117).
        wiring.runtime.end_hooks()
    finally:
        try:
            wiring.session.close()
        finally:
            _discard(wiring.ephemeral)
            if wiring.mcp is not None:
                wiring.mcp.close()


def farewell(wiring: Wiring) -> str:
    """What an interactive session says as it ends, as codex does (D-059):
    the tokens it used and how to continue it. Nothing for a session that
    never began a conversation."""
    usage, context = wiring.runtime.context.session_usage, wiring.runtime.context
    if not context.history and not usage.total:
        return ""
    cached = f" ({usage.cached_input_tokens:,} cached)" if usage.cached_input_tokens else ""
    tokens = f"tokens used: {usage.input_tokens:,} in{cached}, {usage.output_tokens:,} out."
    if wiring.session.degraded:
        return (f"{tokens} This session's journal is incomplete "
                f"({wiring.session.degraded}), so it may not resume.")
    command = f"foundry resume {wiring.session.session_id}"
    if not same_path(Path.cwd(), wiring.workspace.root):
        command += f' --workspace "{wiring.workspace.root}"'
    return f"{tokens} To continue this session: {command}"


def _say_farewell(console: Console, wiring: Wiring) -> None:
    text = farewell(wiring)
    if text:
        console.print(f"[dim]{escape(text)}[/dim]", soft_wrap=True)


def request_headers(backend) -> dict[str, str]:
    """The backend's ``headers``, and those of its ``env_http_headers`` whose
    variable is set (D-084), as codex sends them: values read when the session
    starts, and scrubbed from anything written or shown, as a key is."""
    headers = dict(backend.headers)
    for header, variable in backend.env_http_headers.items():
        value = os.environ.get(variable)
        if not value:
            continue   # an unset variable leaves the header out
        if "\r" in value or "\n" in value:
            # http.client would refuse it with a message quoting it.
            raise ConfigError(f"{variable} holds a line break, which the {header} header "
                              "cannot carry")
        default_redactor().register(value)
        headers[header] = value
    return headers


def _credential_source(config: Config, vault: CredentialVault):
    """The credential this configuration uses. anthropic_messages has its own
    variable and vault field: an OpenAI key must never reach Anthropic, nor the
    reverse; a backend that names its variable (env_key, D-084) reads only that."""
    if config.backend.env_key:
        return EnvKeySource(config.backend.env_key)
    if config.backend.credential_source == "gateway_token":
        return StaticTokenSource(vault)
    if config.backend.protocol == ANTHROPIC_PROTOCOL:
        return ApiKeySource.anthropic(vault)
    return ApiKeySource(vault)


# --- commands -------------------------------------------------------------


def _config_args(args: argparse.Namespace, *, mode: Mode | None = None) -> dict:
    """``-m``, ``--mode``, ``-c`` and ``--profile`` as build() takes them (D-042)."""
    overrides: dict = {}
    if args.model:
        overrides["model"] = args.model
    chosen = mode or (Mode(args.mode) if args.mode else None)
    if chosen is not None:
        overrides["mode"] = chosen
    settings = parse_settings(args.config or [])
    # codex's --enable and --disable (D-144): -c features.NAME=true and =false.
    switched: dict[str, bool] = {}
    for flag, value in (("enable", True), ("disable", False)):
        for name in getattr(args, flag, None) or []:
            if switched.get(name, value) != value:
                raise ConfigError(f"--enable and --disable both name {name}")
            switched[name] = value
    if switched:
        settings.setdefault("features", {}).update(switched)
    return {"overrides": overrides or None, "profile": args.profile, "settings": settings}


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
    _announce_resume(wiring)
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
    _announce_resume(fork)
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
    _announce_resume(resumed)
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
    elif isinstance(source, EnvKeySource):
        label = "key"
    elif source.vault_field == "anthropic_api_key":
        label = "Anthropic API key"
    else:
        label = "OpenAI API key"
    if getattr(args, "action", None) == "status":
        if getattr(args, "with_api_key", False):
            console.print("[red]login status reads no key[/red]: run it without --with-api-key")
            return 2
        return _login_status(console, source, vault, label)
    if isinstance(source, EnvKeySource):
        console.print(f"[yellow]this backend reads its key from {source.env_var}[/yellow] "
                      "(backend.env_key): set it in your environment; foundry login stores "
                      "nothing for it")
        return 1
    if getattr(args, "with_api_key", False):
        secret = _secret_from_stdin(console, label)
        if secret is None:
            return 1
        source.store(secret)
        console.print(f"[green]saved[/green] {home / 'auth.json'} ({label})")
        return 0

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
            f"secret. Set {env_var} in the environment instead, or pipe it to "
            "foundry login --with-api-key."
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


# More than any key or token is long: what was piped in is something else.
STDIN_SECRET_LIMIT = 16_384


def _secret_from_stdin(console: Console, label: str) -> str | None:
    """The key piped to ``foundry login --with-api-key`` (D-087), as codex's
    ``printenv OPENAI_API_KEY | codex login --with-api-key``.

    Never from a terminal: typed there, the key would be echoed on screen,
    which is the thing the prompt without the flag exists to avoid. And never
    quoted back: a refusal says what is wrong with it, not what it was.
    """
    try:
        at_terminal = sys.stdin.isatty()
    except (AttributeError, ValueError):
        at_terminal = False
    if at_terminal:
        console.print(f"[red]--with-api-key reads the {label} from a pipe[/red], as "
                      "printenv OPENAI_API_KEY | foundry login --with-api-key; at a terminal, "
                      "run foundry login without it and paste the key where it is not shown")
        return None
    # Bytes where there are any, decoded as UTF-8: decoded in the console's
    # code page, a key piped from a UTF-8 file with a byte order mark kept the
    # mark as three more characters of the key. The mark itself is dropped
    # below.
    stream = getattr(sys.stdin, "buffer", None)
    try:
        if stream is not None:
            data = stream.read(STDIN_SECRET_LIMIT + 1)
            too_long = len(data) > STDIN_SECRET_LIMIT
            piped = data.decode("utf-8")
        else:
            piped = sys.stdin.read(STDIN_SECRET_LIMIT + 1)
            too_long = len(piped) > STDIN_SECRET_LIMIT
    except (OSError, ValueError) as exc:     # UnicodeDecodeError is a ValueError
        console.print(f"[red]cannot read stdin[/red] ({type(exc).__name__})")
        return None
    secret = (piped or "").lstrip("\ufeff").strip()
    if not secret:
        console.print(f"[red]nothing on stdin[/red]: pipe the {label} in")
        return None
    if too_long or any(c.isspace() or not c.isprintable() for c in secret):
        console.print(f"[red]what was piped in does not look like the {label}[/red]: one line, "
                      "no spaces, "
                      "is expected (it is not shown here)")
        return None
    return secret


def _login_status(console: Console, source, vault: CredentialVault, label: str) -> int:
    """``foundry login status`` (D-087): where this configuration's credential
    comes from -- never any of it. 0 when there is one, 1 when not."""
    env_var = source.env_var
    if os.environ.get(env_var):
        console.print(f"{label}: from {env_var}")
        return 0
    if isinstance(source, EnvKeySource):
        console.print(f"[yellow]no {label}[/yellow]: this backend reads it from {env_var} "
                      "(backend.env_key), which is not set")
        return 1
    field_name = "token" if isinstance(source, StaticTokenSource) else source.vault_field
    if (vault.load() or {}).get(field_name):
        console.print(f"{label}: stored by foundry login ({vault.path})")
        return 0
    console.print(f"[yellow]no {label}[/yellow]: set {env_var}, or run foundry login")
    return 1


def cmd_logout(args: argparse.Namespace) -> int:
    vault = CredentialVault(user_dir() / "auth.json")
    vault.clear()
    Console().print("[green]credentials removed[/green]")
    return 0


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


# --- foundry mcp (D-082) ----------------------------------------------------------------

NO_SERVERS = ("[dim]no MCP servers: `foundry mcp add NAME -- COMMAND` adds one, or "
              "`foundry mcp add NAME --url URL`[/dim]")


def cmd_mcp(args: argparse.Namespace) -> int:
    """``foundry mcp list | get | add | remove``, as ``codex mcp``: the servers
    in the user's own config.toml, from the command line. Listing starts none
    of them -- /mcp in a session says how each is."""
    console = Console()
    path = user_dir() / "config.toml"
    action = getattr(args, "mcp_command", None) or "list"
    if action == "get":
        return _mcp_get(args, console)
    if action == "add":
        add_server(path, args.name, _server_entry(args))
        console.print(f"[green]added[/green] MCP server {escape(args.name)} to "
                      f"{escape(str(path))}; a new session starts it")
        return 0
    if action == "remove":
        remove_server(path, args.name)
        console.print(f"[green]removed[/green] MCP server {escape(args.name)} from "
                      f"{escape(str(path))}")
        return 0
    servers = load_config(home=user_dir(), profile=getattr(args, "profile", None)).mcp_servers
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps([_server_record(s) for s in servers.values()],
                                    indent=2) + "\n")
        return 0
    if not servers:
        console.print(NO_SERVERS)
    for server in servers.values():
        # As /mcp shows it (D-064, D-081): a program, not its arguments; an
        # address's host, not its path.
        state = "enabled" if server.enabled else "disabled"
        kind = "read-only" if server.read_only else "may change things"
        console.print(f"[bold]{escape(server.name)}[/bold] [dim]({escape(server.shown_as())})"
                      f"[/dim] {state}, {kind}", soft_wrap=True)
        said = []
        if server.enabled_tools is not None:
            said.append("only " + (", ".join(server.enabled_tools) or "no tools"))
        if server.disabled_tools:
            said.append("never " + ", ".join(server.disabled_tools))
        if server.env_vars:
            said.append("passes " + ", ".join(server.env_vars))
        if server.bearer_token_env_var:
            said.append(f"token from {server.bearer_token_env_var}")
        if said:
            console.print(f"  [dim]{safe('; '.join(said))}[/dim]", soft_wrap=True)
    return 0


def start_memory_pass(wiring: Wiring, profile: str | None) -> None:
    """codex's memory pass as a session starts (D-145), in a process of its
    own: it says nothing, outlives the session, and is one at a time for a
    workspace. It runs this process's own Foundry -- installed, or a clone's
    run-foundry.py -- from the Foundry folder with -P, so nothing in the
    workspace is imported in its place."""
    source = str(Path(foundry.__file__).resolve().parent.parent)
    start = (f"import sys; sys.path.insert(0, {source!r}); "
             "from foundry.__main__ import main; raise SystemExit(main())")
    argv = [sys.executable, "-P", "-c", start, "memories", "update", "--quiet",
            "-C", str(wiring.workspace.root)]
    if profile:
        argv += ["-p", profile]
    flags = 0
    if IS_WINDOWS:
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
    try:
        subprocess.Popen(argv, cwd=str(wiring.home or user_dir()), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, start_new_session=not IS_WINDOWS,
                         close_fds=True)
    except OSError:
        pass        # memories wait for the next session's pass


def cmd_memories(args: argparse.Namespace) -> int:
    """``foundry memories [show] | update | reset`` (D-145): this workspace's
    memories; the sessions due read now, in the foreground, as a session's
    start reads them in the background; or the memories forgotten, and the
    sessions before now never read for them again."""
    console = Console()
    home = user_dir()
    try:
        root = Workspace(args.workspace).root
    except PathRejected as exc:
        raise ConfigError(str(exc)) from exc
    action = getattr(args, "memories_command", None) or "show"
    folder = memories_folder(home, root)
    if action == "reset":
        if forget(home, root):
            console.print(f"forgot the memories of {escape(str(root))}; sessions from now on "
                          "are read for them", soft_wrap=True)
        else:
            console.print(f"[dim]{escape(str(root))} has no memories[/dim]", soft_wrap=True)
        return 0
    if action == "show":
        own = folder / MEMORY_SESSIONS
        kept = len(list(own.glob("*.md"))) if own.is_dir() else 0
        last = load_state(folder).get("last_pass") or {}
        console.print(f"{escape(str(folder))}: {kept} session(s) kept"
                      + (f"; the last pass read {last.get('read', 0)}" if last else
                         "; no pass yet"), soft_wrap=True)
        # What a session starts with, then what read_memories gives it.
        for heading, text in (("summary", memory_summary(folder)),
                              ("notes", memory_notes(folder))):
            if text:
                console.print(f"\n[bold]{heading}[/bold]")
                console.print(text, markup=False, highlight=False, soft_wrap=True)
        return 0
    if str(root) not in _trusted_paths(home):
        raise ConfigError(f"memories are kept only for a workspace you trust; {root} is not "
                          "(a session there asks, and exec --trust trusts it)")
    config = load_config(root, home=home, profile=getattr(args, "profile", None))
    api_key = _credential_source(config, CredentialVault(home / "auth.json")).acquire().reveal()
    result = run_pass(home=home, root=root, settings=config.memory_settings,
                      backend=make_backend(config, api_key), model=config.backend.model,
                      scrub=default_redactor().scrub)
    if not args.quiet:
        console.print(escape(result.said()), soft_wrap=True)
    return 0 if not result.errors else EXIT_CODES[TerminalStatus.FAILED]


def cmd_features(args: argparse.Namespace) -> int:
    """``foundry features list | enable | disable``, as ``codex features``
    (D-144): codex's feature flags, each with what it is here and whether it
    is on, and one turned on or off in the user's own config.toml -- not a
    profile's, as codex's is not."""
    console = Console()
    action = getattr(args, "features_command", None) or "list"
    if action in ("enable", "disable"):
        value = action == "enable"
        problem = refusal(args.name, value)
        if problem:
            raise ConfigError(problem)
        name = FEATURE_ALIASES.get(args.name, args.name)
        path = user_dir() / "config.toml"
        set_feature(path, name, value)
        console.print(f"[green]{action}d[/green] {escape(name)}: features.{escape(name)} = "
                      f"{'true' if value else 'false'} in {escape(str(path))}; a new session "
                      "goes by it", soft_wrap=True)
        return 0
    config = load_config(home=user_dir())
    width = max(len(feature.name) for feature in FEATURES)
    for feature in FEATURES:
        said = feature_state(config, feature)
        style = "green" if said == "on" else "dim"
        here = (escape(feature.said) if feature.setting or feature.ignored
                else f"[dim]{escape(feature.said)}[/dim]")
        console.print(f"{escape(feature.name):<{width}}  [{style}]{said:<7}[/{style}]  {here}",
                      soft_wrap=True)
    console.print("[dim]codex's other features have no counterpart here: off is what each "
                  "already is[/dim]")
    return 0


# What `foundry mcp get` shows in place of a value a credential's name marks.
HIDDEN = "[hidden]"


def _mcp_get(args: argparse.Namespace, console: Console) -> int:
    """``foundry mcp get NAME``, as ``codex mcp get``: one server's settings
    in full -- what the listing leaves out, its arguments, environment and
    address -- except a value a credential's name marks, and what the
    redactor knows to be one."""
    servers = load_config(home=user_dir(), profile=getattr(args, "profile", None)).mcp_servers
    server = servers.get(args.name)
    if server is None:
        known = f"; it names {', '.join(sorted(servers))}" if servers else ""
        raise ConfigError(f"your configuration has no MCP server named {args.name}{known}")
    scrub = default_redactor().scrub
    shown = _shown_settings(server, scrub)
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({**_server_record(server), **shown}, indent=2) + "\n")
        return 0
    state = "enabled" if server.enabled else "disabled"
    kind = "read-only" if server.read_only else "may change things"
    console.print(f"[bold]{escape(server.name)}[/bold] [dim]({'http' if server.url else 'stdio'})"
                  f"[/dim] {state}, {kind}", soft_wrap=True)
    lines = [("url", shown["url"]) if server.url else ("command", shown["command"])]
    if shown["args"]:
        import subprocess

        lines.append(("args", subprocess.list2cmdline(shown["args"])))
    if shown["cwd"]:
        lines.append(("cwd", shown["cwd"]))
    if shown["env"]:
        lines.append(("env", ", ".join(f"{key}={value}" for key, value in shown["env"].items())))
    if server.env_vars:
        lines.append(("passes", ", ".join(server.env_vars)))
    if server.bearer_token_env_var:
        lines.append(("token", f"from {server.bearer_token_env_var}"))
    headers = [f"{key}: {value}" for key, value in shown["http_headers"].items()]
    headers += [f"{key} from {variable}" for key, variable in server.env_http_headers]
    if headers:
        lines.append(("headers", ", ".join(headers)))
    tools = []
    if server.enabled_tools is not None:
        tools.append("only " + (", ".join(server.enabled_tools) or "no tools"))
    if server.disabled_tools:
        tools.append("never " + ", ".join(server.disabled_tools))
    lines.append(("tools", "; ".join(tools) or "all it lists"))
    lines.append(("timeouts", f"start {server.startup_timeout_s:g}s, each call "
                              f"{server.tool_timeout_s:g}s"))
    lines.append(("remove", f"foundry mcp remove {server.name}"))
    for label, value in lines:
        console.print(f"  [dim]{label:<9}[/dim]{safe(value)}", soft_wrap=True)
    return 0


def _shown_settings(server, scrub) -> dict[str, Any]:
    """The settings the listing leaves out, as `mcp get` shows them."""
    return {"command": scrub(server.command) if server.command else None,
            "args": _shown_args(server.args, scrub),
            "cwd": scrub(server.cwd) if server.cwd else None,
            "http_headers": {key: scrub(value) for key, value in server.http_headers},
            "env_http_headers": dict(server.env_http_headers),
            "env": {key: HIDDEN if looks_like_credential(key) else scrub(value)
                    for key, value in server.env},
            "url": _shown_url(server.url, scrub) if server.url else None}


def _shown_args(args: tuple[str, ...], scrub) -> list[str]:
    """A value a credential's name marks -- ``--api-key X``, ``--token=X``,
    ``API_KEY=X`` -- is shown as hidden. An option so named that takes no
    value hides the argument after it all the same: the cautious mistake."""
    shown: list[str] = []
    hide = False
    for arg in args:
        if hide:
            shown.append(HIDDEN)
            hide = False
            continue
        name, sep, _ = arg.partition("=")
        if (sep or arg.startswith("-")) and looks_like_credential(name):
            shown.append(f"{name}={HIDDEN}" if sep else arg)
            hide = not sep
            continue
        shown.append(scrub(arg))
    return shown


def _shown_url(url: str, scrub) -> str:
    """An address without a query value a credential's name marks --
    ``?api_key=X``. The rest is shown as written; the configuration refuses a
    user name or password in it."""
    import urllib.parse

    parts = urllib.parse.urlsplit(url)
    query = "&".join(_shown_pair(pair, urllib.parse.unquote_plus)
                     for pair in parts.query.split("&"))
    return scrub(urllib.parse.urlunsplit(parts._replace(query=query)))


def _shown_pair(pair: str, unquote) -> str:
    key, sep, _ = pair.partition("=")
    return f"{key}={HIDDEN}" if sep and looks_like_credential(unquote(key)) else pair


def cmd_mcp_server(args: argparse.Namespace) -> int:
    """Foundry as an MCP server over stdio (D-103)."""
    from foundry.cli.mcp_server import serve_stdio

    return serve_stdio(args)


def _server_entry(args: argparse.Namespace) -> dict[str, Any]:
    """The ``[mcp_servers.<name>]`` settings ``foundry mcp add`` was given."""
    command = list(args.server_command)
    entry: dict[str, Any] = {}
    if args.url is not None:
        if command:
            raise ConfigError("a server is a program Foundry starts or an address it reaches: "
                              "give --url or a command after --, not both")
        if args.env or args.env_var:
            raise ConfigError("--env and --env-var are for a server Foundry starts, not one "
                              "at a --url")
        entry["url"] = args.url
        if args.bearer_token_env_var:
            entry["bearer_token_env_var"] = args.bearer_token_env_var
    else:
        if not command:
            raise ConfigError("give the command that starts the server after --, or its --url")
        if args.bearer_token_env_var:
            raise ConfigError("--bearer-token-env-var is for a server at a --url")
        entry["command"] = command[0]
        if command[1:]:
            entry["args"] = command[1:]
        env: dict[str, str] = {}
        for item in args.env or []:
            key, sep, value = item.partition("=")
            if not sep or not key:
                raise ConfigError("--env takes KEY=VALUE")   # not echoed: it may be a secret
            if looks_like_credential(key):
                # Written into config.toml, and readable on this command
                # line by other processes meanwhile.
                raise ConfigError(f"{key} looks like a secret, and --env writes its value into "
                                  f"config.toml; set it in your environment and pass it "
                                  f"through with --env-var {key}")
            env[key] = value
        if env:
            entry["env"] = env
        if args.env_var:
            entry["env_vars"] = list(args.env_var)
    if args.read_only:
        entry["read_only"] = True
    return entry


def _server_record(server) -> dict[str, Any]:
    """``foundry mcp list --json``: what the listing shows, as data."""
    return {
        "name": server.name,
        "transport": "http" if server.url else "stdio",
        "where": server.shown_as(),
        "enabled": server.enabled,
        "read_only": server.read_only,
        "enabled_tools": (list(server.enabled_tools)
                          if server.enabled_tools is not None else None),
        "disabled_tools": list(server.disabled_tools),
        "env_vars": list(server.env_vars),
        "bearer_token_env_var": server.bearer_token_env_var or None,
        "startup_timeout_s": server.startup_timeout_s,
        "tool_timeout_s": server.tool_timeout_s,
    }


def cmd_report(args: argparse.Namespace) -> int:
    from foundry.cli.report import render

    root = user_dir() / "sessions"
    if not root.is_dir():
        Console().print("[dim]no sessions recorded yet[/dim]")
        return 0
    print(render(root, as_json=args.json))
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    """``foundry completion [powershell|bash]`` (D-093): a Tab-completion
    script for the shell, printed for a profile line to load."""
    text = completion_script(args.shell, build_parser())
    sys.stdout.flush()
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(text)
    else:
        # As bytes: printed as text on Windows, every newline became CRLF,
        # and bash reads the carriage return as part of each line.
        stream.write(text.encode("utf-8"))
        stream.flush()
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    """``foundry policy check COMMAND`` (D-135), as ``codex execpolicy
    check``: what the policy would decide for a command in this workspace,
    with this configuration -- at a terminal, and unattended -- without
    running it or starting a session. Hooks are not run."""
    console = Console()
    command = " ".join(args.command).strip()
    if not command:
        Console(stderr=True).print("[red]policy check needs the command to check[/red]")
        return 2
    try:
        root = Workspace(Path(args.workspace).resolve()).root
    except PathRejected as exc:
        Console(stderr=True).print(f"[red]{escape(str(exc))}[/red]")
        return 2
    home = user_dir()
    # The repository's own rules count only once it is trusted; nothing is
    # asked here.
    trusted = str(root) in _trusted_paths(home)
    if not trusted and (root / ".foundry" / "config.toml").is_file():
        Console(stderr=True).print(
            "[dim]this workspace's .foundry/config.toml is not trusted yet: its rules are "
            "not applied[/dim]", soft_wrap=True)
    config = load_config(root if trusted else None, home=home, **_config_args(args))
    policy = PolicyEngine(rules=list(builtin_rules()) + list(config.rules), mode=config.mode)
    op = RunCommand().validate({"command": command})
    here, _ = policy.evaluate(op)
    policy.interactive = False
    alone, _ = policy.evaluate(op)
    # What would be asked about is refused where nobody is there to ask.
    alone_verdict = "deny" if alone.verdict is Verdict.ASK else alone.verdict.value
    if args.json:
        def side(decision, verdict):
            return {"verdict": verdict, "reason": decision.reason, "rule": decision.rule_id,
                    "step": decision.step}

        print(json.dumps({"command": command, "mode": config.mode.value,
                          "at_a_terminal": side(here, here.verdict.value),
                          "unattended": side(alone, alone_verdict)}, ensure_ascii=False))
        return 0
    console.print(escape(command), soft_wrap=True)
    for label, decision, verdict in (
            (f"at a terminal ({config.mode.value})", here, here.verdict.value),
            ("unattended", alone, alone_verdict)):
        console.print(f"  {label:<26}[bold]{verdict}[/bold] -- {escape(decision.reason)} "
                      f"[dim]({escape(decision.rule_id)}, step {decision.step})[/dim]",
                      soft_wrap=True)
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

        from foundry.core.winapi import find_program

        try:
            shell = find_program("powershell.exe")
            if shell is None:
                raise FileNotFoundError("powershell.exe")
            result = subprocess.run(
                [shell, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
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


def _config_flags(parser: argparse.ArgumentParser, *, modes: tuple[Mode, ...] = tuple(Mode)) -> None:
    """The settings a run can be given on its command line (D-042). They win
    over every file and the environment; secrets are not among them."""
    parser.add_argument("-m", "--model", help="the model to use")
    parser.add_argument("--mode", choices=[m.value for m in modes],
                        help="the permission mode" + (
                            " (default dont_ask: nobody is there to approve)"
                            if Mode.DEFAULT not in modes else ""))
    parser.add_argument("-c", "--config", action="append", metavar="KEY=VALUE",
                        help="a setting, such as -c runtime.max_tool_rounds=60 "
                             "(repeatable; no secrets)")
    parser.add_argument("-p", "--profile",
                        help="a [profiles.<name>] table from your config.toml")
    parser.add_argument("--enable", action="append", metavar="FEATURE",
                        help="turn a feature on for this run, as -c features.FEATURE=true "
                             "(repeatable); foundry features list names them")
    parser.add_argument("--disable", action="append", metavar="FEATURE",
                        help="turn a feature off for this run, as -c features.FEATURE=false "
                             "(repeatable)")


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


def _color_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--color", choices=("always", "never", "auto"), default="auto",
                        help="colour what is said on stderr: always, never, or auto (on a "
                             "terminal; the default)")


def _trust_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--trust", action="store_true",
                        help="trust this repository, as a yes when asked does: its "
                             "instructions and settings are read, in this run and later "
                             "ones")


def _ephemeral_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ephemeral", action="store_true",
                        help="keep no record of the run once it ends: nothing to resume, "
                             "nothing in foundry sessions")


def _image_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-i", "--image", action="append", metavar="PATH",
                        help="attach an image to the first task (repeatable)")


class _Parser(argparse.ArgumentParser):
    """Says, when it refuses one of codex's flags, what Foundry takes instead
    (D-097). Only a command line it is already refusing is looked at."""

    def parse_args(self, args=None, namespace=None):
        parsed, unrecognized = self.parse_known_args(args, namespace)
        if unrecognized:
            argv = sys.argv[1:] if args is None else list(args)
            # The subcommand's usage, not the list of every subcommand.
            refusing = next((action.choices[argv[0]] for action in self._actions
                             if isinstance(action, argparse._SubParsersAction)
                             and argv and argv[0] in action.choices), self)
            refusing.error("\n".join([f"unrecognized arguments: {' '.join(unrecognized)}",
                                       *codex_hints(argv, unrecognized)]))
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="foundry", description="A local coding-agent runtime.")
    parser.add_argument("-V", "--version", action="version", version=f"foundry {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start a session (default)")
    run.add_argument("task", nargs="?",
                     help="the first message; at a terminal the session goes on "
                          "after it, otherwise it ends with it")
    run.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                     help="repository to work in (default: here)")
    _image_flag(run)
    _config_flags(run)
    run.set_defaults(func=cmd_session)

    resume = sub.add_parser("resume", help="continue a recorded session")
    resume.add_argument("resume", nargs="?", default=None, metavar="session",
                        help="the session id or name, or last for the newest in this workspace "
                             "(default: at a terminal, pick from the recent ones; "
                             "otherwise last)")
    resume.add_argument("task", nargs="?",
                        help="the first message; at a terminal the session goes on "
                             "after it, otherwise it ends with it")
    resume.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                        help="repository the session worked in")
    resume.add_argument("--last", action="store_true",
                        help="the newest session, as codex's resume --last; a word given "
                             "is then the task")
    resume.add_argument("--all", action="store_true",
                        help="from every workspace's sessions, continued where each was "
                             "recorded")
    _image_flag(resume)
    _config_flags(resume)
    resume.set_defaults(func=cmd_session)

    fork = sub.add_parser("fork", help="continue a copy of a recorded session, apart "
                                       "from it")
    fork.add_argument("resume", nargs="?", default=None, metavar="session",
                      help="the session id or name, or last for the newest in this "
                           "workspace (default: at a terminal, pick from the recent ones; "
                           "otherwise last)")
    fork.add_argument("task", nargs="?",
                      help="the first message; at a terminal the session goes on after "
                           "it, otherwise it ends with it")
    fork.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                      help="repository the session worked in")
    fork.add_argument("--last", action="store_true",
                      help="the newest session, as codex's fork --last; a word given is "
                           "then the task")
    fork.add_argument("--all", action="store_true",
                      help="from every workspace's sessions, continued where each was "
                           "recorded")
    _image_flag(fork)
    _config_flags(fork)
    fork.set_defaults(func=cmd_session)

    undo = sub.add_parser("undo", help="revert the files a recorded session patched")
    undo.add_argument("session", nargs="?", default="last",
                      help="the session id or name (default: last, the newest in this "
                           "workspace)")
    undo.add_argument("--last", action="store_true",
                      help="only the session's newest patch")
    undo.add_argument("--yes", action="store_true", help="do not ask first")
    undo.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    undo.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                      help="repository the session worked in")
    undo.set_defaults(func=cmd_undo)

    execute = sub.add_parser("exec", help="run one task headlessly (approvals are denied)")
    execute.add_argument("task", nargs="?", help="the task; - reads it from stdin")
    execute.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                         help="repository to work in (default: here)")
    execute.add_argument("--json", action="store_true", help="emit the event stream as JSONL")
    execute.add_argument("--resume", metavar="SESSION",
                         help="continue a recorded session (an id, or last); the task "
                              "defaults to carrying on where it stopped")
    execute.add_argument("--fork", metavar="SESSION",
                         help="continue a copy of a recorded session, apart from it (an "
                              "id, a name, or last); the task defaults as with --resume")
    execute.add_argument("-o", "--output-last-message", metavar="FILE",
                         help="write the run's final summary or reply to FILE")
    _ephemeral_flag(execute)
    _color_flag(execute)
    _trust_flag(execute)
    execute.add_argument("--output-schema", metavar="FILE",
                         help="a JSON Schema the run's result must match; the result is "
                              "printed as JSON, and written by -o")
    _image_flag(execute)
    _config_flags(execute, modes=(Mode.DONT_ASK, Mode.PLAN, Mode.ACCEPT_EDITS))
    execute.set_defaults(func=cmd_exec)

    review = sub.add_parser("review", help="review changes headlessly, read-only, as codex "
                                           "review does")
    targets = review.add_mutually_exclusive_group()
    targets.add_argument("--uncommitted", action="store_true",
                         help="the staged, unstaged and untracked changes")
    targets.add_argument("--base", metavar="BRANCH",
                         help="what this branch changed since it left BRANCH")
    targets.add_argument("--commit", metavar="SHA", help="what one commit changed")
    targets.add_argument("prompt", nargs="?", metavar="PROMPT",
                         help="the review's instructions instead; - reads them from stdin")
    review.add_argument("--title", help="the commit's title, shown with it (with --commit)")
    review.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                        help="repository to review (default: here)")
    review.add_argument("--json", action="store_true", help="emit the event stream as JSONL")
    review.add_argument("-o", "--output-last-message", metavar="FILE",
                        help="write the review to FILE")
    _ephemeral_flag(review)
    _color_flag(review)
    _trust_flag(review)
    # Read-only whatever the mode; dont_ask, as nobody is there to approve.
    _config_flags(review, modes=(Mode.DONT_ASK,))
    review.set_defaults(func=cmd_review)

    record = sub.add_parser("record", help="capture a session as a replay fixture")
    record.add_argument("task")
    record.add_argument("--output", required=True, help="fixture file to write")
    record.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                        help="repository to work in (default: here)")
    _config_flags(record)
    record.set_defaults(func=cmd_record)

    login = sub.add_parser("login", help="store an API key, or say where it comes from")
    login.add_argument("action", nargs="?", choices=("status",),
                       help="status: where this configuration's key comes from")
    login.add_argument("--with-api-key", action="store_true",
                       help="read the key (or gateway token) from stdin, as "
                            "printenv OPENAI_API_KEY | foundry login --with-api-key")
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="remove stored credentials")
    logout.set_defaults(func=cmd_logout)

    sessions = sub.add_parser("sessions", help="list or show recorded sessions")
    sessions.add_argument("session_id", nargs="?", help="a session id or name: its records")
    sessions.add_argument("--limit", type=int, default=20)
    sessions.add_argument("--archived", action="store_true",
                          help="the archived sessions (foundry archive), not the others")
    sessions.set_defaults(func=cmd_sessions)

    for name, said in (("archive", "move a recorded session out of every list, kept whole"),
                       ("unarchive", "bring an archived session back")):
        archiving = sub.add_parser(name, help=said)
        archiving.add_argument("session", help="the session's id or name")
        archiving.set_defaults(func=cmd_archive)

    delete = sub.add_parser("delete", help="delete a recorded session for good")
    delete.add_argument("session", help="the session's id or name")
    delete.add_argument("--force", action="store_true",
                        help="do not ask first (takes only an id)")
    delete.set_defaults(func=cmd_delete)

    report = sub.add_parser("report", help="summarize recorded sessions")
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    doctor = sub.add_parser("doctor", help="check the environment")
    doctor.set_defaults(func=cmd_doctor)

    policy = sub.add_parser("policy", help="what the policy decides, as codex execpolicy")
    policy_sub = policy.add_subparsers(dest="policy_command", required=True)
    check = policy_sub.add_parser("check", help="what the policy would decide for a command "
                                                "here, without running it")
    check.add_argument("command", nargs="+", metavar="COMMAND",
                       help="the command, as the model would run it; after --, or quoted")
    check.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                       help="the repository it would run in (default: here)")
    check.add_argument("--json", action="store_true", help="the decisions as JSON")
    _config_flags(check)
    check.set_defaults(func=cmd_policy)

    completion = sub.add_parser("completion",
                                help="print a Tab-completion script for your shell")
    completion.add_argument("shell", nargs="?", choices=SHELLS, default="powershell",
                            help="powershell (the default) or bash")
    completion.set_defaults(func=cmd_completion)

    serve = sub.add_parser("mcp-server",
                           help="be an MCP server over stdio, offering foundry and "
                                "foundry-reply (approvals are denied)")
    _config_flags(serve, modes=(Mode.DONT_ASK, Mode.PLAN, Mode.ACCEPT_EDITS))
    serve.set_defaults(func=cmd_mcp_server)

    features = sub.add_parser("features", help="codex's feature flags: what each is here, "
                                               "and turning one on or off in your "
                                               "config.toml")
    features.set_defaults(func=cmd_features)
    features_sub = features.add_subparsers(dest="features_command")
    features_sub.add_parser("list", help="each feature, whether it is on, and what it is here")
    for name, said in (("enable", "on"), ("disable", "off")):
        switch = features_sub.add_parser(name, help=f"turn a feature {said} in your "
                                                    "config.toml, for later sessions")
        switch.add_argument("name", metavar="FEATURE")

    memories = sub.add_parser("memories", help="this workspace's memories: shown, updated "
                                               "now, or forgotten")
    memories.set_defaults(func=cmd_memories, workspace=".", quiet=False)
    memories_sub = memories.add_subparsers(dest="memories_command")
    for name, said in (("show", "where they are, and how many sessions are in them"),
                       ("update", "read the sessions due now, and merge the notes"),
                       ("reset", "forget them; sessions from now on are read for them")):
        memory_action = memories_sub.add_parser(name, help=said)
        memory_action.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                                   help="the repository (default: here)")
        if name == "update":
            memory_action.add_argument("-p", "--profile",
                                       help="a [profiles.<name>] table from your config.toml")
            memory_action.add_argument("--quiet", action="store_true",
                                       help="say nothing (a session's start runs it so)")

    mcp = sub.add_parser("mcp", help="list, add or remove MCP servers in your config.toml")
    mcp.set_defaults(func=cmd_mcp)
    mcp_sub = mcp.add_subparsers(dest="mcp_command")
    mcp_list = mcp_sub.add_parser("list", help="the MCP servers your configuration names")
    mcp_list.add_argument("--json", action="store_true")
    mcp_list.add_argument("--profile", help="as a session with this profile would see them")
    mcp_get = mcp_sub.add_parser("get", help="one MCP server's settings, in full")
    mcp_get.add_argument("name")
    mcp_get.add_argument("--json", action="store_true")
    mcp_get.add_argument("--profile", help="as a session with this profile would see it")
    mcp_add = mcp_sub.add_parser(
        "add", help="add an MCP server to your config.toml",
        usage="foundry mcp add NAME [--env KEY=VALUE] [--env-var NAME] [--read-only] "
              "-- COMMAND [ARGS...]\n       foundry mcp add NAME --url URL "
              "[--bearer-token-env-var VAR] [--read-only]")
    mcp_add.add_argument("name", help="letters, digits, _ and -; its tools are mcp__NAME__*")
    mcp_add.add_argument("--url", help="the address of a server already running "
                                       "(streamable HTTP)")
    mcp_add.add_argument("--bearer-token-env-var", metavar="VAR",
                         help="the environment variable holding its token")
    mcp_add.add_argument("--env", action="append", metavar="KEY=VALUE",
                         help="a plain value for the server's environment (repeatable)")
    mcp_add.add_argument("--env-var", action="append", metavar="NAME",
                         help="a variable passed through from your environment, by name "
                              "(repeatable): where a secret goes")
    mcp_add.add_argument("--read-only", action="store_true",
                         help="you vouch its tools change nothing")
    # The command after -- is taken off before parsing (_split_command): a
    # REMAINDER argument would swallow the options that come after NAME.
    mcp_add.set_defaults(server_command=[])
    mcp_remove = mcp_sub.add_parser("remove", help="take an MCP server out of your config.toml")
    mcp_remove.add_argument("name")

    return parser


def _split_command(argv: list[str]) -> tuple[list[str], list[str] | None]:
    """``foundry mcp add NAME ... -- COMMAND ARGS``: the server's command is
    everything after the first ``--``, its own options included."""
    if argv[:2] == ["mcp", "add"] and "--" in argv:
        cut = argv.index("--")
        return argv[:cut], argv[cut + 1:]
    return argv, None


def _value_options(parser: argparse.ArgumentParser, command: str) -> set[str]:
    """The options of ``command`` that take a value, from the parser itself."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and command in action.choices:
            return {flag for option in action.choices[command]._actions
                    if option.option_strings and option.nargs != 0
                    for flag in option.option_strings}
    return set()


def exec_resume(argv: list[str], parser: argparse.ArgumentParser) -> list[str] | None:
    """`foundry exec resume [--last | SESSION] [TASK]`, as codex spells it
    (D-088), is `foundry exec --resume SESSION|last [TASK]`; `exec fork ...`
    is `exec --fork ...` the same way (D-133).

    Only a first word `resume` or `fork` before any `--` is read this way, so
    `foundry exec -- resume` is still the task. None: the form is used without
    saying which session. `foundry exec review ...`, as codex spells it too,
    is `foundry review ...` (D-131).
    """
    if not argv or argv[0] != "exec":
        return argv
    options, words, first = _exec_words(argv, parser)
    if words and words[0] == "review":
        return ["review", *argv[1:first], *argv[first + 1:]]
    if not words or words[0] not in ("resume", "fork"):
        return argv
    last = ("--last", False) in options
    kept = [token for token, value in options if value or token != "--last"]
    rest = words[1:]
    if last:
        session, task = "last", rest
    elif rest:
        session, task = rest[0], rest[1:]
    else:
        return None
    return ["exec", *kept, f"--{words[0]}", session, *(["--", *task] if task else [])]


def _exec_words(argv: list[str], parser: argparse.ArgumentParser
                ) -> tuple[list[tuple[str, bool]], list[str], int]:
    """`foundry exec`'s options, each with whether it is another's value; its
    words; and where the first word is. What follows a `--` before any word
    is the task: no words."""
    takes_value = _value_options(parser, "exec")
    options: list[tuple[str, bool]] = []    # (token, is an option's value)
    words: list[str] = []
    first = 0                               # where the first word is
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--":
            if words:
                words.extend(argv[i + 1:])
            break
        if token.startswith("-") and token != "-":
            options.append((token, False))
            if token in takes_value and i + 1 < len(argv):
                options.append((argv[i + 1], True))
                i += 2
                continue
        else:
            first = first or i
            words.append(token)
        i += 1
    return options, words, first


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    rewritten = exec_resume(argv, parser)
    if rewritten is None:
        word = _exec_words(argv, parser)[1][0]
        Console(stderr=True).print(f"[red]exec {word} needs a session id, or --last for the "
                                   "newest[/red]")
        return 2
    argv = rewritten
    # `foundry -m gpt-5` is `foundry run -m gpt-5`, as with no arguments at all
    # (D-042). A bare first word stays a subcommand, so a mistyped one is an
    # error rather than a task sent to the model.
    if not argv or (argv[0].startswith("-")
                    and argv[0] not in ("-h", "--help", "-V", "--version")):
        argv = ["run", *argv]
    argv, server_command = _split_command(argv)
    args = parser.parse_args(argv)
    if server_command is not None:
        args.server_command = server_command
    if not getattr(args, "func", None):
        args = parser.parse_args(["run"])
    try:
        return args.func(args)
    except FoundryError as exc:
        # Its words as they are: "[features]" in one is not markup (D-144).
        Console().print(f"[red]{escape(str(exc))}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]
