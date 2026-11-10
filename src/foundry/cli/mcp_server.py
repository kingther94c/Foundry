"""``foundry mcp-server``: Foundry offered to another agent as an MCP server
over stdio, as ``codex mcp-server`` offers codex (D-103).

The client -- an agent, an editor -- gets two tools. ``foundry`` starts a
session on a task in a Git repository and answers with how it ended;
``foundry-reply`` gives a session another task. Each task runs as
``foundry exec`` runs one, and only ``finish`` reports a task completed. What
the permission mode would ask about is asked of a client that offers
elicitation -- its user answers, as codex's client is asked (D-104) -- and
refused when it offers none.

stdout carries the protocol and nothing else. What the sessions display goes
to stderr, as does anything else that prints, so a stray line cannot break a
message the client is reading.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable

from rich.console import Console
from rich.markup import escape

from foundry import __version__
from foundry.cli.unattended import NO_FINISH, _run_unattended, announce_start, unattended
from foundry.cli.wiring import Wiring, _close, _config_args, build
from foundry.cli.render import sanitize, visible
from foundry.cli.repl import with_note
from foundry.core.config import load_config, settings_layer, user_dir
from foundry.core.errors import ConfigError, FoundryError
from foundry.core.events import (
    ApprovalChoice,
    ApprovalKind,
    ApprovalRequest,
    Notice,
    TerminalStatus,
    Termination,
    ToolBegin,
)
from foundry.core.policy.engine import Mode
from foundry.core.prompts import system_prompt
from foundry.core.resume import find_journal, recorded_workspace
from foundry.core.tools.git import run_git

# The protocol versions this server speaks; a client asking for another is
# offered the first.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
# The most one message from the client may hold.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
# The longest task a call may give, as `foundry exec -` reads one.
MAX_PROMPT_CHARS = 1_000_000
# Sessions held open for foundry-reply. Past this the one least recently used
# is closed; a reply to it resumes it from its journal.
MAX_OPEN_SESSIONS = 8
# How long the tasks at work are waited for, once cancelled because the
# client has gone.
SHUTDOWN_WAIT_S = 10.0
# The longest message a progress notification carries.
MAX_PROGRESS_CHARS = 200
# Without a client to ask, default would only refuse what it asks about.
MODES = (Mode.DONT_ASK, Mode.PLAN, Mode.ACCEPT_EDITS)
# The longest question put to the client: a patch is cut to fit.
MAX_QUESTION_CHARS = 4000
# What would run, shown whole, at most (D-151): an MCP call's arguments may be
# 64,000 bytes; past this a call is refused rather than asked about.
MAX_SHOWN_CHARS = 70_000
# The answers a client is offered, as codex's: yes once, yes for the session,
# no, or no and stop the task. Not "always": that writes a rule into the
# user's own config, which is for the user to do at the terminal.
CHOICES = (ApprovalChoice.ONCE, ApprovalChoice.SESSION, ApprovalChoice.DENY,
           ApprovalChoice.ABORT)
ANSWER_SCHEMA = {"type": "object", "properties": {"decision": {
    "type": "string", "title": "Allow it?", "enum": [choice.value for choice in CHOICES],
    "enumNames": ["Yes, this once", "Yes, for the rest of this session", "No",
                  "No, and stop the task"]}}, "required": ["decision"]}
_ASKING = {ApprovalKind.COMMAND: "run a command", ApprovalKind.PATCH: "change files"}

START = "foundry"
REPLY = "foundry-reply"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

_PROMPT = {"type": "string", "description": "the task, as foundry exec takes one"}
TOOLS = (
    {"name": START, "title": "Foundry: start a session",
     "description": (
         "Start a Foundry session on a task in a Git repository and wait for it to end. "
         "What the permission mode would ask about is asked of you, if you offer "
         "elicitation, and refused if you do not. The answer is the session's last word "
         "-- finish's summary, or its reply -- with its sessionId and how it ended; give "
         "the session another task with foundry-reply."),
     "inputSchema": {"type": "object", "properties": {
         "prompt": _PROMPT,
         "cwd": {"type": "string",
                 "description": "the repository to work in, absolute or relative to the "
                                "server's own directory (default: that directory)"},
         "model": {"type": "string", "description": "the model to use"},
         "profile": {"type": "string",
                     "description": "a [profiles.<name>] table from your config.toml"},
         "mode": {"type": "string", "enum": [mode.value for mode in MODES],
                  "description": "the permission mode (default: the server's --mode, "
                                 "else dont_ask)"},
         "config": {"type": "object",
                    "description": "settings as -c takes them, such as "
                                   "{\"runtime.max_tool_rounds\": 60}; no secrets"},
     }, "required": ["prompt"], "additionalProperties": False}},
    {"name": REPLY, "title": "Foundry: continue a session",
     "description": (
         "Give a Foundry session another task and wait for it to end, as foundry does. "
         "A session this server no longer holds open is resumed from its journal."),
     "inputSchema": {"type": "object", "properties": {
         "sessionId": {"type": "string",
                       "description": "the sessionId a foundry call answered with"},
         "prompt": _PROMPT,
     }, "required": ["sessionId", "prompt"], "additionalProperties": False}},
)
_ARGUMENTS = {START: frozenset({"prompt", "cwd", "model", "profile", "mode", "config"}),
              REPLY: frozenset({"sessionId", "prompt"})}
INSTRUCTIONS = (
    "Foundry is a coding agent. Call foundry with a task to have it worked on in a Git "
    "repository; it answers when the task has ended, with the session's sessionId. Give "
    "that session another task with foundry-reply. Nothing is approved on the user's "
    "behalf: what its permission mode would ask about is asked of the user through "
    "elicitation, when the client offers it, and refused otherwise.")


class _Refused(Exception):
    """A call this server will not run, said to the client as the tool's error."""


