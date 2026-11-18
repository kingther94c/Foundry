"""``foundry checkup`` (D-204): the first run on a machine whose results
leave it only by word of mouth.

Level by level -- this machine, the configuration, the network, the
protocol, a task -- each case gives its verdict in one line as it is found.
A level that cannot be checked because one before it failed is marked
skipped rather than waited out. At the end come a short summary, the
feedback code to read out, and the path of the report, kept on the machine
beside the answers the protocol level received and the task's events.
Nothing from the workspace is sent: the protocol level asks with fixed
prompts and a toy tool (D-205), and the task is done in a repository the
checkup makes (D-206).
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime
import importlib.metadata
import locale
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from rich.console import Console
from rich.markup import escape

import foundry
from foundry import __version__
from foundry.cli.setup_commands import credential_found
from foundry.cli.wiring import _PROTOCOLS, _credential_source, _guard_credentials, make_backend
from foundry.core.auth import CredentialVault
from foundry.core.checkup import (FAIL, LEVELS, OK, SKIP, WARN, Case, feedback_code,
                                  level_status, report)
from foundry.core.config import Config, load_config, managed_policy_path, user_dir
from foundry.core.errors import FoundryError
from foundry.core.httpc import _is_loopback
from foundry.core.httptap import TapClient
from foundry.core.netcheck import network_cases
from foundry.core.policy.engine import Layer
from foundry.core import taskcheck
from foundry.core.protocheck import Spent, protocol_cases
from foundry.core.redaction import default_redactor
from foundry.core.tools.git import run_git
from foundry.core.winapi import IS_WINDOWS, find_program

PROTOCOL_SHORT = {"openai_compat": "chat", "responses": "resp", "anthropic_messages": "anth"}
STYLE = {OK: "green", WARN: "yellow", FAIL: "red", SKIP: "dim"}
# The protocol level waits this long between pieces of an answer.
PROTOCOL_READ_TIMEOUT_S = 60.0
# The model list's answers after which a turn would be refused the same way.
_REFUSED = ("A401", "A403", "PX407")


class Run:
    """The cases found so far, each shown as it is added."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.cases: list[Case] = []
        self._shown_level = ""

    def _header(self, level: str) -> None:
        if level != self._shown_level:
            self.console.print(f"\n[bold]{level}  {LEVELS[level]}[/bold]")
            self._shown_level = level

    def note(self, level: str, text: str) -> None:
        """A line that is not a case: what a long step is doing."""
        self._header(level)
        self.console.print(f"[dim]  {escape(text)}[/dim]", soft_wrap=True, highlight=False)

    def add(self, case: Case) -> Case:
        # What reaches the screen and the report passes the redactor, as a
        # journal record does: an endpoint's answer is quoted in some cases.
        scrub = default_redactor().scrub
        case = dataclasses.replace(case, said=scrub(case.said), fix=scrub(case.fix))
        self._header(case.level)
        style = STYLE[case.status]
        self.console.print(f"  [{style}]{case.status:<4}[/{style}] {case.code:<7} "
                           f"{escape(case.name):<10} {escape(case.said)}",
                           soft_wrap=True, highlight=False)
        if case.fix and case.status in (WARN, FAIL):
            self.console.print(f"[dim]{' ' * 25}{escape(case.fix)}[/dim]", soft_wrap=True,
                               highlight=False)
        self.cases.append(case)
        return case

    def skip(self, level: str, why: str) -> None:
        self.add(Case(level, "all", SKIP, f"not checked: {why}"))

    def failed(self, level: str, code: str) -> bool:
        return any(case.level == level and case.code == code and case.status == FAIL
                   for case in self.cases)


# --- E: this machine ------------------------------------------------------


