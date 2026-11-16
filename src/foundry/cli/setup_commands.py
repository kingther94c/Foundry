"""The commands that look at the setup rather than run a task: ``doctor``,
``policy check``, ``features``, ``memories`` and ``report``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

from rich.console import Console
from rich.markup import escape

import foundry
from foundry import __version__
from foundry.cli.render import sanitize
from foundry.cli.wiring import (
    Wiring,
    _PROTOCOLS,
    _config_args,
    _credential_source,
    _guard_credentials,
    _trusted_paths,
    make_backend,
)
from foundry.core.auth import CredentialVault
from foundry.core.config import Config, load_config, managed_policy_path, user_dir
from foundry.core.config_edit import set_feature
from foundry.core.features import ALIASES as FEATURE_ALIASES
from foundry.core.features import FEATURES, refusal
from foundry.core.features import state as feature_state
from foundry.core.memories import SESSIONS_DIR as MEMORY_SESSIONS
from foundry.core.memories import forget, load_state, run_pass
from foundry.core.memories import folder as memories_folder
from foundry.core.memories import notes as memory_notes
from foundry.core.memories import summary as memory_summary
from foundry.core.errors import ConfigError, FoundryError
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.policy.engine import Layer, PolicyEngine, Verdict, builtin_rules
from foundry.core.redaction import default_redactor
from foundry.core.tools.command import RunCommand
from foundry.core.winapi import IS_WINDOWS
from foundry.core.workspace import PathRejected, Workspace


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
        forgotten = forget(home, root)
        if forgotten is None:
            console.print(f"[yellow]a pass is updating the memories of {escape(str(root))}; "
                          "nothing was forgotten: reset them once it is done[/yellow]",
                          soft_wrap=True)
            return EXIT_CODES[TerminalStatus.FAILED]
        if forgotten:
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
                # A model wrote them: no escape sequence reaches the terminal.
                console.print(sanitize(text), markup=False, highlight=False, soft_wrap=True)
        return 0
    if str(root) not in _trusted_paths(home):
        raise ConfigError(f"memories are kept only for a workspace you trust; {root} is not "
                          "(a session there asks, and exec --trust trusts it)")
    config = load_config(root, home=home, profile=getattr(args, "profile", None))
    _guard_credentials(config)
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


def cmd_report(args: argparse.Namespace) -> int:
    from foundry.cli.report import render

    root = user_dir() / "sessions"
    if not root.is_dir():
        Console().print("[dim]no sessions recorded yet[/dim]")
        return 0
    print(render(root, as_json=args.json))
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


def _credential_line(config: Config, home: Path) -> tuple[str, bool]:
    """Where the credential a session would send comes from, and whether
    there is one -- its source and the variable's name, never the value."""
    source = _credential_source(config, CredentialVault(home / "auth.json"))
    env_var = source.env_var
    if os.environ.get(env_var):
        named = " (backend.env_key)" if config.backend.env_key else ""
        return f"credential  from the variable {env_var}{named}", True
    if config.backend.env_key:
        return (f"credential  none: {env_var}, which backend.env_key names, is not set; "
                "foundry login stores nothing for this backend"), False
    field = getattr(source, "vault_field", None) or "token"
    stored = source.vault.load() or {}
    if stored.get(field):
        return f"credential  stored by foundry login ({field}, in {home / 'auth.json'})", True
    return f"credential  none: set {env_var} or run foundry login", False


def _shown_proxy(proxy: str) -> tuple[str, bool]:
    """A proxy's address without its user name and password, and whether it
    had them."""
    parts = urllib.parse.urlsplit(proxy)
    if parts.username is None and parts.password is None:
        return proxy, False
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query,
                                    parts.fragment)), True


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
    console.print(f"home    {home}", soft_wrap=True)
    # The folder a session would work in: its repository's settings are a
    # layer of the configuration, and one that cannot be read stops a session.
    workspace = Path(getattr(args, "workspace", None) or ".").resolve()
    config = None

    # The things build() hard-fails on. Reporting green while every session dies
    # immediately is worse than not having the command. There are three, not two:
    # an unrecognised protocol also stops build() dead, and doctor used to print
    # a healthy-looking config line for one.
    try:
        config = load_config(workspace, home=home)
        console.print(f"config  {config.backend.model} via {config.backend.base_url} "
                      f"(mode {config.mode.value}, {len(config.rules)} permission rules)")
        if config.backend.protocol not in _PROTOCOLS:
            console.print(f"[red]protocol  {config.backend.protocol!r} is not one of "
                          f"{', '.join(sorted(_PROTOCOLS))}; every session would "
                          "fail to start[/red]")
            ok = False
        else:
            console.print(f"protocol  {config.backend.protocol}")
        days = config.session_retention_days
        # What the sweep at each session's start will do (D-163).
        console.print("sessions  " + ("every one kept" if days == 0 else
                                      f"kept {days} day(s) after their last write")
                      + " (session_retention_days)")
    except FoundryError as exc:
        console.print(f"[red]config  {escape(str(exc))}[/red]", soft_wrap=True)
        config = None
        ok = False

    if config is not None:
        line, present = _credential_line(config, home)
        # The auth file alone said "present" for a key the backend never
        # reads, and "not signed in" beside a key in the variable it does.
        console.print(escape(line) if present else f"[red]{escape(line)}[/red]",
                      soft_wrap=True)
        ok = ok and present
        managed = managed_policy_path()
        if managed.is_file():
            rules = sum(1 for rule in config.rules if rule.layer is Layer.MANAGED)
            console.print(f"policy  {escape(str(managed))}: {rules} permission rule(s) "
                          "from the machine",
                          soft_wrap=True)
        else:
            console.print(f"policy  none on this machine ({escape(str(managed))})",
                          soft_wrap=True)

    from foundry.core.tools.git import is_git_repository

    if is_git_repository(workspace):
        console.print(f"repo    {escape(str(workspace))}", soft_wrap=True)
    else:
        console.print(f"[red]repo    {escape(str(workspace))} is not a Git repository; "
                      "a session here would refuse to start[/red]", soft_wrap=True)
        ok = False

    # The proxy Foundry would use for the endpoint, from the variables or the
    # Windows settings as its HTTP client reads them; a password in the
    # address was printed as it was.
    from foundry.core.httpc import _proxy_for

    target = config.backend.base_url if config is not None else "https://api.openai.com/v1"
    proxy = _proxy_for(target)
    host = urllib.parse.urlsplit(target).hostname or target
    if proxy:
        address, hidden = _shown_proxy(proxy)
        console.print(f"proxy   {escape(address)} for {escape(host)}"
                      + (" (its user name and password not shown)" if hidden else ""),
                      soft_wrap=True)
    else:
        console.print(f"proxy   none for {escape(host)}", soft_wrap=True)
    ca = os.environ.get("FOUNDRY_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    console.print(f"ca      {ca or 'Windows system store'}")

    return 0 if ok else 1
