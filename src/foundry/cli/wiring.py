"""A session's wiring: the runtime and everything it needs, built from the
configuration -- the backend, the policy, the tools, the journal, the hooks,
skills and MCP servers -- and closed again.

The CLI owns no agent behaviour. It builds the runtime, subscribes to the event
stream, and answers approval requests -- which is what makes a headless runner or
another frontend a matter of swapping the CLI.
"""

from __future__ import annotations

import argparse
import shutil
import dataclasses
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from foundry import __version__
from foundry.cli.render import Renderer
from foundry.cli.agents import Spawner
from foundry.core.auth import ApiKeySource, CredentialVault, EnvKeySource, StaticTokenSource
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.commands import CustomPrompt, load_custom_prompts
from foundry.core.config import ANTHROPIC_PROTOCOL, Config, load_config, parse_settings, user_dir
from foundry.core.config_edit import toml_string
from foundry.core.context import ContextManager
from foundry.core.mcp import McpServers, start_servers
from foundry.core.memories import MemoryTool
from foundry.core.memories import folder as memories_folder
from foundry.core.memories import instructions as memory_instructions
from foundry.core.memories import summary as memory_summary
from foundry.core.errors import AuthError, ConfigError, ResumeError
from foundry.core.goal import ACTIVE as GOAL_ACTIVE
from foundry.core.goal import HINTS as GOAL_HINTS
from foundry.core.goal import PAUSED as GOAL_PAUSED
from foundry.core.goal import usage_line as goal_usage
from foundry.core.events import EventSink, Notice, PlanUpdated, TerminalStatus
from foundry.core.httpc import HttpClient
from foundry.core.hooks import HookSet
from foundry.core.notify import Notifier
from foundry.core.policy.engine import Mode, PolicyEngine, builtin_rules
from foundry.core.prompts import (
    environment_paragraph,
    load_instructions,
    repository_docs,
    system_prompt,
)
from foundry.core.redaction import default_redactor
from foundry.core.resume import (
    JournalState,
    outside_changes,
    read_journal,
    resolve_session,
    resume_note,
    same_path,
)
from foundry.core.retention import sweep
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
from foundry.core.tools.git import capture_baseline, is_git_repository
from foundry.core.tools.agent import SpawnAgent
from foundry.core.tools.registry import default_registry
from foundry.core.winapi import register_included_secrets, withhold_variables
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
    # What session_retention_days removed as this session started (D-163),
    # said once the front end listens (D-168); "" when nothing went.
    swept: str = ""


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
        # what it changed, and its system prompt, are relative to that. With the
        # files changed outside it since, which are not its work (D-177).
        baseline = dataclasses.replace(state.baseline, outside=outside)
    try:
        wiring = _assemble(workspace, config, trusted, baseline, session, state, outside,
                           home=home, console=console)
    except BaseException:
        session.close()
        _discard(discard)
        raise
    wiring.ephemeral = discard
    _prune_sessions(wiring, home / "sessions")
    # The terminal's title is an interactive session's (D-139).
    wiring.renderer.title_items = config.terminal_title if interactive else ()
    return wiring


def _prune_sessions(wiring: Wiring, sessions: Path) -> None:
    """The user's session_retention_days (OQ-19, D-163), as a session
    starts, once its own journal is held: what went is journaled here, and
    said by _announce_found, once the front end listens -- said here, it went
    out before exec --json was listening (D-168)."""
    days = wiring.config.session_retention_days
    if days <= 0:
        return
    swept = sweep(sessions, days)
    if not swept.removed and not swept.left:
        return
    wiring.session.append(EventType.SESSIONS_PRUNED, {
        "retention_days": days, "removed": list(swept.removed), "left": list(swept.left)})
    wiring.swept = (f"removed {len(swept.removed)} session(s) last written more than {days} "
                    "day(s) ago (session_retention_days)")
    if swept.left:
        wiring.swept += f"; {len(swept.left)} in use or not removable, left for next time"


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
    # Their values, and an MCP server's env_vars', are scrubbed from the
    # first record on, before a server starts or a header is sent: the
    # memory pass's own process reads journals with only what it registers
    # here and the model's key (D-154).
    servers = config.mcp_servers.values()
    for name in [*names, *(name for server in servers for name in server.env_vars)]:
        if name:
            default_redactor().register(os.environ.get(name))


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


def _announce_found(wiring: Wiring) -> None:
    """What the session's start found -- the sessions session_retention_days
    removed (D-168), and what a resume found -- said through the event
    stream once the front end listens, so a --json consumer hears it too.
    Every front end calls it once a session is built."""
    events = wiring.runtime.events
    if wiring.swept:
        events.emit(Notice(wiring.swept, level="info"))
    state = wiring.resumed
    if state is None:
        return
    ended = state.status.value + (f" ({state.reason})" if state.reason else "")
    task = state.first_task.splitlines()[0][:80] if state.first_task else "?"
    # The finish a task ended with is answered by that ending: it is how every
    # finished task looks, not a call whose result went missing (D-186).
    lost = sum(r.outcome != "finish_accepted" for r in state.repairs)
    events.emit(Notice(
        (f"forked session {state.fork_of} into {state.session_id} ({task!r}); it goes on "
         "apart from it. " if state.fork_of else
         f"resumed session {state.session_id} ({task!r}); it had ended {ended}. ") +
        (f"{lost} tool call(s) without a recorded result were given one; " if lost else "") +
        f"{len(wiring.outside)} file(s) changed outside it since it started.", level="info"))
    profile, model = wiring.config.backend.name, wiring.config.backend.model
    # The model it last ran on, not the one it started on: resumed on the
    # model /model had switched it to, it was warned of a change there was
    # none of (D-178). A switch it does not go on with is said below.
    if state.profile != profile or (not state.switched_model and state.model != model):
        events.emit(Notice(
            f"this session ran on {state.profile} ({state.switched_model or state.model}); "
            f"it continues on {profile} ({model})", level="warning"))
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


# One table, so `doctor` validates exactly what `build` accepts.
_PROTOCOLS = {
    "openai_compat": OpenAICompatBackend,
    "responses": ResponsesBackend,
    ANTHROPIC_PROTOCOL: AnthropicBackend,
}
