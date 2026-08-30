"""Append-only session journal plus the content-addressed artifact store.

The journal is the single source of truth: every model request, tool call,
policy decision, approval, command, and termination lands here. Two properties
follow from that and drive the design:

* a request must be reconstructable from the journal, which is what makes the
  replay backend (and, later, resume) possible;
* a journal whose last line is truncated must still be readable, and a journal
  with no termination record must read as ``interrupted`` -- never as success.

Every write goes through the redactor. Nothing else in the codebase may open
these files for writing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from foundry.core.conversation import IR_VERSION
from foundry.core.events import PROTOCOL_VERSION, TerminalStatus
from foundry.core.redaction import Redactor, default_redactor

SCHEMA_VERSION = 1


class EventType:
    SESSION_META = "session_meta"
    GIT_BASELINE = "git_baseline"
    CAPABILITY_PROBE = "capability_probe"
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    POLICY_DECISION = "policy_decision"
    APPROVAL = "approval"
    COMMAND_EXEC = "command_exec"
    TOKEN_USAGE = "token_usage"
    VALIDATION_CLAIM = "validation_claim"
    NOTICE = "notice"
    ERROR = "error"
    TERMINATION = "termination"


# Types whose loss on a crash would break the evidence chain: flushed on write.
_FLUSH_TYPES = frozenset(
    {EventType.TERMINATION, EventType.APPROVAL, EventType.COMMAND_EXEC, EventType.POLICY_DECISION}
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True, slots=True)
class JournalRecord:
    ts: str
    ordinal: int
    type: str
    v: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    size: int
    media_type: str
    truncated: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "size": self.size,
            "media_type": self.media_type,
            "truncated": self.truncated,
        }


class ArtifactStore:
    """Content-addressed by sha256. Handed out as opaque ids: ``read_artifact``
    resolves them through the session's own index, never as a path."""

    def __init__(self, root: Path, redactor: Redactor | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._redactor = redactor or default_redactor()
        self._index: dict[str, Path] = {}

    def put_bytes(self, data: bytes, *, media_type: str = "application/octet-stream",
                  truncated: bool = False) -> ArtifactRef:
        data = self._redactor.scrub_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / digest
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        self._index[digest] = path
        return ArtifactRef(artifact_id=digest, size=len(data), media_type=media_type,
                           truncated=truncated)

    def put_text(self, text: str, *, media_type: str = "text/plain", truncated: bool = False) -> ArtifactRef:
        return self.put_bytes(self._redactor.scrub(text).encode("utf-8"),
                              media_type=media_type, truncated=truncated)

    def has(self, artifact_id: str) -> bool:
        return artifact_id in self._index

    def read_text(self, artifact_id: str, *, offset: int = 0, limit: int | None = None) -> str:
        """Only ids this session issued resolve. An unknown id is an error, not
        a path lookup -- otherwise this becomes a second unrestricted reader."""
        path = self._index.get(artifact_id)
        if path is None:
            raise KeyError(f"unknown artifact id: {artifact_id}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if offset:
            text = text[offset:]
        if limit is not None:
            text = text[:limit]
        return text


class SessionStore:
    """One journal file plus its artifact directory."""

    def __init__(self, root: Path, session_id: str | None = None,
                 redactor: Redactor | None = None) -> None:
        self.session_id = session_id or new_session_id()
        self.dir = root / self.session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "events.jsonl"
        self.artifacts = ArtifactStore(self.dir / "artifacts", redactor)
        self._redactor = redactor or default_redactor()
        self._ordinal = 0
        self._fh = self.path.open("a", encoding="utf-8", newline="\n")
        self._terminated = False
        # Set to the first write failure's description; the runtime surfaces it
        # so a session whose evidence is incomplete says so.
        self.degraded: str = ""

    # -- writing ----------------------------------------------------------

    def append(self, event_type: str, payload: dict[str, Any]) -> int:
        """Record one event. A failing journal degrades the session, never
        crashes it.

        A full volume or a file held by a backup agent used to raise out of the
        tool dispatch, through the CLI's ``finally`` (which raised again writing
        the termination record), past ``main``'s FoundryError handler -- a raw
        traceback and exit 1 instead of one of the documented exit codes, with
        buffered records lost. The journal being unwritable is worth reporting;
        it is not worth losing the session over.
        """
        self._ordinal += 1
        record = {
            "ts": utc_now_iso(),
            "ordinal": self._ordinal,
            "type": event_type,
            "v": SCHEMA_VERSION,
            "payload": self._redactor.scrub_obj(payload),
        }
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            if event_type in _FLUSH_TYPES:
                self._fh.flush()
                os.fsync(self._fh.fileno())
        except (OSError, ValueError) as exc:
            if not self.degraded:
                self.degraded = f"{type(exc).__name__}: {exc}"
        return self._ordinal

    def write_header(self, *, workspace: str, profile: str, model: str,
                     foundry_version: str) -> None:
        self.append(EventType.SESSION_META, {
            "schema_version": SCHEMA_VERSION,
            # Declared but never written, these three were a promise of forward
            # compatibility that no reader could act on. A replay tool has to
            # know which IR, event protocol and policy vintage produced a
            # journal before it can decide whether it understands the file.
            "ir_version": IR_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "foundry_version": foundry_version,
            "session_id": self.session_id,
            "workspace": workspace,
            "profile": profile,
            "model": model,
        })

    def record_command(self, *, argv: list[str], cwd: str, exit_code: int | None,
                       duration_ms: int, stdout: bytes, stderr: bytes,
                       truncated: bool = False) -> int:
        """Command output is stored as redacted raw bytes (base64) so a wrong
        decode can be redone later without losing the evidence."""
        return self.append(EventType.COMMAND_EXEC, {
            "argv": argv,
            "cwd": cwd,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "stdout_b64": base64.b64encode(self._redactor.scrub_bytes(stdout)).decode("ascii"),
            "stderr_b64": base64.b64encode(self._redactor.scrub_bytes(stderr)).decode("ascii"),
            "truncated": truncated,
        })

    def record_termination(self, status: TerminalStatus, reason: str, summary: str = "") -> int:
        ordinal = self.append(EventType.TERMINATION, {
            "status": status.value,
            "reason": reason,
            "summary": summary,
        })
        self._terminated = True
        return ordinal

    def close(self) -> None:
        if not self._fh.closed:
            try:
                self._fh.flush()
            except (OSError, ValueError):
                pass
            self._fh.close()

    def __enter__(self) -> SessionStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- reading ----------------------------------------------------------

    @staticmethod
    def read_records(path: Path) -> Iterator[JournalRecord]:
        """Tolerates a truncated final line: a process killed mid-write must not
        make the whole journal unreadable."""
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # truncated tail
                yield JournalRecord(
                    ts=obj.get("ts", ""),
                    ordinal=obj.get("ordinal", 0),
                    type=obj.get("type", ""),
                    v=obj.get("v", 0),
                    payload=obj.get("payload", {}),
                )

    @staticmethod
    def terminal_status(path: Path) -> TerminalStatus:
        """A journal with no termination record is ``interrupted``. This is the
        rule that keeps a crashed run from ever reading as completed."""
        status = TerminalStatus.INTERRUPTED
        for record in SessionStore.read_records(path):
            if record.type == EventType.TERMINATION:
                raw = record.payload.get("status", "")
                try:
                    status = TerminalStatus(raw)
                except ValueError:
                    status = TerminalStatus.INTERRUPTED
        return status


class AuditLog:
    """A second, append-only line per tool decision, outside any session.

    With no sandbox this is the honest compensating control: every ALLOW, ASK
    outcome, and DENY is recoverable even if a session journal is deleted. File
    tools deny writes to this path.
    """

    def __init__(self, path: Path, redactor: Redactor | None = None) -> None:
        self.path = path
        self.degraded: str = ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.degraded = f"{type(exc).__name__}: {exc}"
        self._redactor = redactor or default_redactor()

    def record(self, *, workspace: str, tool: str, target: str, decision: str,
               outcome: str) -> None:
        """Append one decision. Degrades rather than crashing.

        Same contract as SessionStore.append: a file held by a backup agent or
        a full volume used to raise out of the tool dispatch and kill the
        process with a traceback instead of a documented exit code. Because the
        audit log is the stated compensating control for having no sandbox, a
        run whose trail is incomplete says so in its summary rather than
        failing silently.
        """
        line = json.dumps({
            "ts": utc_now_iso(),
            "workspace": workspace,
            "tool": tool,
            "target": self._redactor.scrub(target),
            "decision": decision,
            "outcome": outcome,
        }, ensure_ascii=False)
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            if not self.degraded:
                self.degraded = f"{type(exc).__name__}: {exc}"