@dataclass(frozen=True, slots=True)
class _Task:
    prompt: str
    session_id: str = ""
    cwd: Path | None = None
    model: str | None = None
    profile: str | None = None
    mode: Mode | None = None
    settings: dict = field(default_factory=dict)


@dataclass(slots=True)
class _Open:
    """A session the server holds open, for foundry-reply."""
    wiring: Wiring
    place: str             # its repository, as a key: one task at a time in each
    running: Any = None    # the id of the request it is at work for
    progress: Any = None   # that request's progressToken
    sent: int = 0
    used: int = 0


class Server:
    """The protocol, given each line the client sends; ``write`` takes each
    line it is answered with. A tools/call runs in its own thread, so a ping or
    a cancel is answered while a task is at work."""

    def __init__(self, write: Callable[[bytes], None], *, cwd: Path,
                 defaults: dict | None = None, home: Path | None = None,
                 console: Callable[[], Console] | None = None) -> None:
        defaults = defaults or {}
        self._write = write
        self._cwd = cwd
        self._home = home
        self._overrides = dict(defaults.get("overrides") or {})
        # As exec: a configured mode never decides what an unattended task may do.
        self._overrides.setdefault("mode", Mode.DONT_ASK)
        self._profile = defaults.get("profile")
        self._settings = defaults.get("settings") or {}
        self._console = console or (lambda: Console(stderr=True))
        self._write_lock = threading.Lock()
        self._lock = threading.Lock()
        self._open: dict[str, _Open] = {}
        # Each request at work, and its session once it has one.
        self._requests: dict[Any, _Open | None] = {}
        self._cancelled: set[Any] = set()
        # Each repository a task is at work in, and that task's request.
        self._places: dict[str, Any] = {}
        self._building = 0
        self._clock = 0
        self._closing = False
        self._workers: list[threading.Thread] = []
        # Whether the client said at initialize that it can be asked (D-104),
        # and each question put to it, with its answer once it has come.
        self._asks = False
        self._questions: dict[str, dict | None] = {}
        self._asked = 0
        self._answers = threading.Condition(self._lock)

    # -- messages ------------------------------------------------------------------

    def receive(self, line: bytes) -> None:
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(None, PARSE_ERROR, "not a JSON-RPC message")
            return
        if not isinstance(message, dict):
            # Batches left the protocol in 2025-06-18.
            self._error(None, INVALID_REQUEST, "one JSON-RPC message per line, not a batch")
            return
        method = message.get("method")
        if not isinstance(method, str):
            self._answered(message)
            return
        params = message.get("params")
        if params is None:
            params = {}
        if "id" not in message:
            if method == "notifications/cancelled" and isinstance(params, dict):
                self._cancel(params.get("requestId"))
            return  # notifications/initialized and the rest: nothing to do
        rid = message["id"]
        if not _is_id(rid):
            self._error(None, INVALID_REQUEST, "a request's id is a string or an integer")
            return
        if not isinstance(params, dict):
            self._error(rid, INVALID_PARAMS, f"{method}: params must be an object")
        elif method == "initialize":
            self._reply(rid, self._initialize(params))
        elif method == "ping":
            self._reply(rid, {})
        elif method == "tools/list":
            self._reply(rid, {"tools": self._tools()})
        elif method == "tools/call":
            self._call(rid, params)
        else:
            self._error(rid, METHOD_NOT_FOUND, f"method not found: {method}")

    def too_large(self) -> None:
        self._error(None, INVALID_REQUEST, f"a message over {MAX_MESSAGE_BYTES} bytes")

    def _initialize(self, params: dict) -> dict:
        offered = params.get("capabilities")
        self._asks = isinstance(offered, dict) and isinstance(offered.get("elicitation"), dict)
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "foundry", "title": "Foundry", "version": __version__},
                "instructions": INSTRUCTIONS}

    def _modes(self) -> tuple[Mode, ...]:
        return MODES + ((Mode.DEFAULT,) if self._asks else ())

    def _tools(self) -> list[dict]:
        tools = copy.deepcopy(list(TOOLS))
        tools[0]["inputSchema"]["properties"]["mode"]["enum"] = [m.value for m in self._modes()]
        return tools

    def _send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self._write(data)
            except (OSError, ValueError):
                pass  # the client has gone; its input ends too, and the server with it

    def _reply(self, rid: Any, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _error(self, rid: Any, code: int, text: str) -> None:
        self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": text}})

    # -- a call --------------------------------------------------------------------

    def _call(self, rid: Any, params: dict) -> None:
        name = params.get("name")
        if name not in _ARGUMENTS:
            self._error(rid, INVALID_PARAMS,
                        f"no tool {name!r}: this server offers {START} and {REPLY}")
            return
        meta = params.get("_meta")
        token = meta.get("progressToken") if isinstance(meta, dict) else None
        try:
            task = self._read(name, params.get("arguments", {}))
        except _Refused as exc:
            self._reply(rid, _refusal(str(exc)))
            return
        with self._lock:
            if rid in self._requests:
                busy = True
            else:
                busy = False
                self._requests[rid] = None
                worker = threading.Thread(target=self._work, name=f"foundry-mcp-{rid}",
                                          args=(rid, name, task, token if _is_id(token) else None),
                                          daemon=True)
                self._workers = [w for w in self._workers if w.is_alive()] + [worker]
        if busy:
            self._error(rid, INVALID_REQUEST, f"request {rid!r} is still at work; an id is "
                                              "not used twice")
            return
        worker.start()

    def _read(self, name: str, arguments: Any) -> _Task:
        if not isinstance(arguments, dict):
            raise _Refused(f"{name}'s arguments are an object")
        extra = sorted(set(arguments) - _ARGUMENTS[name])
        if extra:
            raise _Refused(f"{name} takes no {', '.join(map(repr, extra))}; it takes "
                           f"{', '.join(sorted(_ARGUMENTS[name]))}")
        prompt = arguments.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise _Refused(f"{name} needs the prompt: the task")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise _Refused(f"the prompt is over {MAX_PROMPT_CHARS:,} characters")
        given = {key: arguments.get(key) for key in _ARGUMENTS[name] - {"prompt", "config"}}
        for key, value in given.items():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise _Refused(f"{key} is a string, and not an empty one")
        if name == REPLY:
            if given["sessionId"] is None:
                raise _Refused(f"{REPLY} needs the sessionId a {START} call answered with")
            return _Task(prompt=prompt, session_id=given["sessionId"].strip())
        mode = None
        if given["mode"] is not None:
            mode = next((m for m in self._modes() if m.value == given["mode"]), None)
            if mode is None and self._asks:
                raise _Refused(f"mode is one of {', '.join(m.value for m in self._modes())}")
            if mode is None:
                raise _Refused(f"mode is one of {', '.join(m.value for m in MODES)}: nobody is "
                               "there to approve what default would ask about (a client that "
                               "offers elicitation is asked)")
        config = arguments.get("config", {})
        if not isinstance(config, dict):
            raise _Refused("config is an object of settings, as -c takes them")
        try:
            settings = settings_layer(config)
        except ConfigError as exc:
            raise _Refused(f"config: {exc}") from exc
        cwd = Path(given["cwd"]) if given["cwd"] else self._cwd
        return _Task(prompt=prompt, cwd=cwd if cwd.is_absolute() else self._cwd / cwd,
                     model=given["model"], profile=given["profile"], mode=mode,
                     settings=settings)

    def _work(self, rid: Any, name: str, task: _Task, token: Any) -> None:
        entry = None
        try:
            with self._lock:
                gone = rid in self._cancelled
            if gone:
                self._settle(rid, None)
                return
            entry = self._take(rid, name, task)
            with self._lock:
                entry.progress, entry.sent = token, 0
            answer = self._run(entry, task)
        except _Refused as exc:
            answer = _refusal(str(exc))
        except FoundryError as exc:
            answer = _refusal(str(exc))
        except Exception as exc:  # noqa: BLE001 - a fault of Foundry's own
            answer = _refusal(f"Foundry failed: {type(exc).__name__}: {exc}")
            if entry is not None:
                self._drop(entry)
                entry = None
        if self._settle(rid, entry):
            self._reply(rid, answer)

    def _take(self, rid: Any, name: str, task: _Task) -> _Open:
        """The session the task runs in: one held open, or one built -- new,
        or resumed from its journal."""
        if name == REPLY:
            with self._lock:
                entry = self._open.get(task.session_id)
                if entry is not None:
                    if entry.running is not None:
                        raise _Refused(f"session {task.session_id} is at work on another "
                                       "task; wait for its answer")
                    self._claim(entry.place, rid)
                    entry.running = rid
                    self._requests[rid] = entry
                    cancelled = rid in self._cancelled
            if entry is not None:
                # A cancel that came between _work's look and the entry being
                # registered found no runtime to stop; it is honoured here, as
                # a session just built honours one (D-156).
                if cancelled:
                    entry.wiring.runtime.cancel()
                return entry
            journal = find_journal((self._home or user_dir()) / "sessions", task.session_id)
            if journal is None:
                raise _Refused(f"no session {task.session_id}: give the sessionId a {START} "
                               "call answered with")
            where = Path(recorded_workspace(journal.parent) or self._cwd)
        else:
            assert task.cwd is not None
            where = task.cwd
        place = _place(where)
        with self._lock:
            self._claim(place, rid)
            try:
                evicted = self._make_room()
            except _Refused:
                del self._places[place]
                raise
            self._building += 1
        for old in evicted:
            self._shut(old)
        try:
            wiring = self._build(where, task, resume=task.session_id if name == REPLY else None)
        except BaseException:
            with self._lock:
                self._building -= 1
                del self._places[place]
            raise
        entry = _Open(wiring=wiring, place=place, running=rid)
        wiring.runtime.events.subscribe(self._progress(entry))
        if self._asks:
            self._let_ask(entry)
        with self._lock:
            self._building -= 1
            self._open[wiring.session.session_id] = entry
            self._requests[rid] = entry
            cancelled = rid in self._cancelled
        if cancelled:
            wiring.runtime.cancel()
        return entry

    # -- asking the client (D-104) ------------------------------------------------

    def _let_ask(self, entry: _Open) -> None:
        """What the mode would ask about is asked of the client, not refused."""
        runtime = entry.wiring.runtime
        runtime.approval = lambda request: self._ask(entry, request)
        runtime.policy.interactive = True
        runtime.context.system_prompt = system_prompt(runtime.policy)

    def _ask(self, entry: _Open, request: ApprovalRequest) -> ApprovalChoice:
        """An elicitation request, waited on until it is answered -- or until
        the task is stopped or the client goes, when the client is told the
        question is moot and the task stops."""
        runtime = entry.wiring.runtime
        with self._lock:
            if self._closing:
                return ApprovalChoice.ABORT
        message = _question(request, entry.wiring, runtime.scrub)
        if message is None:
            runtime.events.emit(Notice(
                f"not asked: {request.tool}'s call is too long to show in full "
                f"(over {MAX_SHOWN_CHARS:,} characters), so it is refused", level="warning"))
            return ApprovalChoice.DENY
        with self._lock:
            if self._closing:
                return ApprovalChoice.ABORT
            self._asked += 1
            qid = f"foundry-ask-{self._asked}"
            self._questions[qid] = None
        self._send({"jsonrpc": "2.0", "id": qid, "method": "elicitation/create", "params": {
            "message": message, "requestedSchema": ANSWER_SCHEMA}})
        with self._answers:
            try:
                # The client's going cancels every task at work, this one too.
                while self._questions[qid] is None and not runtime._cancelled:
                    self._answers.wait(0.1)
                reply = self._questions[qid]
            finally:
                del self._questions[qid]
        if reply is None:
            self._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                        "params": {"requestId": qid, "reason": "the task was stopped"}})
            return ApprovalChoice.ABORT
        return _decision(reply)

    def _answered(self, message: dict) -> None:
        """A reply from the client: the answer to a question put to it."""
        qid = message.get("id")
        with self._answers:
            # Only a question's own id: a malformed one (a list) is no key at all.
            if isinstance(qid, str) and qid in self._questions:
                self._questions[qid] = message
                self._answers.notify_all()

    def _claim(self, place: str, rid: Any) -> None:
        """Held: one task at a time in a repository, so no session patches a
        file another is changing under it."""
        if place in self._places:
            raise _Refused(f"another Foundry task is at work in {place}; wait for its answer, "
                           "or give a task in another repository")
        self._places[place] = rid

    def _make_room(self) -> list[_Open]:
        """Held: the sessions closed to make room for one more."""
        evicted = []
        while len(self._open) + self._building >= MAX_OPEN_SESSIONS:
            idle = [(entry.used, sid) for sid, entry in self._open.items()
                    if entry.running is None]
            if not idle:
                raise _Refused(f"every session this server holds open ({MAX_OPEN_SESSIONS}) "
                               "is at work; wait for one to answer")
            evicted.append(self._open.pop(min(idle)[1]))
        return evicted

    def _build(self, where: Path, task: _Task, *, resume: str | None) -> Wiring:
        overrides = dict(self._overrides)
        if task.model:
            overrides["model"] = task.model
        if task.mode is not None:
            overrides["mode"] = task.mode
        # The call's settings are the client's, after the server's own -c,
        # and may only tighten them (D-150).
        wiring = build(where, home=self._home, console=self._console(), interactive=False,
                       resume=resume, overrides=overrides,
                       profile=task.profile or self._profile, settings=self._settings,
                       client_settings=task.settings)
        unattended(wiring)
        announce_start(wiring)
        return wiring

    def _run(self, entry: _Open, task: _Task) -> dict:
        wiring = entry.wiring
        outcome = _run_unattended(wiring.runtime, with_note(wiring, task.prompt))
        status = outcome.status or TerminalStatus.PARTIAL
        reason = ""
        if outcome.status is None:
            reason = NO_FINISH
            wiring.runtime.events.emit(Termination(status=status, reason=reason,
                                                   summary=outcome.text))
            try:
                wiring.session.record_termination(status, reason)
            except OSError:
                pass  # the journal says it is degraded; the answer still goes
        # The session is held open for a foundry-reply, so the calls the task
        # left -- finish, and any after it -- are answered now, as the REPL
        # answers them (D-063, D-167): a request with a call left unanswered is
        # refused by every provider.
        wiring.runtime.answer_left_calls()
        return _answer(wiring.session.session_id, status, reason, outcome.text)

    def _settle(self, rid: Any, entry: _Open | None) -> bool:
        """The request is done with; whether it is to be answered (not if
        cancelled: the client has stopped waiting, or gone)."""
        with self._lock:
            answered = rid not in self._cancelled
            self._cancelled.discard(rid)
            self._requests.pop(rid, None)
            closing = self._closing
            if entry is not None:
                self._places.pop(entry.place, None)
                entry.running = entry.progress = None
                self._clock += 1
                entry.used = self._clock
                # A cancel that came as the task ended is spent with it.
                entry.wiring.runtime._cancelled = False
                if closing:
                    self._open.pop(entry.wiring.session.session_id, None)
        if entry is not None and closing:
            self._shut(entry)
        return answered

    def _drop(self, entry: _Open) -> None:
        """A session in a state nobody can vouch for: closed, not offered again."""
        with self._lock:
            self._open.pop(entry.wiring.session.session_id, None)
            self._places.pop(entry.place, None)
        self._shut(entry, TerminalStatus.FAILED, "foundry mcp-server failed")

    @staticmethod
    def _shut(entry: _Open, status: TerminalStatus = TerminalStatus.CANCELLED,
              reason: str = "foundry mcp-server closed it") -> None:
        try:
            _close(entry.wiring, status, reason)
        except Exception:  # noqa: BLE001 - one session's fault must not keep the rest open
            pass

    def _progress(self, entry: _Open) -> Callable[[Any], None]:
        """Each tool call the task makes, as a progress notification -- when
        the request asked for them with a progressToken."""
        def forward(event: Any) -> None:
            if not isinstance(event, ToolBegin):
                return
            with self._lock:
                token = entry.progress
                if token is None:
                    return
                entry.sent += 1
                count = entry.sent
            text = " ".join(event.display.split())
            if len(text) > MAX_PROGRESS_CHARS:
                text = text[:MAX_PROGRESS_CHARS - 3] + "..."
            self._send({"jsonrpc": "2.0", "method": "notifications/progress",
                        "params": {"progressToken": token, "progress": count,
                                   "message": text}})
        return forward

    def _cancel(self, rid: Any) -> None:
        if not _is_id(rid):
            return
        with self._lock:
            if rid not in self._requests:
                return  # answered already, or never this server's
            self._cancelled.add(rid)
            entry = self._requests[rid]
        if entry is not None:
            entry.wiring.runtime.cancel()

    # -- the end -------------------------------------------------------------------

    def close(self) -> None:
        """The client has gone: the tasks at work are cancelled and waited for
        a while, and every session is closed."""
        with self._lock:
            self._closing = True
            idle = [sid for sid, entry in self._open.items() if entry.running is None]
            closed = [self._open.pop(sid) for sid in idle]
            busy = [entry for entry in self._requests.values() if entry is not None]
            self._cancelled.update(self._requests)
            workers = list(self._workers)
        for entry in busy:
            entry.wiring.runtime.cancel()
        for entry in closed:
            self._shut(entry)
        deadline = time.monotonic() + SHUTDOWN_WAIT_S
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))


