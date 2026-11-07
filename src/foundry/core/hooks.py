"""Lifecycle hooks, as codex's (D-115 to D-121).

A hook is a command line the user's own configuration names -- in
``hooks.json`` beside ``config.toml``, in a ``[hooks]`` table of it, or in
the machine's policy -- run at a point in a turn with what is happening as
one JSON object on its stdin. The names and shapes are codex's, so a hook
written for codex runs here unchanged. Ten of codex's events are run:

* ``SessionStart``, at a session's first prompt (``startup``, or ``resume``)
  and after a compaction (``compact``): a hook may add a note, or stop the
  prompt or the task.
* ``UserPromptSubmit``, before a prompt is sent: a hook may stop it, so
  the model never sees it, or add a note to it.
* ``PreToolUse``, before a tool call reaches policy: a hook may refuse the
  call, rewrite its input, or add a note for the model. It never allows what
  policy would not: a rewritten call is checked from the start, breaker,
  rules and approval included.
* ``PermissionRequest``, when policy would ask the user about a call and
  someone could be asked: a hook may answer for them, allowing the call
  once or denying it; a denial wins. It is asked only what the user would
  be, so it never allows what policy refuses.
* ``PostToolUse``, after a tool gave its result: a hook may put its own
  feedback in place of the result, or add a note.
* ``Stop``, when the model is done -- it answered without a call, or called
  ``finish``: a hook may send the task on, its reason the next prompt.
* ``Interrupt``, when a task is stopped, and ``SessionEnd``, when a session
  closes: told, and given a second (three at most) to act; neither decides.
* ``PreCompact`` and ``PostCompact``, around a compaction (``manual`` or
  ``auto``): a hook may stop it before, or the task after.

A hook may instead name a tool of an MCP server the session already has
(``mcp_tool``): the tool is called with arguments filled in from the event,
and what it returns is read as a command hook's output.

Like a notify program or an MCP server, a hook is a program Foundry starts,
so a repository's configuration may not name one. A hook that fails, times
out or answers in a shape it does not know is a warning, and the call goes
on as codex lets it: a hook is a guardrail, and policy is the boundary.

A hook marked ``async`` runs in the background, as codex's: the work goes on
without it, and what it says -- a note for the model, a message for the
user -- is taken at the next safe point, before the task's next model
request or with the next prompt. It decides nothing. SessionEnd hooks run
in step whatever they say, and those still running when the session closes
are ended.
"""

from __future__ import annotations

import difflib
import functools
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from foundry.core.errors import ConfigError, ToolError
from foundry.core.winapi import IS_WINDOWS, child_environment, decode_output, find_program

PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
USER_PROMPT_SUBMIT = "UserPromptSubmit"
STOP = "Stop"
SESSION_START = "SessionStart"
SESSION_END = "SessionEnd"
INTERRUPT = "Interrupt"
PRE_COMPACT = "PreCompact"
POST_COMPACT = "PostCompact"
PERMISSION_REQUEST = "PermissionRequest"
HOOK_EVENTS = (PRE_TOOL_USE, PERMISSION_REQUEST, POST_TOOL_USE, USER_PROMPT_SUBMIT, STOP,
               SESSION_START, SESSION_END, INTERRUPT, PRE_COMPACT, POST_COMPACT)
# Events codex gives no matcher: every hook of one runs, whatever its
# matcher says. SessionStart's matcher is tried on its source, SessionEnd's
# on its reason, which is always "other", PreCompact's and PostCompact's on
# what started the compaction, "manual" or "auto".
UNMATCHED_EVENTS = frozenset({USER_PROMPT_SUBMIT, STOP, INTERRUPT})
SESSION_END_REASON = "other"
# Where exit code 2 is a decision, its reason on stderr; elsewhere it is a
# code like any other but 0.
_EXIT_2_EVENTS = frozenset({PRE_TOOL_USE, PERMISSION_REQUEST, POST_TOOL_USE,
                            USER_PROMPT_SUBMIT, STOP})
# codex's other events: named, so a hooks.json carried over says which of
# its hooks would not run, rather than reading as a typo.
CODEX_EVENTS = frozenset({
    "PreModelRequest", "PostModelRequest", "SubagentStart", "SubagentStop",
})
DEFAULT_TIMEOUT_S = 600
# SessionEnd and Interrupt hooks hold up a session that is closing or a
# user who stopped it: a second unless set, three at most, as codex's.
SHORT_EVENTS = frozenset({SESSION_END, INTERRUPT})
SHORT_TIMEOUT_S = 1
SHORT_MAX_TIMEOUT_S = 3
# What a hook's additionalContext may take before it is cut, in codex's
# tokens, counted here at four characters a token. 0 means no cut.
DEFAULT_CONTEXT_TOKENS = 2_500
CHARS_PER_TOKEN = 4
HOOKS_FILE = "hooks.json"

_FILE_KEYS = frozenset({"description", "hooks"})
_GROUP_KEYS = frozenset({"matcher", "hooks"})
_HANDLER_KEYS = frozenset({"type", "command", "commandWindows", "command_windows", "timeout",
                           "async", "statusMessage", "additionalContextLimit"})
_MCP_KEYS = frozenset({"type", "server", "tool", "input", "timeout", "async", "statusMessage",
                       "additionalContextLimit"})
