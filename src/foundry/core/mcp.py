"""An MCP client over stdio (D-043) and streamable HTTP (D-081): another
program's tools, under Foundry's policy.

A server is a program the user names in their own config.toml -- or the
address of one already running, reached over HTTP (see StreamableHttpClient).
Foundry starts
it with the same scrubbed environment a command gets (plus what the entry adds),
speaks JSON-RPC 2.0 to it one message per line, lists its tools, and offers each
to the model as ``mcp__<server>__<tool>``. Nothing about a call is the
server's to decide: every one goes through the policy pipeline like any other
mutator -- refused in plan mode, asked about otherwise unless a rule allows it
-- and its output is untrusted content, truncated and scrubbed like a
command's.

The process runs under a job object, so closing the session ends the server
and everything it started.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import queue
import re
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from foundry import __version__
from foundry.core.config import HEADER_VALUE, McpServerConfig
from foundry.core.conversation import ToolSchema, without_lone_surrogates
from foundry.core.errors import (Cancelled, FoundryError, ImageRejected, InvalidToolCall,
                                 ToolError)
from foundry.core.httpc import HttpClient
from foundry.core.images import MAX_IMAGES_PER_MESSAGE, from_base64 as image_from_base64
from foundry.core.redaction import Redactor, default_redactor
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput, truncate_middle
from foundry.core.winapi import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    ProcessJob,
    child_environment,
    find_program,
)

PROTOCOL_VERSION = "2025-06-18"
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
# Messages queued for a server that is not reading them: past these, a request
# is refused at once, and an answer to the server's own request is dropped.
MAX_UNREAD_MESSAGES = 256
MAX_TOOLS_PER_SERVER = 100
MAX_ARGUMENT_BYTES = 64_000
MAX_SCHEMA_BYTES = 16_000
MAX_DESCRIPTION_CHARS = 2_000
TOOL_NAME_MAX = 64          # what OpenAI-compatible APIs accept
_LIST_PAGES = 20
_NOT_NAME = re.compile(r"[^A-Za-z0-9_-]")
_EMPTY_SCHEMA = {"type": "object", "properties": {}}


class McpError(FoundryError):
    category = "mcp"


def tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>`` in the characters every provider accepts; a
    name too long keeps a digest of the whole, so two never collide by being
    cut at the same place."""
    name = _NOT_NAME.sub("_", f"mcp__{server}__{tool}")
    if len(name) > TOOL_NAME_MAX:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        name = name[:TOOL_NAME_MAX - 9] + "_" + digest
    return name


# --- the connection ---------------------------------------------------------------


def _answer_for(message: dict) -> dict:
    """The reply to a request from the server. Foundry offers it nothing but
    a ping: no sampling, no roots, no elicitation."""
    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
    if message.get("method") == "ping":
        reply["result"] = {}
    else:
        reply["error"] = {"code": -32601, "message": "not offered by this client"}
    return reply


def _result(method: str, reply: dict) -> dict:
    if "error" in reply:
        error = reply["error"]
        text = error.get("message") if isinstance(error, dict) else str(error)
        raise McpError(f"{method} failed: {str(text)[:300]}")
    result = reply.get("result")
    if not isinstance(result, dict):
        raise McpError(f"{method} returned no result")
    return result


class _Protocol:
    """What Foundry asks of a server, whatever carries the messages: the
    handshake, its tools, and a call to one."""

    server: McpServerConfig
    capabilities: dict[str, Any]
    server_info: dict[str, Any]

    def request(self, method: str, params: dict | None = None, *, timeout: float,
                cancelled: Callable[[], bool] | None = None) -> dict:
        raise NotImplementedError

    def _handshake(self) -> str:
        """Initialize, and the protocol version the server answered with."""
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "foundry", "version": __version__},
        }, timeout=self.server.startup_timeout_s)
        version = result.get("protocolVersion")
        if not isinstance(version, str):
            raise McpError("its reply to initialize names no protocol version")
        self.server_info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
        self.capabilities = (result.get("capabilities")
                             if isinstance(result.get("capabilities"), dict) else {})
        return version

    def list_tools(self) -> list[dict]:
        if "tools" not in self.capabilities:
            return []
        tools: list[dict] = []
        cursor = None
        for _ in range(_LIST_PAGES):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {},
                                  timeout=self.server.startup_timeout_s)
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpError("tools/list returned no list of tools")
            tools.extend(t for t in page if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or len(tools) >= MAX_TOOLS_PER_SERVER:
                break
        return tools[:MAX_TOOLS_PER_SERVER]

    def call_tool(self, name: str, arguments: dict, *,
                  cancelled: Callable[[], bool] | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments},
                            timeout=self.server.tool_timeout_s, cancelled=cancelled)

    # -- resources (D-102) -----------------------------------------------------

    def list_resources(self, cursor: str | None = None, *, templates: bool = False) -> dict:
        """One page of the server's resources, or of its URI templates."""
        method = "resources/templates/list" if templates else "resources/list"
        return self.request(method, {"cursor": cursor} if cursor else {},
                            timeout=self.server.tool_timeout_s)

    def read_resource(self, uri: str, *,
                      cancelled: Callable[[], bool] | None = None) -> dict:
        return self.request("resources/read", {"uri": uri},
                            timeout=self.server.tool_timeout_s, cancelled=cancelled)