def source_token() -> tuple[str, str]:
    """The Foundry that runs, as the feedback code names it -- the clone's
    commit, with * when tracked files have changed, or the version of an
    installed copy -- and as the report says it."""
    package = Path(foundry.__file__).resolve().parent
    clone = package.parent.parent
    if package.parent.name == "src" and (clone / ".git").exists():
        try:
            code, head, _ = run_git(["rev-parse", "--short=7", "HEAD"], clone)
            if code == 0 and head.strip():
                _, changed, _ = run_git(["status", "--porcelain", "--untracked-files=no"],
                                        clone)
                token = head.strip() + ("*" if changed.strip() else "")
                return token, (f"Foundry {token} from {package}"
                               + (" (with local changes)" if changed.strip() else ""))
        except FoundryError:
            pass
    return f"v{__version__}", f"Foundry {__version__} from {package}"


def python_case(version=None) -> Case:
    version = tuple(version or sys.version_info)[:3]
    said = f"Python {version[0]}.{version[1]}.{version[2]} ({sys.executable})"
    if version[:2] == (3, 12):
        return Case("E", "python", OK, said)
    return Case("E", "python", WARN, said, f"PY{version[0]}{version[1]}",
                "Foundry is built and tested on Python 3.12: install it, and run with it")


def rich_case() -> Case:
    try:
        version = importlib.metadata.version("rich")
    except importlib.metadata.PackageNotFoundError:
        version = "(version unknown)"
    return Case("E", "rich", OK, f"rich {version}")


def git_case() -> Case:
    try:
        code, out, _ = run_git(["--version"], Path.cwd())
    except FoundryError:
        return Case("E", "git", FAIL, "git is not on PATH", "GIT0",
                    "install Git for Windows, or put git.exe on PATH: every session needs it")
    if code != 0:
        return Case("E", "git", FAIL, "git is on PATH but does not run", "GIT1",
                    "run git --version in this terminal to see why")
    return Case("E", "git", OK, out.strip())