# ${field.nested} in an mcp_tool hook's input, as codex fills it in.
_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")
# A matcher of names and `|` alone is a list of names, as codex reads it;
# anything else is a regular expression, searched for.
_NAMES_ONLY = re.compile(r"[A-Za-z0-9_|]*\Z")

# A hook sees codex's name for a tool, so a script written for codex's
# `Bash` sees a command as it expects; a matcher may use Foundry's name too.
_HOOK_NAMES = {"run_command": "Bash"}
_MATCHER_ALIASES = {"run_command": ("run_command",), "apply_patch": ("Edit", "Write")}
# Foundry's modes as codex's hooks name a permission mode.
PERMISSION_MODES = {"default": "default", "accept_edits": "acceptEdits", "plan": "plan",
                    "dont_ask": "dontAsk"}


def events_named() -> str:
    """The events Foundry runs, as a sentence names them."""
    return f"{', '.join(HOOK_EVENTS[:-1])} and {HOOK_EVENTS[-1]}"


@functools.lru_cache(maxsize=256)
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


@dataclass(frozen=True, slots=True)
class Hook:
    event: str
    matcher: str          # as written; "" or "*" match every call
    command: str          # the command line, commandWindows taken on Windows
    timeout_s: int = DEFAULT_TIMEOUT_S
    status_message: str = ""
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    # codex's `async`: run in the background (D-120); what such a hook says
    # informs and never decides.
    background: bool = False
    source: str = ""      # the file it was read from
    # codex's `mcp_tool` handler (D-121): the server, the tool, and its
    # arguments as JSON, with ${field.nested} filled in from the event.
    mcp_server: str = ""
    mcp_tool: str = ""
    mcp_input: str = ""

    def matches(self, names: tuple[str, ...]) -> bool:
        if self.matcher in ("", "*"):
            return True
        if _NAMES_ONLY.match(self.matcher):
            choices = self.matcher.split("|")
            return any(name in choices for name in names)
        return any(_regex(self.matcher).search(name) for name in names)


def hook_tool_name(tool: str) -> str:
    return _HOOK_NAMES.get(tool, tool)


def matcher_names(tool: str) -> tuple[str, ...]:
    """The names a matcher is tried against: the one a hook sees first."""
    return (hook_tool_name(tool), *_MATCHER_ALIASES.get(tool, ()))