class StdioClient(_Protocol):
    """One server process and the JSON-RPC conversation with it."""

    def __init__(self, server: McpServerConfig, cwd: Path,
                 environ: dict[str, str] | None = None) -> None:
        self.server = server
        self.cwd = cwd
        self._environ = dict(os.environ if environ is None else environ)
        self._process: subprocess.Popen | None = None
        self._job: ProcessJob | None = None
        # What is to be written to the server, by a thread of its own: a
        # server that stops reading fills the pipe, and a write blocked on it
        # held every request, the reader answering its pings, Ctrl+C and
        # close (D-155). None asks the writer to close the server's input.
        self._outbox: queue.Queue[bytes | None] = queue.Queue(maxsize=MAX_UNREAD_MESSAGES)
        self._write_error = ""
        self._cond = threading.Condition()
        self._pending: set[int] = set()
        self._responses: dict[int, dict] = {}
        self._next_id = 0
        self._stopped = ""
        self._stderr: deque[str] = deque(maxlen=20)
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}

    # -- process ---------------------------------------------------------------

    def environment(self) -> dict[str, str]:
        """What a command would get -- credential-shaped names removed -- plus
        the variables the entry passes through by name, and its plain values."""
        env = child_environment(self._environ)
        for name in self.server.env_vars:
            if name in self._environ:
                env[name] = self._environ[name]
                # What env_vars is for: a secret the server needs, so one it
                # echoes is scrubbed as a bearer token is (D-153).
                default_redactor().register(self._environ[name])
        env.update(dict(self.server.env))
        return env

    def start(self) -> None:
        env = self.environment()
        # On PATH only: the current directory -- usually the repository --
        # is not searched (D-049).
        program = find_program(self.server.command, env)
        if program is None:
            raise McpError(f"{self.server.command!r} was not found on PATH")
        # The entry's own folder, as codex's cwd (D-110), or the workspace.
        cwd = Path(self.server.cwd) if self.server.cwd else self.cwd
        if not cwd.is_dir():
            raise McpError(f"its cwd {cwd} is not a folder")
        self._job = ProcessJob()
        try:
            self._process = subprocess.Popen(
                [program, *self.server.args], cwd=cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW) if IS_WINDOWS else 0,
                start_new_session=not IS_WINDOWS,
            )
        except OSError as exc:
            self._job.close()
            raise McpError(f"could not start {self.server.command}: {exc}") from exc
        if IS_WINDOWS and self._job.active:
            self._job.assign(int(self._process._handle))  # type: ignore[attr-defined]
        threading.Thread(target=self._write_stdin, args=(self._process,), daemon=True,
                         name=f"mcp-{self.server.name}-in").start()
        threading.Thread(target=self._read_stdout, daemon=True,
                         name=f"mcp-{self.server.name}-out").start()
        threading.Thread(target=self._read_stderr, daemon=True,
                         name=f"mcp-{self.server.name}-err").start()

        self._handshake()
        self.notify("notifications/initialized")

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def close(self) -> None:
        """End the server: its input closed once what was sent is written, a
        moment to exit, then the job object -- which also ends anything it
        started, and a writer still blocked on a server that stopped reading."""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            self._outbox.put_nowait(None)
        except queue.Full:
            pass   # it reads nothing: the job object ends it below
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if not (IS_WINDOWS and self._job is not None and self._job.terminate()):
                self._kill(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self._job is not None:
            self._job.close()   # kill-on-close: whatever it left behind ends too
        if not IS_WINDOWS:
            self._kill(process)

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        if IS_WINDOWS:
            process.kill()
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    def stderr_tail(self) -> str:
        return " | ".join(self._stderr)

    def _stopped_reason(self) -> str:
        tail = self.stderr_tail()
        return self._stopped + (f"; it said: {tail[-400:]}" if tail else "")

    # -- messages --------------------------------------------------------------

    def _send(self, message: dict) -> None:
        """Queued for the writer, never waited on: a request waits for its
        reply, with its own timeout and Ctrl+C, not for the pipe."""
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        process = self._process
        if process is None or process.stdin is None:
            raise McpError("the server is not running")
        if self._stopped or self._write_error:
            reason = self._stopped_reason() if self._stopped else self._write_error
            raise McpError(f"the server is not running ({reason})")
        try:
            self._outbox.put_nowait(data)
        except queue.Full:
            raise McpError(f"the server is not reading what it is sent: "
                           f"{MAX_UNREAD_MESSAGES} messages wait for it") from None

    def _write_stdin(self, process: subprocess.Popen) -> None:
        stream = process.stdin
        try:
            while stream is not None:
                data = self._outbox.get()
                if data is None:
                    break
                stream.write(data)
                stream.flush()
        except (OSError, ValueError) as exc:
            with self._cond:
                self._write_error = str(exc) or type(exc).__name__
                self._cond.notify_all()
        finally:
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def notify(self, method: str, params: dict | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: dict | None = None, *, timeout: float,
                cancelled: Callable[[], bool] | None = None) -> dict:
        with self._cond:
            self._next_id += 1
            rid = self._next_id
            self._pending.add(rid)
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self._send(message)
            reply = self._wait(rid, method, timeout, cancelled)
        finally:
            with self._cond:
                self._pending.discard(rid)
                self._responses.pop(rid, None)
        return _result(method, reply)

    def _wait(self, rid: int, method: str, timeout: float,
              cancelled: Callable[[], bool] | None) -> dict:
        deadline = time.monotonic() + timeout
        with self._cond:
            while rid not in self._responses:
                if self._stopped:
                    raise McpError(self._stopped_reason())
                if self._write_error:
                    raise McpError(f"the server is not running "
                                   f"({self._stopped_reason() or self._write_error})")
                if cancelled is not None and cancelled():
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(min(remaining, 0.1))
            reply = self._responses.get(rid)
        if reply is not None:
            return reply
        interrupted = cancelled is not None and cancelled()
        try:
            self.notify("notifications/cancelled", {
                "requestId": rid, "reason": "interrupted" if interrupted else "timed out"})
        except McpError:
            pass
        if interrupted:
            raise Cancelled(f"{method} interrupted")
        raise McpError(f"{method} timed out after {timeout:g}s")

    def _read_stdout(self) -> None:
        process = self._process
        stream = process.stdout if process is not None else None
        try:
            while stream is not None:
                line = stream.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE_BYTES:
                    # Skip the rest of it; whatever was waiting for it fails.
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_MESSAGE_BYTES)
                    self._fail_pending(f"a reply over {MAX_MESSAGE_BYTES} bytes")
                    continue
                self._dispatch(line)
        except (OSError, ValueError):
            pass
        finally:
            code = process.poll() if process is not None else None
            if code is None and process is not None:
                try:
                    code = process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    code = None
            with self._cond:
                self._stopped = ("the server stopped" if code is None
                                 else f"the server stopped (exit code {code})")
                self._cond.notify_all()

    def _read_stderr(self) -> None:
        process = self._process
        stream = process.stderr if process is not None else None
        try:
            while stream is not None:
                line = stream.readline(4096)
                if not line:
                    break
                text = " ".join(line.decode("utf-8", errors="replace").split())
                if text:
                    self._stderr.append(text[:300])
        except (OSError, ValueError):
            pass

    def _fail_pending(self, reason: str) -> None:
        with self._cond:
            for rid in self._pending:
                self._responses[rid] = {"error": {"message": reason}}
            self._cond.notify_all()

    def _dispatch(self, line: bytes) -> None:
        try:
            message = without_lone_surrogates(json.loads(line.decode("utf-8", errors="replace")))
        except ValueError:
            return   # a log line printed to stdout: not a message, not ours to act on
        if not isinstance(message, dict):
            return
        if "method" in message:
            if "id" in message:
                try:
                    self._send(_answer_for(message))
                except McpError:
                    pass
            return   # notifications: progress, logs -- nothing to act on
        rid = message.get("id")
        with self._cond:
            if rid in self._pending:
                self._responses[rid] = message
                self._cond.notify_all()