def _is_id(value: Any) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool)


# How many superprojects up a submodule's place is looked for.
MAX_SUPERPROJECTS = 8


def _git_line(arguments: list[str], path: Path) -> str:
    try:
        code, out, _ = run_git(arguments, path)
    except (FoundryError, OSError):
        return ""
    return out.strip() if code == 0 else ""


def _place(path: Path) -> str:
    """The repository a task would work in, as a key: the outermost
    superproject of a submodule. A task in the superproject writes the
    submodule's files too, so the two were two tasks writing one file
    (D-156)."""
    top = _git_line(["rev-parse", "--show-toplevel"], path) or str(path)
    for _ in range(MAX_SUPERPROJECTS):
        outer = _git_line(["rev-parse", "--show-superproject-working-tree"], Path(top))
        if not outer:
            break
        top = outer
    return os.path.normcase(os.path.realpath(top))


def _question(request: ApprovalRequest, wiring: Wiring,
              scrub: Callable[[str], str] | None) -> str | None:
    """What the client's user is asked: what would run, and why it is asked
    -- its display, as the terminal shows it, stripped of control sequences
    and scrubbed of registered secrets.

    What would run is never cut (D-151): cut at 4,000 characters with the
    rest, a command padded past them was approved without its tail. Only a
    patch's body is shortened to fit, as the terminal shortens its preview;
    a call too long to show whole is not asked about at all (None)."""
    def clean(text: str) -> str:
        text = sanitize(text)
        return scrub(text) if scrub is not None else text

    what = _ASKING.get(request.approval_kind, f"use {request.tool}")
    lines = [f"Foundry session {wiring.session.session_id} in {wiring.workspace.root} asks to "
             f"{what}: {visible(request.display)}", f"It asks because: {visible(request.reason)}"]
    patch = request.detail if request.approval_kind is ApprovalKind.PATCH else ""
    if request.detail and not patch:
        lines += ["", "With these arguments:", visible(request.detail)]
    text = clean("\n".join(lines))
    if len(text) > MAX_SHOWN_CHARS:
        return None
    if patch:
        body = clean(patch)
        room = max(MAX_QUESTION_CHARS - len(text) - 2, 0)
        if len(body) > room:
            note = (f"\n[... {len(body) - room:,} more characters of the patch; the full "
                    "patch is in the session journal ...]")
            body = body[:max(room - len(note), 0)] + note
        text += "\n\n" + body
    return text