def powershell_case() -> Case:
    if not IS_WINDOWS:
        return Case("E", "powershell", SKIP, "not Windows")
    shell = find_program("powershell.exe")
    if shell is None:
        return Case("E", "powershell", FAIL, "powershell.exe is not on PATH", "PS0",
                    "every command a session runs goes through it: ask why it is missing")
    try:
        done = subprocess.run([shell, "-NoProfile", "-Command",
                               "$PSVersionTable.PSVersion.ToString()"],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return Case("E", "powershell", FAIL, f"powershell.exe did not run: {exc}", "PS1",
                    "a machine's policy can block it; a session cannot run commands then")
    version = done.stdout.decode("utf-8", "replace").strip()
    if done.returncode != 0 or not version:
        return Case("E", "powershell", FAIL, "powershell.exe did not run a command", "PS1",
                    "a machine's policy can block it; a session cannot run commands then")
    return Case("E", "powershell", OK, f"Windows PowerShell {version}")


def long_paths_case() -> Case:
    if not IS_WINDOWS:
        return Case("E", "longpaths", SKIP, "not Windows")
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            enabled, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return Case("E", "longpaths", SKIP, "the setting could not be read")
    if enabled:
        return Case("E", "longpaths", OK, "paths longer than 260 characters work")
    return Case("E", "longpaths", WARN, "paths longer than 260 characters fail", "LP0",
                "only deep trees (node_modules) meet it; an administrator can set "
                "LongPathsEnabled to 1")


def console_case() -> Case:
    if IS_WINDOWS:
        import ctypes

        kernel = ctypes.windll.kernel32
        said = (f"console code page {kernel.GetConsoleOutputCP()}; the system's ANSI code "
                f"page {kernel.GetACP()} and OEM {kernel.GetOEMCP()}; Foundry writes UTF-8")
    else:
        said = f"the locale's encoding is {locale.getpreferredencoding(False)}"
    return Case("E", "console", OK, said)


def machine(run: Run, about_source: str) -> None:
    run.add(Case("E", "foundry", OK, about_source))
    for check in (python_case, rich_case, git_case, powershell_case, long_paths_case,
                  console_case):
        run.add(check())


# --- C: the configuration -------------------------------------------------


def configuration(run: Run, workspace: Path, home: Path) -> tuple[Config | None, dict[str, str]]:
    facts = {"K": "?", "M": "?", "R": "?"}
    try:
        config = load_config(workspace, home=home)
    except FoundryError as exc:
        text = str(exc)
        code = "CFG16" if "UTF-16" in text else "CFGCP" if "is not UTF-8" in text else "CFG1"
        run.add(Case("C", "config", FAIL, text, code,
                     "the message names the file and what to change; every session stops "
                     "here too"))
        return None, facts
    user_file = home / "config.toml"
    if user_file.is_file():
        run.add(Case("C", "config", OK, f"{user_file} read"))
    else:
        run.add(Case("C", "config", WARN, f"there is no {user_file}: the defaults point at "
                     "api.openai.com", "CFG0",
                     "write config.toml with the gateway's base_url, protocol and model "
                     "(README, Configuration)"))

    protocol = config.backend.protocol
    facts["R"] = PROTOCOL_SHORT.get(protocol, "?")
    if protocol in _PROTOCOLS:
        run.add(Case("C", "protocol", OK, protocol))
    else:
        run.add(Case("C", "protocol", FAIL, f"{protocol!r} is not one of "
                     f"{', '.join(sorted(_PROTOCOLS))}", "PRO0",
                     "set protocol under [backend] to the one the gateway speaks"))

    url = config.backend.base_url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        run.add(Case("C", "base_url", FAIL, f"{url!r} is not an http or https address",
                     "URL0", "set base_url under [backend] to the gateway's address"))
    elif parts.scheme == "http" and not _is_loopback(parts.hostname):
        run.add(Case("C", "base_url", WARN, f"{url} is plain http: the credential would "
                     "cross the network unencrypted", "URLHTTP",
                     "use the gateway's https address, if it has one"))
    else:
        run.add(Case("C", "base_url", OK, url))

    model = config.backend.model
    if model:
        run.add(Case("C", "model", OK, model))
    else:
        run.add(Case("C", "model", FAIL, "no model is set", "MOD0",
                     "set model under [backend] in config.toml"))

    said, present, kind = credential_found(config, home)
    facts["K"] = kind
    if present:
        run.add(Case("C", "credential", OK, said))
    else:
        run.add(Case("C", "credential", FAIL, said, "KEY0",
                     "a session cannot start without one; the checkup never shows its value"))

    managed = managed_policy_path()
    if managed.is_file():
        rules = sum(1 for rule in config.rules if rule.layer is Layer.MANAGED)
        facts["M"] = str(rules)
        run.add(Case("C", "policy", OK, f"{managed}: {rules} permission rule(s) from the "
                     "machine"))
    else:
        facts["M"] = "-"
        run.add(Case("C", "policy", OK, f"none on this machine ({managed})"))
    return config, facts


# --- N: the network -------------------------------------------------------


def _models_failure(status: int | None, body: bytes, error: FoundryError,
                    content_type: str) -> Case:
    text = body.decode("utf-8", "replace")
    if status == 401:
        return Case("N", "models", FAIL, "the endpoint does not accept the credential "
                    "(HTTP 401)", "A401",
                    "check that it is the key or token this endpoint issues, and that it "
                    "is still valid")
    if status == 403:
        return Case("N", "models", FAIL, "the endpoint refuses the credential here "
                    "(HTTP 403)", "A403",
                    "the credential is known but not allowed this: ask for access to the "
                    "endpoint and its models")
    if status == 407:
        return Case("N", "models", FAIL, "the proxy wants a sign-in (HTTP 407)", "PX407",
                    "put the proxy's user name and password in HTTPS_PROXY if it takes "
                    "Basic; Foundry cannot answer NTLM or Negotiate")
    if status in (404, 405):
        return Case("N", "models", WARN, f"the endpoint lists no models (HTTP {status})",
                    "M404", "many gateways do not; it says nothing of whether a turn works")
    looks_html = "html" in content_type.lower() or text.lstrip()[:1] == "<"
    if status is not None and status >= 400:
        quoted = " ".join(text.split())[:160]
        return Case("N", "models", FAIL, f"the endpoint answered HTTP {status}"
                    + (f": {quoted}" if quoted and not looks_html else ""), f"H{status}",
                    "HTTP 5xx is a fault on the endpoint's side; 4xx names what it "
                    "refused")
    if status is not None and looks_html:
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        named = f" ({' '.join(title.group(1).split())[:80]})" if title else ""
        return Case("N", "models", WARN, f"a web page answered, not a model list{named}",
                    "HTML", "a proxy's or a sign-in page answered in the endpoint's place: "
                    "open base_url in a browser on this machine to see it")
    if status is not None:
        return Case("N", "models", WARN, f"the answer is not a model list: {error}", "MDLJ",
                    "the endpoint answers, in a shape Foundry does not read as a list")
    if "timed out" in str(error):
        return Case("N", "models", FAIL, f"no answer: {error}", "MDLT",
                    "the endpoint took the connection but did not answer in time")
    return Case("N", "models", FAIL, f"the request failed: {error}", "MDL0",
                "the connection worked a moment ago; try again, and say if it repeats")


def models_case(backend, tap: TapClient, model: str) -> Case:
    """The endpoint's list of models, asked as /model asks it (D-067)."""
    try:
        ids = backend.list_models()
    except FoundryError as exc:
        last = tap.last
        return _models_failure(last.status if last else None, last.body if last else b"",
                               exc, last.headers.get("content-type", "") if last else "")
    if model in ids:
        return Case("N", "models", OK, f"{len(ids)} model(s) listed, {model} among them")
    shown = ", ".join(ids[:5]) + (", ..." if len(ids) > 5 else "")
    return Case("N", "models", WARN, f"{len(ids)} model(s) listed, and {model} is not among "
                f"them: {shown or 'none'}", "MDLX",
                "set model in config.toml to one of these; some gateways list fewer models "
                "than they serve")


def network(run: Run, config: Config, home: Path) -> tuple[str | None, str]:
    """The network level; then the credential, when the protocol level can
    go on, or why it cannot."""
    ca = os.environ.get("FOUNDRY_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    _, reached = network_cases(config.backend.base_url, ca_bundle=ca, said=run.add)
    if not reached:
        run.add(Case("N", "models", SKIP, "not asked: the endpoint was not reached"))
        return None, "the endpoint was not reached"
    if config.backend.protocol not in _PROTOCOLS:
        run.add(Case("N", "models", SKIP, "not asked: the protocol is not one Foundry speaks"))
        return None, "the protocol is not one Foundry speaks"
    try:
        secret = _credential_source(config, CredentialVault(home / "auth.json")).acquire()
        backend = make_backend(config, secret.reveal())
    except FoundryError as exc:
        run.add(Case("N", "models", SKIP, f"not asked: {exc}"))
        return None, "there is no credential to ask with"
    tap = TapClient(read_timeout=backend.client.read_timeout)
    backend.client = tap
    case = run.add(models_case(backend, tap, config.backend.model))
    if case.code in _REFUSED:
        return None, "the credential was refused"
    return secret.reveal(), ""


# --- P: the protocol ------------------------------------------------------


def _ask_protocol(config: Config, secret: str, spent: Spent, *, said=lambda case: None):
    """The protocol level's cases for this configuration, through its own
    adapter; what they received is kept under the protocol's short name."""
    backend = make_backend(config, secret)
    tap = TapClient(read_timeout=PROTOCOL_READ_TIMEOUT_S)
    backend.client = tap
    own = Spent()
    cases, _ = protocol_cases(backend, tap, config.backend.model, said=said, spent=own)
    short = PROTOCOL_SHORT[config.backend.protocol]
    spent.requests += own.requests
    spent.input_tokens += own.input_tokens
    spent.output_tokens += own.output_tokens
    spent.captures += [(f"{short}-{name}", exchange) for name, exchange in own.captures]
    return cases


def _another_protocol(config: Config, secret: str, protocol: str, spent: Spent) -> Case:
    """The same cases in another protocol at the same address, said as one
    case: whether the gateway speaks that one too."""
    other = copy.deepcopy(config)
    other.backend.protocol = protocol
    # What one protocol takes and another refuses is left out.
    other.backend.effort = other.backend.verbosity = other.backend.reasoning_summary = None
    short = PROTOCOL_SHORT[protocol]
    cases = _ask_protocol(other, secret, spent)
    failed = [case.code for case in cases if case.status == FAIL]
    if failed:
        return Case("P", f"also {short}", WARN, f"{protocol} at the same address: "
                    f"{', '.join(failed)}", f"X{short.upper()}",
                    "the gateway does not speak this protocol there, or not all of it")
    # It speaks it; what it warned of there is said too, or "works too"
    # would hide a ceiling ignored or a stream held back in that protocol.
    warned = [case.code for case in cases if case.status == WARN]
    return Case("P", f"also {short}", OK, f"{protocol} at the same address works too"
                + (f", warning {', '.join(warned)} there" if warned else ""))


def protocol(run: Run, config: Config, secret: str, *, all_protocols: bool) -> Spent:
    spent = Spent()
    _ask_protocol(config, secret, spent, said=run.add)
    if all_protocols:
        for other in _PROTOCOLS:
            if other != config.backend.protocol:
                run.add(_another_protocol(config, secret, other, spent))
    run.add(Case("P", "spent", OK, f"{spent.requests} request(s), {spent.input_tokens} "
                 f"token(s) in and {spent.output_tokens} out"))
    return spent


# --- T: a task -------------------------------------------------------------


def _session_environment(config: Config, home: Path, secret: str) -> dict[str, str]:
    """The session's environment: its own Foundry folder, the credential in
    the variable its configuration reads (in memory, never in a file), and
    this Foundry on its path."""
    env = dict(os.environ)
    env["FOUNDRY_HOME"] = str(home)
    source = _credential_source(config, CredentialVault(home / "auth.json"))
    env[source.env_var] = secret
    package_root = str(Path(foundry.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root,
                                                      env.get("PYTHONPATH")]))
    # The `python` the session runs the tests with is the one running this.
    env["PATH"] = os.pathsep.join(filter(None, [str(Path(sys.executable).parent),
                                                env.get("PATH")]))
    env.update({"PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"})
    return env


def task(run: Run, config: Config, secret: str, home: Path, folder: Path) -> None:
    """A session given the sample task, as exec runs one; its events kept,
    redacted, in ``folder``; the verdict from the checkup's own test run."""
    run.note("T", f"a session is fixing a sample module, in at most {taskcheck.ROUNDS} "
             "rounds: a minute or two")
    scrub = default_redactor().scrub_bytes
    with tempfile.TemporaryDirectory(prefix="foundry-checkup-",
                                     ignore_cleanup_errors=True) as scratch:
        scratch = Path(scratch)
        try:
            repo = taskcheck.make_sample(scratch)
            user_file = home / "config.toml"
            mine = user_file.read_text(encoding="utf-8-sig") if user_file.is_file() else ""
            session_home = scratch / "home"
            session_home.mkdir()
            (session_home / "config.toml").write_text(taskcheck.child_config(mine),
                                                      encoding="utf-8")
        except (OSError, UnicodeDecodeError, FoundryError) as exc:
            run.add(Case("T", "task", FAIL, f"the sample repository could not be made: {exc}",
                         "TSKS", "the temporary folder and git must work: say this line"))
            return
        ran = taskcheck.Ran()
        began = time.monotonic()
        try:
            done = subprocess.run(taskcheck.exec_argv(repo), cwd=repo, capture_output=True,
                                  stdin=subprocess.DEVNULL,
                                  env=_session_environment(config, session_home, secret),
                                  timeout=taskcheck.TIMEOUT_S)
            stdout, stderr, ran.exit_code = done.stdout, done.stderr, done.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, ran.timed_out = exc.stdout or b"", exc.stderr or b"", True
        ran.seconds = time.monotonic() - began
        ran.stderr = stderr.decode("utf-8", "replace")
        taskcheck.read_events(stdout.decode("utf-8", "replace"), ran)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "task.jsonl").write_bytes(scrub(stdout))
            (folder / "task.err").write_bytes(scrub(stderr))
        except OSError:
            pass
        run.add(taskcheck.task_case(ran, taskcheck.tests_pass(repo), taskcheck.tests_kept(repo)))