# --- over streamable HTTP (D-081) --------------------------------------------------

# Visible ASCII, which is all a header value may carry here.
_HEADER_VALUE = re.compile(r"[\x21-\x7e]{1,1024}\Z")
# How long ending a session, or telling the server a call was given up on,
# may take: neither is waited for past it.
QUIET_TIMEOUT = 2.0
_JSON = "application/json"
_EVENTS = "text/event-stream"


class _Exchange:
    """The connection one request is waiting on, so the thread that gave up
    on it can cut it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: http.client.HTTPConnection | None = None
        self._aborted = False

    def attach(self, conn: http.client.HTTPConnection) -> None:
        """Called once the request is sent, so the connection has its socket."""
        with self._lock:
            self._conn = conn
            aborted = self._aborted
        if aborted:
            raise McpError("given up on")

    def abort(self) -> None:
        with self._lock:
            self._aborted = True
            conn = self._conn
        sock = conn.sock if conn is not None else None
        if sock is None:
            return   # not sent yet: attach() ends it
        # Windows does not wake a read blocked on a socket that is shut down;
        # closing its handle does. Detached first, so the socket object no
        # longer names the handle and nothing closes it -- or a later socket
        # given the same number -- a second time.
        try:
            socket.close(sock.detach())
        except OSError:
            pass


class StreamableHttpClient(_Protocol):
    """A server already running somewhere, reached at its address over the
    streamable HTTP transport (D-081), as codex's ``url`` servers are.

    Each message is a POST of its own; the reply to a request is either one
    JSON body or an event stream that ends with it, and a request from the
    server inside that stream is answered with another POST. The session id
    the server hands out at initialize goes with every message after it, and
    closing the session ends it on the server too.

    A bearer token comes from the environment variable the entry names: read
    when the server is started, held in memory for the Authorization header
    only, and registered with the redactor, so a server that echoes it -- or
    an error that quotes it -- shows ``[redacted]`` instead.

    http.client cannot interrupt a read, so each request runs on a thread of
    its own while the caller waits, watching for Ctrl+C and the timeout; on
    either the connection is cut and the server told the request was given
    up on, as over stdio.
    """

    def __init__(self, server: McpServerConfig, environ: dict[str, str] | None = None, *,
                 http: HttpClient | None = None, redactor: Redactor | None = None) -> None:
        self.server = server
        self._environ = dict(os.environ if environ is None else environ)
        self._http = http or HttpClient()
        self._redactor = redactor or default_redactor()
        self._token = ""
        self._sent: dict[str, str] = {}
        self._session = ""
        self._protocol = ""
        self._lock = threading.Lock()
        self._next_id = 0
        self._closed = False
        self._stopped = ""
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}

    def start(self) -> None:
        variable = self.server.bearer_token_env_var
        if variable:
            token = self._environ.get(variable, "")
            if not token:
                raise McpError(f"{variable}, which bearer_token_env_var names, is not set")
            if not _HEADER_VALUE.match(token):
                # http.client would refuse it with a message quoting it.
                raise McpError(f"the token in {variable} has characters a header cannot "
                               "carry")
            self._redactor.register(token)
            self._token = token
        self._sent = self._user_headers()
        version = self._handshake()
        if not _HEADER_VALUE.match(version):
            raise McpError("its protocol version cannot be sent back in a header")
        self._protocol = version
        self.notify("notifications/initialized")

    @property
    def running(self) -> bool:
        return not self._closed and not self._stopped

    def stderr_tail(self) -> str:
        return ""   # a server Foundry did not start has no output of its own here

    def close(self) -> None:
        """End the session on the server too, as the protocol asks; one that
        does not allow it answers 405, and one that does not answer is not
        waited for."""
        if self._closed:
            return
        self._closed = True
        if not self._session or self._stopped:
            return
        # On a thread, waited for QUIET_TIMEOUT in all: the connection's
        # timeout is each read's, and a server that trickles its answer a
        # byte at a time held the session's exit as long as it liked (D-155).
        ending = threading.Thread(target=self._end_session, daemon=True,
                                  name=f"mcp-{self.server.name}-close")
        ending.start()
        ending.join(QUIET_TIMEOUT)

    def _end_session(self) -> None:
        try:
            conn, path, proxy_headers = self._http.connect(self.server.url)
            conn.timeout = QUIET_TIMEOUT
            try:
                conn.request("DELETE", path, headers={**proxy_headers, **self._headers()})
                conn.getresponse().read(4096)
            finally:
                conn.close()
        except (FoundryError, http.client.HTTPException, OSError):
            pass

    def _user_headers(self) -> dict[str, str]:
        """The entry's own headers (D-111), as codex's: those it gives, and
        those whose variable is set -- read now, held in memory, registered
        with the redactor as a token is."""
        headers = dict(self.server.http_headers)
        for header, variable in self.server.env_http_headers:
            value = self._environ.get(variable, "")
            if not value:
                continue   # an unset variable leaves the header out
            if not HEADER_VALUE.match(value):
                # http.client would refuse it with a message quoting it.
                raise McpError(f"the value in {variable} has characters the {header} "
                               "header cannot carry")
            self._redactor.register(value)
            headers[header] = value
        return headers

    # -- messages --------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {**self._sent, "Accept": f"{_JSON}, {_EVENTS}"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        if self._protocol:
            headers["MCP-Protocol-Version"] = self._protocol
        return headers

    def notify(self, method: str, params: dict | None = None, *,
               timeout: float | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._deliver(message, self.server.startup_timeout_s if timeout is None else timeout)

    def _deliver(self, message: dict, timeout: float) -> None:
        """POST a message that has no reply: a notification, or the answer to
        a request of the server's."""
        try:
            conn, path, proxy_headers = self._http.connect(self.server.url)
        except FoundryError as exc:
            raise McpError(str(exc)) from exc
        conn.timeout = timeout
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        try:
            conn.request("POST", path, body=body, headers={
                "Content-Type": _JSON, **proxy_headers, **self._headers()})
            raw = conn.getresponse()
            self._check_status(raw)
            raw.read(64 * 1024)
        except (http.client.HTTPException, OSError) as exc:
            raise McpError(f"the connection failed: {exc}") from exc
        finally:
            conn.close()

    def _say_given_up(self, rid: int, reason: str) -> None:
        def send() -> None:
            try:
                self.notify("notifications/cancelled", {"requestId": rid, "reason": reason},
                            timeout=QUIET_TIMEOUT)
            except Exception:  # noqa: BLE001 - nothing to do about it, and no one waiting
                pass
        threading.Thread(target=send, daemon=True,
                         name=f"mcp-{self.server.name}-cancel").start()

    def request(self, method: str, params: dict | None = None, *, timeout: float,
                cancelled: Callable[[], bool] | None = None) -> dict:
        if self._stopped or self._closed:
            raise McpError(self._stopped or "the session with the server is closed")
        with self._lock:
            self._next_id += 1
            rid = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        exchange = _Exchange()
        outcome: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                outcome["reply"] = self._exchange(message, exchange, timeout)
            except BaseException as exc:  # noqa: BLE001 - handed to the waiting thread
                outcome["error"] = exc
            finally:
                done.set()

        threading.Thread(target=run, daemon=True, name=f"mcp-{self.server.name}-http").start()
        deadline = time.monotonic() + timeout
        try:
            while not done.wait(0.05):
                if cancelled is not None and cancelled():
                    break
                if time.monotonic() >= deadline:
                    break
        except BaseException:
            exchange.abort()
            self._say_given_up(rid, "interrupted")
            raise
        if not done.is_set():
            exchange.abort()
            interrupted = cancelled is not None and cancelled()
            self._say_given_up(rid, "interrupted" if interrupted else "timed out")
            if interrupted:
                raise Cancelled(f"{method} interrupted")
            raise McpError(f"{method} timed out after {timeout:g}s")
        error = outcome.get("error")
        if isinstance(error, McpError):
            raise error
        if error is not None:
            raise McpError(f"{method} failed: {error}") from error
        return _result(method, outcome["reply"])

    def _exchange(self, message: dict, exchange: _Exchange, timeout: float) -> dict:
        """POST a request and read its reply: on its own thread."""
        conn, path, proxy_headers = self._http.connect(self.server.url)
        # The waiting thread decides when a request has taken too long, and
        # tells the server; these only bound a thread no one waits for.
        conn.timeout = min(timeout + QUIET_TIMEOUT, self._http.connect_timeout)
        try:
            body = json.dumps(message, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=body, headers={
                "Content-Type": _JSON, **proxy_headers, **self._headers()})
            conn.sock.settimeout(timeout + QUIET_TIMEOUT)
            exchange.attach(conn)
            raw = conn.getresponse()
            if message["method"] == "initialize":
                self._take_session(raw.getheader("Mcp-Session-Id"))
            self._check_status(raw)
            kind = (raw.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if raw.status == 202:
                raise McpError(f"{message['method']} was accepted with no reply")
            if kind == _JSON:
                return self._json_reply(raw, message["id"])
            if kind == _EVENTS:
                return self._event_reply(raw, message["id"])
            raise McpError(f"its reply was {kind or 'of no content type'}, not JSON or an "
                           "event stream")
        except (http.client.HTTPException, OSError) as exc:
            raise McpError(f"the connection failed: {exc}") from exc
        finally:
            conn.close()

    def _take_session(self, value: str | None) -> None:
        if value is None:
            return   # a server that keeps no session
        if not _HEADER_VALUE.match(value):
            raise McpError("its session id cannot be sent back in a header")
        self._session = value

    def _check_status(self, raw: http.client.HTTPResponse) -> None:
        status = raw.status
        if status < 400:
            return
        said = " ".join(raw.read(2000).decode("utf-8", errors="replace").split())[:200]
        if status == 404 and self._session:
            # The session is gone -- the server restarted, or ended it.
            self._stopped = "the server ended this session"
            raise McpError(f"{self._stopped} (HTTP 404)")
        if status in (401, 403):
            if self._token:
                raise McpError(f"the server refused the token (HTTP {status})")
            raise McpError(f"the server refused the request (HTTP {status}); it may want a "
                           "token, named by bearer_token_env_var")
        raise McpError(f"HTTP {status}" + (f": {said}" if said else ""))

    def _json_reply(self, raw: http.client.HTTPResponse, rid: int) -> dict:
        data = raw.read(MAX_MESSAGE_BYTES + 1)
        if len(data) > MAX_MESSAGE_BYTES:
            raise McpError(f"a reply over {MAX_MESSAGE_BYTES} bytes")
        try:
            message = without_lone_surrogates(json.loads(data.decode("utf-8")))
        except ValueError as exc:
            raise McpError("its reply was not JSON") from exc
        reply = self._take(message, rid)
        if reply is None:
            raise McpError("its reply answered another request")
        return reply

    def _event_reply(self, raw: http.client.HTTPResponse, rid: int) -> dict:
        """Read events until the one that answers ``rid``."""
        data: list[str] = []
        size = 0
        while True:
            line = raw.readline(MAX_MESSAGE_BYTES + 1)
            if len(line) > MAX_MESSAGE_BYTES:
                raise McpError(f"an event over {MAX_MESSAGE_BYTES} bytes")
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text:
                # A blank line ends an event; the end of the stream ends the last.
                if data:
                    reply = self._event("\n".join(data), rid)
                    data, size = [], 0
                    if reply is not None:
                        return reply
                if not line:
                    raise McpError("its event stream ended without the reply")
                continue
            field_name, _, value = text.partition(":")
            if field_name != "data":
                continue   # a comment, or event, id or retry: nothing Foundry needs
            value = value[1:] if value.startswith(" ") else value
            size += len(value) + 1
            if size > MAX_MESSAGE_BYTES:
                raise McpError(f"an event over {MAX_MESSAGE_BYTES} bytes")
            data.append(value)

    def _event(self, text: str, rid: int) -> dict | None:
        try:
            message = without_lone_surrogates(json.loads(text))
        except ValueError:
            return None   # not a message: nothing to act on
        return self._take(message, rid)

    def _take(self, message: Any, rid: int) -> dict | None:
        """The reply to ``rid`` among what the server sent, answering any
        request of its own on the way."""
        for item in message if isinstance(message, list) else [message]:
            if not isinstance(item, dict):
                continue
            if "method" in item:
                if "id" in item:
                    try:
                        self._deliver(_answer_for(item), QUIET_TIMEOUT)
                    except McpError:
                        pass
                continue   # notifications: progress, logs -- nothing to act on
            if item.get("id") == rid:
                return item
        return None


Client = StdioClient | StreamableHttpClient


def _client(server: McpServerConfig, cwd: Path,
            environ: dict[str, str] | None) -> Client:
    if server.url:
        return StreamableHttpClient(server, environ)
    return StdioClient(server, cwd, environ)


# --- a server's tool, as Foundry offers it ----------------------------------------


def render_result(result: dict, images: list | None = None, name: str = "") -> str:
    """The text of a tools/call result. Content Foundry cannot show is named,
    not dropped, so the model knows it was there. Given an ``images`` list,
    image content that passes the checks is collected into it instead (D-045)."""
    parts: list[str] = []
    content = result.get("content")
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif kind == "image" and images is not None:
            if len(images) >= MAX_IMAGES_PER_MESSAGE:
                parts.append(f"[image content not shown: more than {MAX_IMAGES_PER_MESSAGE} "
                             "images in one result]")
                continue
            try:
                picture = image_from_base64(item.get("data"), name)
            except ImageRejected as exc:
                parts.append(f"[image content not shown: {exc}]")
                continue
            images.append(picture.block)
            parts.append(f"[image {len(images)} attached ({picture.describe()})]")
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} content ({item.get('mimeType', 'unknown type')}) not shown]")
        elif kind == "resource_link":
            parts.append(f"[resource link: {item.get('uri', '?')}]")
        elif kind == "resource" and isinstance(item.get("resource"), dict):
            resource = item["resource"]
            if isinstance(resource.get("text"), str):
                parts.append(resource["text"])
            else:
                parts.append(f"[binary resource {resource.get('uri', '?')} not shown]")
        else:
            parts.append(f"[{kind or 'unknown'} content not shown]")
    if not parts and "structuredContent" in result:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=2))
    return "\n".join(parts)