def _decision(reply: dict) -> ApprovalChoice:
    """The client's answer: anything but a clear yes is a no."""
    result = reply.get("result")
    if not isinstance(result, dict) or result.get("action") != "accept":
        return ApprovalChoice.DENY
    content = result.get("content")
    value = content.get("decision") if isinstance(content, dict) else None
    return next((choice for choice in CHOICES if choice.value == value), ApprovalChoice.DENY)


def _refusal(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _answer(session_id: str, status: TerminalStatus, reason: str, text: str) -> dict:
    """The session's last word, and how it ended: in the text, for a client
    that shows its model only that, and as structured content."""
    said = text.strip() or reason or f"the session ended {status.value}"
    why = f" ({reason})" if reason and reason not in said else ""
    structured: dict[str, Any] = {"sessionId": session_id, "status": status.value, "text": said}
    if reason:
        structured["reason"] = reason
    return {"content": [
        {"type": "text", "text": said},
        {"type": "text", "text": f"Foundry session {session_id} ended {status.value}{why}. "
                                 f"Give it another task with {REPLY} and this sessionId."}],
        "structuredContent": structured,
        # The task could not be worked on; a partial outcome is still an answer.
        "isError": status in (TerminalStatus.BLOCKED, TerminalStatus.FAILED)}


# --- stdio --------------------------------------------------------------------------


def serve(reader: BinaryIO, server: Server) -> None:
    """The client's messages, one a line, until its input ends."""
    try:
        while True:
            line = reader.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):
                    line = reader.readline(MAX_MESSAGE_BYTES)
                server.too_large()
                continue
            if line.strip():
                server.receive(line)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