def hook_tool_input(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """A call's input as a hook sees it: codex's ``command`` for a patch, as
    for a shell command; the arguments themselves otherwise."""
    if tool == "apply_patch":
        return {"command": args.get("patch", "")}
    return dict(args)


def rewritten_args(tool: str, args: dict[str, Any], updated: Any) -> dict[str, Any] | None:
    """The arguments a PreToolUse hook's ``updatedInput`` asks for, or None
    when it is not the shape codex gives one: an object, with a string
    ``command`` for a shell command or a patch."""
    if not isinstance(updated, dict):
        return None
    if tool in ("run_command", "apply_patch") and not isinstance(updated.get("command"), str):
        return None
    if tool == "apply_patch":
        return {"patch": updated["command"]}
    if tool == "run_command":
        return {**args, **updated}
    return dict(updated)


# --- configuration ------------------------------------------------------------


def _event_refused(event: str) -> str:
    if event == "state":
        return ("hooks.state is codex's record of the hooks you reviewed in its /hooks; "
                "Foundry runs the hooks your own files name and keeps no such record, so "
                "remove a hook you do not want run")
    if event in ("managed_dir", "windows_managed_dir"):
        return (f"hooks.{event} belongs to codex's requirements.toml; the machine's hooks "
                "go in Foundry's own policy.toml or the hooks.json beside it")
    if event in CODEX_EVENTS:
        return f"Foundry does not run {event} hooks yet; it runs {events_named()}"
    close = difflib.get_close_matches(event, [*HOOK_EVENTS, *sorted(CODEX_EVENTS)], n=1)
    return f"no hook event {event!r}" + (f"; did you mean {close[0]!r}?" if close else "")


def _text(value: Any, where: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value or (not empty and not value.strip()):
        raise ConfigError(f"{where} must be {'a string' if empty else 'a non-empty string'}")
    return value


def _handler(entry: Any, event: str, matcher: str, where: str, source: str) -> Hook:
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table, with type = \"command\" and its command")
    kind = entry.get("type")
    if kind in ("prompt", "agent"):
        raise ConfigError(f"{where}: a {kind} hook is skipped by codex too; remove it")
    if kind not in ("command", "mcp_tool"):
        raise ConfigError(f"{where}.type must be \"command\" or \"mcp_tool\"")
    keys = _HANDLER_KEYS if kind == "command" else _MCP_KEYS
    unknown = sorted(set(entry) - keys)
    if unknown:
        close = difflib.get_close_matches(unknown[0], sorted(keys), n=1)
        raise ConfigError(f"{where}: no setting {unknown[0]!r}"
                          + (f"; did you mean {close[0]!r}?" if close else
                             f" (a hook takes {', '.join(sorted(keys))})"))
    mcp: dict[str, str] = {}
    if kind == "mcp_tool":
        if event == SESSION_END:
            raise ConfigError(f"{where}: a SessionEnd hook runs a command; codex runs no "
                              "mcp_tool hook there either")
        template = entry.get("input", {})
        if not isinstance(template, dict):
            raise ConfigError(f"{where}.input must be a table of the tool's arguments")
        mcp = {"mcp_server": _text(entry.get("server"), f"{where}.server"),
               "mcp_tool": _text(entry.get("tool"), f"{where}.tool"),
               "mcp_input": json.dumps(template, ensure_ascii=False)}
        command = f"mcp_tool {mcp['mcp_server']}/{mcp['mcp_tool']}"
    else:
        command = _text(entry.get("command"), f"{where}.command")
        if "commandWindows" in entry and "command_windows" in entry:
            raise ConfigError(f"{where}: commandWindows and command_windows are one setting; "
                              "give one")
        windows = entry.get("commandWindows", entry.get("command_windows"))
        if windows is not None:
            windows = _text(windows, f"{where}.commandWindows")
            if IS_WINDOWS:
                command = windows
    short = event in SHORT_EVENTS
    timeout = entry.get("timeout", SHORT_TIMEOUT_S if short else DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise ConfigError(f"{where}.timeout is in seconds, a whole number of at least 1")
    if short:
        # Cut to codex's cap, as codex cuts it, so its files load here.
        timeout = min(timeout, SHORT_MAX_TIMEOUT_S)
    background = entry.get("async", False)
    if not isinstance(background, bool):
        raise ConfigError(f"{where}.async must be true or false")
    if event == SESSION_END or mcp:
        # codex runs these in step whatever they say: a session closing
        # waits for nothing after them, and an MCP tool hook is one call.
        background = False
    status = _text(entry.get("statusMessage", ""), f"{where}.statusMessage", empty=True)
    tokens = entry.get("additionalContextLimit", DEFAULT_CONTEXT_TOKENS)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ConfigError(f"{where}.additionalContextLimit is a number of tokens, 0 or more "
                          "(0: no limit)")
    return Hook(event=event, matcher=matcher, command=command, timeout_s=timeout,
                status_message=status.strip(), context_tokens=tokens, background=background,
                source=source, **mcp)


def fill_input(template: Any, event: dict[str, Any]) -> Any:
    """An mcp_tool hook's arguments, as codex fills them in: a string that
    is one ``${field.nested}`` becomes that field's value, whatever its type;
    one within other text, that text with the value written in (a string as
    it is, anything else as JSON); objects and arrays throughout. A field the
    event does not have is a LookupError naming it."""
    if isinstance(template, dict):
        return {key: fill_input(value, event) for key, value in template.items()}
    if isinstance(template, list):
        return [fill_input(value, event) for value in template]
    if not isinstance(template, str):
        return template
    found = list(_PLACEHOLDER.finditer(template))
    if not found:
        return template
    if len(found) == 1 and found[0].span() == (0, len(template)):
        return _event_field(event, found[0].group(1))
    parts: list[str] = []
    end = 0
    for match in found:
        value = _event_field(event, match.group(1))
        parts += [template[end:match.start()],
                  value if isinstance(value, str)
                  else json.dumps(value, ensure_ascii=False, separators=(",", ":"))]
        end = match.end()
    return "".join(parts) + template[end:]


def _event_field(event: dict[str, Any], path: str) -> Any:
    value: Any = event
    for name in path.split("."):
        if not isinstance(value, dict) or name not in value:
            raise LookupError(f"hook input placeholder `${{{path}}}` was not found")
        value = value[name]
    return value


def parse_hooks(events: Any, source: str) -> tuple[Hook, ...]:
    """The hooks of one table: ``[hooks]`` of a config file, or the
    ``hooks`` object of a hooks.json. Every mistake is an error naming where
    it is (D-091): a hook skipped without a word looks like one that runs."""
    if not isinstance(events, dict):
        raise ConfigError(f"{source}: hooks must be a table of events, as [[hooks.PreToolUse]]")
    hooks: list[Hook] = []
    for event, groups in events.items():
        if event not in HOOK_EVENTS:
            raise ConfigError(f"{source}: {_event_refused(event)}")
        if not isinstance(groups, list):
            raise ConfigError(f"{source}: hooks.{event} must be a list of matcher groups, as "
                              f"[[hooks.{event}]]")
        for index, group in enumerate(groups):
            where = f"{source}: hooks.{event}[{index}]"
            if not isinstance(group, dict):
                raise ConfigError(f"{where} must be a table, with a matcher and its hooks")
            unknown = sorted(set(group) - _GROUP_KEYS)
            if unknown:
                raise ConfigError(f"{where}: no setting {unknown[0]!r} (a matcher group takes "
                                  "matcher and hooks)")
            matcher = group.get("matcher")
            matcher = "" if matcher is None else _text(matcher, f"{where}.matcher", empty=True)
            if matcher not in ("", "*") and not _NAMES_ONLY.match(matcher):
                try:
                    _regex(matcher)
                except re.error as exc:
                    raise ConfigError(f"{where}.matcher {matcher!r} is not a regular "
                                      f"expression: {exc}") from exc
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                raise ConfigError(f"{where}.hooks must be a list, as [[hooks.{event}.hooks]]")
            hooks.extend(_handler(entry, event, matcher, f"{where}.hooks[{number}]", source)
                         for number, entry in enumerate(handlers))
    return tuple(hooks)


def read_hooks_file(path: Path) -> tuple[Hook, ...]:
    """A hooks.json, as codex's: ``{"description": ..., "hooks": {...}}``.
    None there is none."""
    if not path.is_file():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f'{path}: a hooks file is one JSON object, as {{"hooks": {{...}}}}')
    unknown = sorted(set(data) - _FILE_KEYS)
    if unknown:
        raise ConfigError(f"{path}: no setting {unknown[0]!r} (a hooks file takes description "
                          "and hooks)")
    if "description" in data and not isinstance(data["description"], str):
        raise ConfigError(f"{path}: description must be a string")
    return parse_hooks(data.get("hooks", {}), str(path))


# --- running ----------------------------------------------------------------


@dataclass(slots=True)
class HookRun:
    hook: Hook
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""       # it could not be started, timed out or was stopped
    duration_ms: int = 0


@dataclass(slots=True)
class HookSet:
    """The hooks a session runs, and where and how they run."""
    hooks: tuple[Hook, ...] = ()
    cwd: Path | None = None
    env_policy: Any = None              # the user's [shell_environment_policy]
    environ: dict[str, str] | None = None   # the environment to start from
    # The session's MCP clients, by server, for mcp_tool hooks (D-121).
    mcp: dict[str, Any] | None = None
    # Background hooks (D-120): their runs as they end, for the runtime to
    # take at its next safe point; their threads; what ends those running.
    _finished: list = field(default_factory=list, compare=False, repr=False)
    _lock: Any = field(default_factory=threading.Lock, compare=False, repr=False)
    _threads: list = field(default_factory=list, compare=False, repr=False)
    _halt: Any = field(default_factory=threading.Event, compare=False, repr=False)

    def matching(self, event: str, names: tuple[str, ...]) -> list[Hook]:
        return [hook for hook in self.hooks if hook.event == event
                and (event in UNMATCHED_EVENTS or hook.matches(names))]

    def run(self, hooks: list[Hook], payload: dict[str, Any], *,
            cancelled: Callable[[], bool] | None = None) -> list[HookRun]:
        """Run ``hooks`` with ``payload`` on their stdin, all at once as
        codex starts them, so one that hangs does not keep another from
        starting; their runs in the order the hooks are. KeyboardInterrupt
        when the session is stopped meanwhile, once each is ended. A
        background hook is started and not waited for: its run is had from
        :meth:`finished` once it ends (D-120)."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for hook in hooks:
            if hook.background:
                self._start(hook, data)
        hooks = [hook for hook in hooks if not hook.background]
        if not hooks:
            return []
        if len(hooks) == 1:
            return [self._run_one(hooks[0], data, cancelled)]
        stop = threading.Event()

        def halted() -> bool:
            return stop.is_set() or (cancelled is not None and cancelled())

        runs: list[HookRun | None] = [None] * len(hooks)

        def work(index: int, hook: Hook) -> None:
            try:
                runs[index] = self._run_one(hook, data, halted)
            except KeyboardInterrupt:
                runs[index] = HookRun(hook, error="stopped")

        threads = [threading.Thread(target=work, args=(index, hook), daemon=True)
                   for index, hook in enumerate(hooks)]
        for thread in threads:
            thread.start()
        try:
            # Joined in slices: a plain join would hold Ctrl+C until the
            # slowest hook ended, up to its timeout.
            for thread in threads:
                while thread.is_alive():
                    thread.join(0.2)
        except KeyboardInterrupt:
            stop.set()
            for thread in threads:
                thread.join(5)
            raise
        if cancelled is not None and cancelled():
            raise KeyboardInterrupt
        return [run if run is not None else HookRun(hook, error="stopped")
                for run, hook in zip(runs, hooks)]

    def _call_tool(self, hook: Hook, data: bytes,
                   cancelled: Callable[[], bool] | None) -> HookRun:
        """An mcp_tool hook (D-121), as codex runs one: the tool called on a
        server the session already has -- not started or restarted for it,
        and not asked about, since the user named it -- within the hook's
        timeout or the server's, the shorter. Its text is read as a command
        hook's stdout; an error result, or no such server, a failure.
        KeyboardInterrupt when the session is stopped meanwhile."""
        from foundry.core.errors import Cancelled
        from foundry.core.mcp import McpError

        began = time.monotonic()

        def ended(**fields: Any) -> HookRun:
            return HookRun(hook, duration_ms=int((time.monotonic() - began) * 1000), **fields)

        client = (self.mcp or {}).get(hook.mcp_server)
        if client is None or not client.running:
            return ended(error=f"could not be run: the MCP server {hook.mcp_server!r} is not "
                               "connected")
        try:
            arguments = fill_input(json.loads(hook.mcp_input), json.loads(data))
        except LookupError as exc:
            return ended(error=f"could not be run: {exc.args[0]}")
        try:
            result = client.request(
                "tools/call", {"name": hook.mcp_tool, "arguments": arguments},
                timeout=min(hook.timeout_s, client.server.tool_timeout_s), cancelled=cancelled)
        except Cancelled:
            raise KeyboardInterrupt from None
        except McpError as exc:
            return ended(error=f"could not be run: {exc}")
        content = result.get("content")
        text = "\n".join(item["text"] for item in content if isinstance(item, dict)
                         and item.get("type") == "text" and isinstance(item.get("text"), str)
                         ) if isinstance(content, list) else ""
        if result.get("isError") is True:
            return ended(error=f"returned an error: {text}")
        return ended(exit_code=0, stdout=text)

    def _start(self, hook: Hook, data: bytes) -> None:
        """Run a background hook on a thread of its own. Only the session's
        close ends it, not a stopped task: codex lets it finish."""
        def work() -> None:
            try:
                run = self._run_one(hook, data, self._halt.is_set)
            except KeyboardInterrupt:
                return
            if self._halt.is_set():
                return       # ended as the session closed: no one to tell
            with self._lock:
                self._finished.append(run)

        thread = threading.Thread(target=work, daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def finished(self) -> list[HookRun]:
        """The background hooks' runs that ended since last asked, in the
        order they ended."""
        with self._lock:
            runs = list(self._finished)
            self._finished.clear()
        return runs

    def settle(self, timeout_s: float) -> bool:
        """Wait up to ``timeout_s`` for the background hooks running to end;
        whether they all have."""
        deadline = time.monotonic() + timeout_s
        for thread in list(self._threads):
            thread.join(max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in self._threads)

    def close(self) -> None:
        """End the background hooks still running, as the session closes:
        what they would say has no one left to reach."""
        self._halt.set()
        self.settle(5)

    def _run_one(self, hook: Hook, data: bytes,
                 cancelled: Callable[[], bool] | None) -> HookRun:
        if hook.mcp_server:
            return self._call_tool(hook, data, cancelled)
        from foundry.core.tools.command import run_program

        env = child_environment(self.environ, policy=self.env_policy)
        # A program the command line names is looked for on PATH and not
        # first in the workspace, where a clone could have put one (D-049).
        env["NoDefaultCurrentDirectoryInExePath"] = "1"
        if IS_WINDOWS:
            shell = find_program("cmd.exe", env)
            if shell is None:
                return HookRun(hook, error="cmd.exe was not found on PATH")
            # codex's `cmd /C "<command>"`, with /S so the outer quotes are
            # the ones removed whatever the command holds, and /D so no
            # AutoRun command of the registry's runs first.
            argv: list[str] | str = f'"{shell}" /D /S /C "{hook.command}"'
            executable: str | None = shell
        else:  # pragma: no cover - Windows is the supported platform
            argv, executable = ["/bin/sh", "-c", hook.command], None
        try:
            result = run_program(argv, cwd=str(self.cwd or Path.cwd()),
                                 timeout_s=hook.timeout_s, env=env, cancelled=cancelled,
                                 stdin_data=data, executable=executable)
        except (OSError, ToolError) as exc:
            return HookRun(hook, error=f"could not be started: {exc}")
        stdout = decode_output(result.stdout).text
        stderr = decode_output(result.stderr).text
        if result.timed_out:
            return HookRun(hook, stdout=stdout, stderr=stderr,
                           error=f"timed out after {hook.timeout_s}s",
                           duration_ms=result.duration_ms)
        return HookRun(hook, exit_code=result.exit_code, stdout=stdout, stderr=stderr,
                       duration_ms=result.duration_ms)


# --- what the hooks said ------------------------------------------------------


@dataclass(slots=True)
class HookVerdict:
    """What the hooks of one event said, together."""
    # PreToolUse: why the call is refused. PermissionRequest: why it is
    # denied. PostToolUse: what the model is given in place of the result.
    # UserPromptSubmit: why the prompt is not sent. Stop: what the task goes
    # on with.
    block: list[str] = field(default_factory=list)
    # PermissionRequest: a hook allowed the call, which a denial outranks.
    allow: bool = False
    # UserPromptSubmit, Stop, SessionStart, PreCompact and PostCompact: the
    # stopReason of each `continue: false`, "" for one without.
    stops: list[str] = field(default_factory=list)
    rewrites: list[dict[str, Any]] = field(default_factory=list)   # PreToolUse
    context: list[str] = field(default_factory=list)               # for the model
    messages: list[str] = field(default_factory=list)              # systemMessage
    failures: list[str] = field(default_factory=list)              # warnings
    records: list[dict[str, Any]] = field(default_factory=list)    # for the journal


_UNIVERSAL = {"continue": bool, "stopReason": str, "systemMessage": str, "suppressOutput": bool}


def _json_object(stdout: str) -> tuple[dict[str, Any] | None, bool]:
    """The object a hook printed, and whether what it printed looked like
    JSON without being an object: that is a broken answer; plain text is
    not an answer at all."""
    text = stdout.strip()
    if not text:
        return None, False
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return value, False
    return None, text.startswith(("{", "["))


def _typed(obj: dict[str, Any], kinds: dict[str, type]) -> bool:
    """Whether each field present has its type; a null is a field left out."""
    return all(obj.get(name) is None or isinstance(obj[name], kind)
               for name, kind in kinds.items())


def _reason(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _short(command: str) -> str:
    return command if len(command) <= 60 else command[:57] + "..."


def _context(hook: Hook, text: str) -> str:
    limit = hook.context_tokens * CHARS_PER_TOKEN
    if not limit or len(text) <= limit:
        return text
    return (text[:limit] + f"\n[... cut: the hook gave {len(text)} characters, more than "
            f"its additionalContextLimit of about {hook.context_tokens} tokens]")


def _feedback(text: str) -> str:
    """What a hook gives the model in place of a result, or as the next
    prompt: cut at codex's default, whatever the hook's
    additionalContextLimit, which is for notes only."""
    limit = DEFAULT_CONTEXT_TOKENS * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    return (text[:limit] + f"\n[... cut: the hook gave {len(text)} characters, more than "
            f"about {DEFAULT_CONTEXT_TOKENS} tokens]")


class _Reading:
    """One run read: what it decided and what is said of it."""

    def __init__(self, run: HookRun, event: str, verdict: HookVerdict) -> None:
        self.run, self.event, self.verdict = run, event, verdict
        self.outcome = "completed"
        self.control = not run.hook.background
        self.plain = ""     # text it printed that is not JSON

    def fail(self, why: str) -> None:
        self.outcome = "failed"
        self.verdict.failures.append(f"`{_short(self.run.hook.command)}` {why}")

    def done(self) -> None:
        record: dict[str, Any] = {"command": self.run.hook.command,
                                  "source": self.run.hook.source,
                                  "exit_code": self.run.exit_code,
                                  "duration_ms": self.run.duration_ms,
                                  "outcome": self.outcome}
        if self.run.error:
            record["error"] = self.run.error
        self.verdict.records.append(record)

    def start(self) -> dict[str, Any] | None:
        """The answer to read, or None when there is none to act on: a run
        that failed, a code other than 0 and 2, or nothing printed."""
        run = self.run
        if run.error:
            self.fail(run.error)
            return None
        if run.exit_code == 2 and self.control and self.event in _EXIT_2_EVENTS:
            reason = run.stderr.strip()
            if not reason:
                self.fail("exited with code 2 but did not write a reason to stderr")
                return None
            self.decided(reason)
            return None
        if run.exit_code != 0:
            self.fail("exited without a status code" if run.exit_code is None
                      else f"exited with code {run.exit_code}")
            return None
        answer, broken = _json_object(run.stdout)
        if broken:
            self.fail(f"returned invalid {self.event} JSON output")
        elif answer is None:
            self.plain = run.stdout.strip()
        return answer

    def decided(self, reason: str) -> None:
        self.outcome = "blocked"
        self.verdict.block.append(_feedback(reason))

    def failed(self) -> bool:
        """Whether the run failed, said as codex says it for an event that
        reads no exit code 2: why, from its stderr, or the code."""
        run = self.run
        if run.error:
            self.fail(run.error)
        elif run.exit_code is None:
            self.fail("exited without a status code")
        elif run.exit_code != 0:
            self.fail(f"failed: {run.stderr.strip()}" if run.stderr.strip()
                      else f"exited with code {run.exit_code}")
        else:
            return False
        return True


def _hook_output(answer: dict[str, Any], kinds: dict[str, type]) -> dict[str, Any] | None:
    output = answer.get("hookSpecificOutput")
    if output is None:
        return {}
    if not isinstance(output, dict) or not _typed(output, kinds):
        return None
    return output


def read_pre_tool_use(runs: list[HookRun]) -> HookVerdict:
    """PreToolUse answers, as codex reads them: a refusal (``deny`` with a
    reason, codex's older ``block``, or exit code 2 and a reason on stderr),
    a rewrite (``allow`` with ``updatedInput``), a note for the model."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, PRE_TOOL_USE, verdict)
        answer = reading.start()
        if answer is not None:
            _pre_answer(reading, answer)
        reading.done()
    return verdict


def _pre_answer(reading: _Reading, answer: dict[str, Any]) -> None:
    verdict = reading.verdict
    output = _hook_output(answer, {"hookEventName": str, "additionalContext": str,
                                   "permissionDecision": str,
                                   "permissionDecisionReason": str})
    if (output is None or not _typed(answer, {**_UNIVERSAL, "decision": str, "reason": str})
            or output.get("permissionDecision") not in (None, "allow", "deny", "ask")
            or answer.get("decision") not in (None, "approve", "block")):
        reading.fail("returned invalid PreToolUse JSON output")
        return
    if answer.get("systemMessage"):
        verdict.messages.append(answer["systemMessage"])
    decision = output.get("permissionDecision")
    reason = output.get("permissionDecisionReason")
    updated = output.get("updatedInput")
    specific = decision is not None or reason is not None or updated is not None
    if answer.get("continue") is False:
        invalid = "returned unsupported continue:false"
    elif answer.get("stopReason") is not None:
        invalid = "returned unsupported stopReason"
    elif answer.get("suppressOutput"):
        invalid = "returned unsupported suppressOutput"
    elif specific:
        if updated is not None and decision != "allow":
            invalid = "returned updatedInput without permissionDecision:allow"
        elif decision == "allow" and updated is None:
            invalid = "returned unsupported permissionDecision:allow"
        elif decision == "ask":
            invalid = "returned unsupported permissionDecision:ask"
        elif decision == "deny" and not _reason(reason):
            invalid = "returned permissionDecision:deny without a non-empty reason"
        elif decision is None and reason is not None:
            invalid = "returned permissionDecisionReason without permissionDecision"
        else:
            invalid = ""
    elif answer.get("decision") == "approve":
        invalid = "returned unsupported decision:approve"
    elif answer.get("decision") == "block" and not _reason(answer.get("reason")):
        invalid = "returned decision:block without a non-empty reason"
    elif answer.get("decision") is None and answer.get("reason") is not None:
        invalid = "returned reason without decision"
    else:
        invalid = ""
    if output.get("additionalContext") and (not reading.control or not invalid):
        verdict.context.append(_context(reading.run.hook, output["additionalContext"]))
    if not reading.control:
        return
    if invalid:
        reading.fail(invalid)
    elif specific and decision == "deny":
        reading.decided(_reason(reason))
    elif not specific and answer.get("decision") == "block":
        reading.decided(_reason(answer.get("reason")))
    elif updated is not None:
        reading.outcome = "rewrote"
        verdict.rewrites.append(updated)


def read_permission_request(runs: list[HookRun]) -> HookVerdict:
    """PermissionRequest answers, as codex reads them: ``allow`` or ``deny``
    (with a message, or exit code 2 and a reason on stderr) for a call the
    user would be asked about, or no decision. Plain text is not read."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, PERMISSION_REQUEST, verdict)
        answer = reading.start()
        if answer is not None:
            _permission_answer(reading, answer)
        reading.done()
    return verdict


_DECISION = {"behavior": str, "message": str, "interrupt": bool}


def _permission_answer(reading: _Reading, answer: dict[str, Any]) -> None:
    verdict = reading.verdict
    output = _hook_output(answer, {"hookEventName": str, "decision": dict})
    decision = (output or {}).get("decision")
    if (output is None or not _typed(answer, _UNIVERSAL)
            or (decision is not None and (not _typed(decision, _DECISION)
                                          or decision.get("behavior") not in ("allow", "deny")))):
        reading.fail("returned invalid PermissionRequest JSON output")
        return
    if answer.get("systemMessage"):
        verdict.messages.append(answer["systemMessage"])
    if not reading.control:
        return
    decision = decision or {}
    if answer.get("continue") is False:
        reading.fail("returned unsupported continue:false")
    elif answer.get("stopReason") is not None:
        reading.fail("returned unsupported stopReason")
    elif answer.get("suppressOutput"):
        reading.fail("returned unsupported suppressOutput")
    elif decision.get("updatedInput") is not None:
        reading.fail("returned unsupported updatedInput")
    elif decision.get("updatedPermissions") is not None:
        reading.fail("returned unsupported updatedPermissions")
    elif decision.get("interrupt"):
        reading.fail("returned unsupported interrupt:true")
    elif decision.get("behavior") == "deny":
        reading.decided(_reason(decision.get("message"))
                        or f"{PERMISSION_REQUEST} hook denied approval")
    elif decision.get("behavior") == "allow":
        reading.outcome = "allowed"
        verdict.allow = True


def read_post_tool_use(runs: list[HookRun]) -> HookVerdict:
    """PostToolUse answers, as codex reads them: ``block`` with a reason, or
    exit code 2 and a reason on stderr, puts the reason in place of the
    result; ``continue: false`` its stop text; a note for the model."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, POST_TOOL_USE, verdict)
        answer = reading.start()
        if answer is not None:
            _post_answer(reading, answer)
        reading.done()
    return verdict


def _post_answer(reading: _Reading, answer: dict[str, Any]) -> None:
    verdict = reading.verdict
    output = _hook_output(answer, {"hookEventName": str, "additionalContext": str})
    if (output is None or not _typed(answer, {**_UNIVERSAL, "decision": str, "reason": str})
            or answer.get("decision") not in (None, "block")):
        reading.fail("returned invalid PostToolUse JSON output")
        return
    if answer.get("systemMessage"):
        verdict.messages.append(answer["systemMessage"])
    block = answer.get("decision") == "block"
    stopped = answer.get("continue") is False
    if answer.get("suppressOutput"):
        invalid = "returned unsupported suppressOutput"
    elif output.get("updatedMCPToolOutput") is not None:
        invalid = "returned unsupported updatedMCPToolOutput"
    elif block and not _reason(answer.get("reason")):
        invalid = "returned decision:block without a non-empty reason"
    elif not block and not stopped and answer.get("reason") is not None:
        invalid = "returned reason without decision"
    else:
        invalid = ""
    if output.get("additionalContext") and (not reading.control or not invalid):
        verdict.context.append(_context(reading.run.hook, output["additionalContext"]))
    if not reading.control:
        return
    if stopped:
        reading.outcome = "stopped"
        stop = _reason(answer.get("stopReason")) or "PostToolUse hook stopped execution"
        verdict.block.append(_feedback(_reason(answer.get("reason")) or stop))
    elif invalid:
        reading.fail(invalid)
    elif block:
        reading.decided(_reason(answer.get("reason")))


def read_user_prompt_submit(runs: list[HookRun]) -> HookVerdict:
    """UserPromptSubmit answers, as codex reads them: ``block`` with a
    reason, exit code 2 and a reason on stderr, or ``continue: false`` stops
    the prompt; ``additionalContext``, or plain text, is a note for the
    model."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, USER_PROMPT_SUBMIT, verdict)
        answer = reading.start()
        if answer is not None:
            _prompt_answer(reading, answer)
        elif reading.plain:
            verdict.context.append(_context(run.hook, reading.plain))
        reading.done()
    return verdict


def _prompt_answer(reading: _Reading, answer: dict[str, Any]) -> None:
    verdict = reading.verdict
    output = _hook_output(answer, {"hookEventName": str, "additionalContext": str})
    if (output is None or not _typed(answer, {**_UNIVERSAL, "decision": str, "reason": str})
            or answer.get("decision") not in (None, "block")):
        reading.fail("returned invalid UserPromptSubmit JSON output")
        return
    if answer.get("systemMessage"):
        verdict.messages.append(answer["systemMessage"])
    block = answer.get("decision") == "block"
    invalid = block and not _reason(answer.get("reason"))
    if output.get("additionalContext") and (not reading.control or not invalid):
        verdict.context.append(_context(reading.run.hook, output["additionalContext"]))
    if not reading.control:
        return
    if answer.get("continue") is False:
        reading.outcome = "stopped"
        verdict.stops.append(_reason(answer.get("stopReason")))
    elif invalid:
        reading.fail("returned decision:block without a non-empty reason")
    elif block:
        reading.decided(_reason(answer.get("reason")))


def read_stop(runs: list[HookRun]) -> HookVerdict:
    """Stop answers, as codex reads them: ``block`` with a reason, or exit
    code 2 and a reason on stderr, sends the task on with the reason as its
    next prompt; ``continue: false`` ends it, whatever another hook asked.
    A hook that decides answers in JSON: plain text from one is a failure."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, STOP, verdict)
        answer = reading.start()
        if answer is not None:
            _stop_answer(reading, answer)
        elif reading.plain and reading.control:
            reading.fail("returned invalid Stop JSON output")
        reading.done()
    return verdict


def _stop_answer(reading: _Reading, answer: dict[str, Any]) -> None:
    verdict = reading.verdict
    if (not _typed(answer, {**_UNIVERSAL, "decision": str, "reason": str})
            or answer.get("decision") not in (None, "block")):
        reading.fail("returned invalid Stop JSON output")
        return
    if answer.get("systemMessage"):
        verdict.messages.append(answer["systemMessage"])
    if not reading.control:
        return
    block = answer.get("decision") == "block"
    if answer.get("continue") is False:
        reading.outcome = "stopped"
        verdict.stops.append(_reason(answer.get("stopReason")))
    elif block and not _reason(answer.get("reason")):
        reading.fail("returned decision:block without a non-empty reason")
    elif block:
        reading.decided(_reason(answer.get("reason")))


def read_session_start(runs: list[HookRun]) -> HookVerdict:
    """SessionStart answers, as codex reads them: ``additionalContext``, or
    plain text, is a note for the model with the session's first prompt;
    ``continue: false`` stops that prompt. Exit code 2 decides nothing."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, SESSION_START, verdict)
        answer = reading.start()
        if answer is not None:
            _start_answer(reading, answer)
        elif reading.plain:
            verdict.context.append(_context(run.hook, reading.plain))
        reading.done()
    return verdict


def _start_answer(reading: _Reading, answer: dict[str, Any]) -> None:
    verdict = reading.verdict
    output = _hook_output(answer, {"hookEventName": str, "additionalContext": str})
    if output is None or not _typed(answer, _UNIVERSAL):
        reading.fail("returned invalid SessionStart JSON output")
        return
    if answer.get("systemMessage"):
        verdict.messages.append(answer["systemMessage"])
    if output.get("additionalContext"):
        verdict.context.append(_context(reading.run.hook, output["additionalContext"]))
    if reading.control and answer.get("continue") is False:
        reading.outcome = "stopped"
        verdict.stops.append(_reason(answer.get("stopReason")))


def read_session_end(runs: list[HookRun]) -> HookVerdict:
    """SessionEnd runs, as codex reads them: what one prints is not read;
    one that fails says why, from its stderr."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, SESSION_END, verdict)
        reading.failed()
        reading.done()
    return verdict


def read_interrupt(runs: list[HookRun]) -> HookVerdict:
    """Interrupt answers, as codex reads them: nothing, or JSON with a
    ``systemMessage``; plain text is a failure."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, INTERRUPT, verdict)
        answer = reading.start()
        if answer is not None:
            if not _typed(answer, {"systemMessage": str}):
                reading.fail("returned invalid Interrupt JSON output")
            elif answer.get("systemMessage"):
                verdict.messages.append(answer["systemMessage"])
        elif reading.plain:
            reading.fail("returned text that is not JSON")
        reading.done()
    return verdict


def _read_compact(event: str, runs: list[HookRun]) -> HookVerdict:
    """PreCompact and PostCompact answers, as codex reads them: nothing, or
    JSON with a ``systemMessage`` and ``continue: false``, which stops the
    compaction (PreCompact) or the task after it (PostCompact). Plain text
    is not read."""
    verdict = HookVerdict()
    for run in runs:
        reading = _Reading(run, event, verdict)
        if not reading.failed():
            answer, broken = _json_object(run.stdout)
            if broken or (answer is not None and not _typed(answer, _UNIVERSAL)):
                reading.fail(f"returned invalid {event} JSON output")
            elif answer is not None:
                if answer.get("systemMessage"):
                    verdict.messages.append(answer["systemMessage"])
                if reading.control and answer.get("continue") is False:
                    reading.outcome = "stopped"
                    verdict.stops.append(_reason(answer.get("stopReason")))
        reading.done()
    return verdict


def read_pre_compact(runs: list[HookRun]) -> HookVerdict:
    return _read_compact(PRE_COMPACT, runs)


def read_post_compact(runs: list[HookRun]) -> HookVerdict:
    return _read_compact(POST_COMPACT, runs)


READERS: dict[str, Callable[[list[HookRun]], HookVerdict]] = {
    PRE_TOOL_USE: read_pre_tool_use, PERMISSION_REQUEST: read_permission_request,
    POST_TOOL_USE: read_post_tool_use,
    USER_PROMPT_SUBMIT: read_user_prompt_submit, STOP: read_stop,
    SESSION_START: read_session_start, SESSION_END: read_session_end,
    INTERRUPT: read_interrupt, PRE_COMPACT: read_pre_compact, POST_COMPACT: read_post_compact,
}


def scrubbed(value: Any, scrub: Callable[[str], str] | None) -> Any:
    """``value`` with each string in it scrubbed: before JSON is made of
    it, since a secret with a quote in it is no longer itself once escaped."""
    if scrub is None:
        return value
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {key: scrubbed(item, scrub) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrubbed(item, scrub) for item in value]
    return value