@dataclass(slots=True)
class McpTool:
    name: str
    kind: ToolKind
    client: Client
    server: str
    remote: str
    description: str
    parameters: dict[str, Any]

    def schema(self) -> ToolSchema:
        return ToolSchema(name=self.name, description=self.description,
                          parameters=self.parameters)

    def validate(self, args: dict[str, Any]) -> Operation:
        if not isinstance(args, dict):
            raise InvalidToolCall(f"{self.name} takes an object of arguments")
        required = self.parameters.get("required")
        # A schema's own word, not checked when it was listed: `"required": 5`
        # raised a TypeError at every call, and ended the session (D-155).
        missing = [k for k in (required if isinstance(required, list) else [])
                   if isinstance(k, str) and k not in args]
        if missing:
            raise InvalidToolCall(f"{self.name} needs {', '.join(missing)}")
        text = json.dumps(args, sort_keys=True, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            raise InvalidToolCall(f"{self.name}: arguments over {MAX_ARGUMENT_BYTES} bytes")
        shown = text if len(text) <= 300 else text[:297] + "..."
        return Operation(
            tool=self.name, kind=self.kind, args=args,
            display=f"{self.server}: {self.remote} {shown}",
            # Rules match on the arguments, so a rule can allow a tool only
            # for some of them.
            target=text,
            digest=hashlib.sha256(f"{self.name}\x00{text}".encode("utf-8")).hexdigest()[:16],
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            result = self.client.call_tool(self.remote, op.args, cancelled=ctx.cancelled)
        except McpError as exc:
            raise ToolError(f"MCP server {self.server}: {exc}") from exc
        images: list | None = [] if ctx.images else None
        text = render_result(result, images, f"{self.server}: {self.remote}") or "(no output)"
        body, truncated = truncate_middle(text, ctx.max_output_bytes)
        artifact_id = ""
        if truncated and ctx.artifacts is not None:
            artifact_id = ctx.artifacts.put_text(text).artifact_id
            body += f"\n\n[full output saved; read_artifact artifact_id=\"{artifact_id}\"]"
        return ToolOutput(content=body, is_error=result.get("isError") is True,
                          truncated=truncated, artifact_id=artifact_id,
                          metadata={"mcp_server": self.server, "mcp_tool": self.remote},
                          images=tuple(images or ()))


def _tool(client: Client, spec: dict, taken: set[str]) -> McpTool | str:
    """The tool, or why it is not offered."""
    remote = spec.get("name")
    if not isinstance(remote, str) or not remote:
        return "a tool with no name"
    name = tool_name(client.server.name, remote)
    if name in taken:
        return f"{remote}: its name {name} is taken"
    schema = spec.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        schema = dict(_EMPTY_SCHEMA)
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        return f"{remote}: its argument schema is over {MAX_SCHEMA_BYTES} bytes"
    description = spec.get("description") if isinstance(spec.get("description"), str) else ""
    description = (" ".join(description.split())[:MAX_DESCRIPTION_CHARS]
                   + f" (from the MCP server {client.server.name!r})").strip()
    kind = ToolKind.READ_ONLY if client.server.read_only else ToolKind.MUTATOR
    return McpTool(name=name, kind=kind, client=client, server=client.server.name,
                   remote=remote, description=description, parameters=schema)


# --- every configured server -------------------------------------------------------


@dataclass(slots=True)
class McpServers:
    clients: list[Client] = field(default_factory=list)
    # server -> the names of its tools, as offered
    tools: dict[str, list[str]] = field(default_factory=dict)
    # server -> why it is not available
    failures: dict[str, str] = field(default_factory=dict)
    # tools a server listed that are not offered, and why
    skipped: list[str] = field(default_factory=list)
    # server -> its tools the user's enabled_tools / disabled_tools leave out
    # (D-080): the user's choice, not a warning
    left_out: dict[str, list[str]] = field(default_factory=dict)
    # the servers whose resources the model can list and read (D-102)
    resources: list[str] = field(default_factory=list)

    def close(self) -> None:
        for client in self.clients:
            client.close()

    def summary(self) -> str:
        parts = [f"{name}: {len(names)} tool(s)" for name, names in self.tools.items()]
        parts += [f"{name}: not available ({why})" for name, why in self.failures.items()]
        return "; ".join(parts)


def start_servers(servers: dict[str, McpServerConfig], cwd: Path, registry, *,
                  environ: dict[str, str] | None = None) -> McpServers:
    """Start every enabled server at once, and register the tools of those
    that came up. A server that fails is reported, never fatal."""
    found = McpServers()
    enabled = [s for s in servers.values() if s.enabled]
    clients = {s.name: _client(s, cwd, environ) for s in enabled}
    results: dict[str, list[dict] | str] = {}

    def run(client: Client) -> None:
        try:
            client.start()
            results[client.server.name] = client.list_tools()
        except FoundryError as exc:
            results[client.server.name] = str(exc)
        except Exception as exc:  # noqa: BLE001 - a broken server must not end the session
            results[client.server.name] = f"{type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=run, args=(c,), daemon=True) for c in clients.values()]
    for thread in threads:
        thread.start()
    for thread, client in zip(threads, clients.values()):
        # start() bounds each request; this bounds a server that hangs between them.
        thread.join(client.server.startup_timeout_s * 2 + 5)

    taken = set(registry.names())
    for server in enabled:
        client = clients[server.name]
        outcome = results.get(server.name, "it did not answer in time")
        if isinstance(outcome, str):
            found.failures[server.name] = outcome
            client.close()
            continue
        found.clients.append(client)
        names = found.tools.setdefault(server.name, [])
        # A name the server does not list is a typo that would quietly hide a
        # tool the user wanted, or offer one they did not (D-080).
        listed = {spec.get("name") for spec in outcome}
        for setting, wanted in (("enabled_tools", server.enabled_tools or ()),
                                ("disabled_tools", server.disabled_tools)):
            for remote in wanted:
                if remote not in listed:
                    found.skipped.append(f"{server.name}: {setting} names {remote!r}, "
                                         "which the server does not offer")
        for spec in outcome:
            remote = spec.get("name")
            if isinstance(remote, str) and not server.offers(remote):
                found.left_out.setdefault(server.name, []).append(remote)
                continue
            tool = _tool(client, spec, taken)
            if isinstance(tool, str):
                found.skipped.append(f"{server.name}: {tool}")
                continue
            registry.register(tool)
            taken.add(tool.name)
            names.append(tool.name)
    # Imported here: the resource tools are built on this module's clients.
    from foundry.core.mcp_resources import register as register_resources
    found.resources = register_resources(found.clients, registry, taken)
    return found