def _protocol_channel() -> tuple[BinaryIO, Callable[[bytes], None]]:
    """stdin and stdout, kept for the protocol alone. The descriptors are
    pointed elsewhere: whatever else writes to stdout -- a print, a process
    that inherits it -- reaches stderr, and a process Foundry starts reads
    its stdin from nowhere. On Windows, git started while the server waited
    on the client's next line hung on the pipe they shared, until it timed
    out."""
    sys.stdout.flush()
    reader = os.fdopen(os.dup(sys.stdin.fileno()), "rb")
    stream = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    try:
        nowhere = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nowhere, sys.stdin.fileno())
        os.close(nowhere)
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    except (OSError, ValueError):
        pass

    def write(data: bytes) -> None:
        stream.write(data)
        stream.flush()
    return reader, write


def serve_stdio(args: Any) -> int:
    console = Console(stderr=True)
    try:
        defaults = _config_args(args, mode=Mode(args.mode or Mode.DONT_ASK.value))
        # A setting or profile that cannot work is said now, not to each call.
        load_config(None, overrides=defaults["overrides"], home=user_dir(),
                    profile=defaults["profile"], settings=defaults["settings"])
    except ConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return 2
    reader, write = _protocol_channel()
    serve(reader, Server(write, cwd=Path.cwd(), defaults=defaults))
    return 0