# --- the command ----------------------------------------------------------


def keep_answers(folder: Path, spent: Spent) -> bool:
    """Each answer the protocol level received, as it came, beside what was
    sent for it -- both redacted -- for the adapters' tests to be run on
    them where they were made. A request's headers were never kept."""
    scrub = default_redactor().scrub_bytes
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, exchange in spent.captures:
            (folder / f"{name}.raw").write_bytes(scrub(exchange.body))
            (folder / f"{name}.request.json").write_bytes(scrub(exchange.sent))
    except OSError:
        return False
    return True


def cmd_checkup(args: argparse.Namespace) -> int:
    console = Console()
    home = user_dir()
    workspace = Path(getattr(args, "workspace", None) or ".").resolve()
    when = datetime.datetime.now()
    console.print("[bold]foundry checkup[/bold]: what works on this machine, level by level. "
                  "Nothing from your workspace is sent anywhere.", soft_wrap=True)
    run = Run(console)
    token, about_source = source_token()
    machine(run, about_source)
    config, facts = configuration(run, workspace, home)
    folder = home / "checkups" / f"checkup-{when:%Y%m%d-%H%M%S}"
    spent = None
    if config is None or run.failed("C", "URL0"):
        why = ("the configuration could not be read" if config is None
               else "base_url is not an address")
        for level in ("N", "P", "T"):
            run.skip(level, why)
    else:
        # What the configuration passes to commands and headers is registered
        # with the redactor before anything is shown (D-153).
        _guard_credentials(config)
        secret, why = network(run, config, home)
        if secret is None:
            run.skip("P", why)
            run.skip("T", why)
        else:
            spent = protocol(run, config, secret,
                             all_protocols=getattr(args, "all_protocols", False))
            if getattr(args, "no_task", False):
                run.skip("T", "--no-task")
            elif any(case.level == "P" and case.status == FAIL for case in run.cases):
                run.skip("T", "the protocol level failed")
            elif any(case.level == "E" and case.name == "git" and case.status == FAIL
                     for case in run.cases):
                run.skip("T", "git does not run")
            else:
                task(run, config, secret, home, folder)

    code = feedback_code(token, run.cases, facts)
    about = [about_source, f"Python {platform.python_version()} on {platform.platform()}",
             f"Checked from {workspace}"]
    if spent is not None and spent.captures and keep_answers(folder, spent):
        about.append(f"What the protocol level received, as it came and redacted: {folder}")
    if (folder / "task.jsonl").is_file():
        about.append(f"The task session's events, redacted: {folder / 'task.jsonl'}")
    text = default_redactor().scrub(report(when=when.strftime("%Y-%m-%d %H:%M"), about=about,
                                           cases=run.cases, code=code))
    path: Path | None = folder.parent / f"{folder.name}.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        console.print(f"[yellow]the report could not be written: {escape(str(exc))}[/yellow]",
                      soft_wrap=True)
        path = None

    console.print("\n[bold]Summary[/bold]")
    for level, title in LEVELS.items():
        mine = [case for case in run.cases if case.level == level]
        if not mine:
            continue
        status = level_status(mine)
        codes = " ".join(case.code for case in mine if case.status in (FAIL, WARN))
        style = STYLE[status]
        console.print(f"  {level}  {title:<20} [{style}]{status:<4}[/{style}]  {codes}",
                      highlight=False)
    if path is not None:
        console.print(f"Report, kept on this machine: {escape(str(path))}", soft_wrap=True,
                      highlight=False)
    console.print("Feedback code, to read out:")
    console.print(f"  [bold]{escape(code)}[/bold]", soft_wrap=True, highlight=False)
    return 1 if any(case.status == FAIL for case in run.cases) else 0
