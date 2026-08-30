"""CLI entry points: session, login, logout, sessions, doctor.

The CLI owns no agent behaviour. It builds the runtime, subscribes to the event
stream, and answers approval requests -- which is what makes a headless runner or
another frontend a matter of swapping this file.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from foundry import __version__
from foundry.cli.render import Renderer
from foundry.core.auth import ApiKeySource, CredentialVault, StaticTokenSource
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.recording import RecordingBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.config import Config, load_config, user_dir
from foundry.core.context import ContextManager
from foundry.core.errors import AuthError, ConfigError, FoundryError
from foundry.core.events import EXIT_CODES, EventSink, TerminalStatus
from foundry.core.httpc import HttpClient
from foundry.core.policy.engine import Mode, PolicyEngine, builtin_rules
from foundry.core.prompts import (
    base_system_prompt,
    environment_paragraph,
    load_project_doc,
    permissions_paragraph,
)
from foundry.core.redaction import default_redactor
from foundry.core.runtime import AgentRuntime, Budget
from foundry.core.session import AuditLog, SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.git import capture_baseline, is_git_repository
from foundry.core.tools.registry import default_registry
from foundry.core.workspace import PathRejected, Workspace

TRUST_FILE = "trusted.json"


@dataclass(slots=True)
class Wiring:
    workspace: Workspace
    config: Config
    runtime: AgentRuntime
    renderer: Renderer
    session: SessionStore


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
    )
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
          interactive: bool = True) -> Wiring:
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

    baseline = capture_baseline(workspace.root)

    policy = PolicyEngine(
        rules=list(builtin_rules()) + list(config.rules),
        mode=config.mode,
        dirty_files=set(baseline.dirty_paths) | set(baseline.untracked_paths),
    )

    session = SessionStore(home / "sessions", redactor=default_redactor())
    session.write_header(workspace=str(workspace.root), profile=config.backend.name,
                         model=config.backend.model, foundry_version=__version__)
    session.append("git_baseline", baseline.as_payload())

    registry = default_registry(recorder=session)
    dead = policy.dead_rules(registry.names())
    for rule in dead:
        console.print(f"[yellow]warning:[/yellow] rule for unknown tool {rule.tool!r} "
                      "can never match")

    vault = CredentialVault(home / "auth.json")
    source = (StaticTokenSource(vault) if config.backend.credential_source == "gateway_token"
              else ApiKeySource(vault))
    api_key = ""
    try:
        api_key = source.acquire().reveal()
    except AuthError:
        pass  # reported at first use, so `doctor` and `sessions` still work

    backend_class = {
        "openai_compat": OpenAICompatBackend,
        "responses": ResponsesBackend,
    }.get(config.backend.protocol)
    if backend_class is None:
        raise ConfigError(
            f"unknown protocol {config.backend.protocol!r}; "
            "supported: openai_compat, responses"
        )
    backend = backend_class(
        base_url=config.backend.base_url, model=config.backend.model, api_key=api_key,
        extra_headers=config.backend.headers, stream=config.backend.stream,
        client=HttpClient(read_timeout=config.backend.stream_idle_timeout_ms / 1000),
        max_retries=config.backend.request_max_retries,
    )

    context = ContextManager(
        system_prompt=base_system_prompt() + "\n\n" + permissions_paragraph(policy),
        project_doc=load_project_doc(workspace.root, trusted=trusted),
        environment=environment_paragraph(workspace.root, baseline, config.backend.model),
        max_context_tokens=backend.capabilities().max_context_tokens,
    )

    renderer = Renderer(console=console)
    sink = EventSink()
    sink.subscribe(renderer.handle)

    runtime = AgentRuntime(
        backend=backend, registry=registry, policy=policy, context=context,
        tool_ctx=ToolContext(workspace=workspace, artifacts=session.artifacts,
                             read_tracker=ReadTracker(),
                             max_output_bytes=config.max_output_bytes),
        session=session, events=sink, approval=renderer.ask_approval,
        budget=Budget(max_tool_rounds=config.max_tool_rounds,
                      max_tool_calls=config.max_tool_calls),
        model=config.backend.model,
        audit=AuditLog(home / "audit.jsonl", default_redactor()),
        git_baseline=baseline,
        credentials=source,
        persist_rule=_rule_writer(home, policy),
    )
    return Wiring(workspace=workspace, config=config, runtime=runtime,
                  renderer=renderer, session=session)


# --- commands -------------------------------------------------------------


def cmd_session(args: argparse.Namespace) -> int:
    console = Console()
    try:
        wiring = build(Path(args.workspace).resolve(), console=console)
    except FoundryError as exc:
        console.print(f"[red]{exc}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]

    wiring.renderer.show_disclosure(str(wiring.workspace.root),
                                    wiring.config.backend.model,
                                    wiring.config.mode.value)
    status = TerminalStatus.CANCELLED
    try:
        if args.task:
            outcome = wiring.runtime.run_turn(args.task)
            # Only the finish gate can produce 'completed'. A turn that simply
            # ended is unfinished work, not success.
            status = outcome.status or TerminalStatus.PARTIAL
        else:
            while True:
                try:
                    task = console.input("\n[bold cyan]foundry>[/bold cyan] ").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print()
                    break
                if not task:
                    continue
                if task in ("/exit", "/quit"):
                    break
                outcome = wiring.runtime.run_turn(task)
                if outcome.status is not None:
                    status = outcome.status
                    break
    except KeyboardInterrupt:
        wiring.runtime.cancel()
        console.print("\n[yellow]cancelled[/yellow]")
    finally:
        # A journal write can fail (full volume, a backup agent holding the
        # file); losing the exit code over it would be worse than the lost line.
        try:
            if not wiring.session._terminated:
                wiring.session.record_termination(status, "session ended")
        except OSError:
            pass
        wiring.session.close()

    return EXIT_CODES.get(status, 0)


def cmd_login(args: argparse.Namespace) -> int:
    console = Console()
    home = user_dir()
    vault = CredentialVault(home / "auth.json")
    console.print(
        "Paste an OpenAI API key. Foundry stores it encrypted for your Windows "
        "account (DPAPI) and never puts it in prompts or logs."
    )
    try:
        import getpass

        key = getpass.getpass("api key: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]cancelled[/yellow]")
        return 1
    if not key:
        console.print("[red]no key entered[/red]")
        return 1
    ApiKeySource(vault).store(key)
    console.print(f"[green]saved[/green] {home / 'auth.json'}")
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
        target = root / args.session_id / "events.jsonl"
        if not target.is_file():
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
        console.print(f"{entry.name}  [{style}]{status.value}[/{style}]")
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    """Headless: one task, no prompts, ASK resolves to DENY.

    Fail-closed is the whole point. An unattended run that silently approved
    whatever it was asked would be strictly worse than one that stops.
    """
    console = Console(stderr=True)
    try:
        wiring = build(Path(args.workspace).resolve(),
                       overrides={"mode": Mode.DONT_ASK}, console=console,
                       interactive=False)
    except FoundryError as exc:
        console.print(f"[red]{exc}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]

    wiring.runtime.approval = None  # nobody to ask
    wiring.runtime.policy.interactive = False

    if args.json:
        import json as _json

        sink_events: list[str] = []

        def as_json(event) -> None:
            payload = {"kind": getattr(event, "kind", "event")}
            for attribute in ("text", "display", "tool", "status", "reason", "summary",
                              "message", "ok"):
                value = getattr(event, attribute, None)
                if value is not None:
                    payload[attribute] = value.value if hasattr(value, "value") else value
            sink_events.append(_json.dumps(payload, ensure_ascii=False))
            print(sink_events[-1], flush=True)

        wiring.runtime.events.subscribe(as_json)

    status = TerminalStatus.FAILED
    try:
        outcome = wiring.runtime.run_turn(args.task)
        # Only the finish gate can produce 'completed'. A turn that simply
        # ended is unfinished work, not success.
        status = outcome.status or TerminalStatus.PARTIAL
        if not args.json and outcome.text:
            print(outcome.text)
    except KeyboardInterrupt:
        wiring.runtime.cancel()
        status = TerminalStatus.CANCELLED
    finally:
        try:
            if not wiring.session._terminated:
                wiring.session.record_termination(status, "headless run ended")
        except OSError:
            pass
        wiring.session.close()
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
        try:
            if not wiring.session._terminated:
                wiring.session.record_termination(TerminalStatus.CANCELLED, "recording ended")
        except OSError:
            pass
        wiring.session.close()
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

    # The two things build() hard-fails on. Reporting green while every session
    # dies immediately is worse than not having the command.
    try:
        config = load_config(home=home)
        console.print(f"config  {config.backend.model} via {config.backend.base_url} "
                      f"(mode {config.mode.value}, {len(config.rules)} permission rules)")
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

    execute = sub.add_parser("exec", help="run one task headlessly (approvals are denied)")
    execute.add_argument("task")
    execute.add_argument("--workspace", default=".")
    execute.add_argument("--json", action="store_true", help="emit the event stream as JSONL")
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
