"""The runtime's outward protocol: events out, ops in.

The CLI is one subscriber among future others (headless runner, IDE bridge), so
nothing here may know about terminals. Approval is an event/response pair rather
than a blocking prompt inside a tool, which is what lets a user interrupt while
an approval is pending and lets headless mode answer every ASK with DENY.

Frozen at M0 (design.md section 3). Additive changes only.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from foundry.core.conversation import Usage

PROTOCOL_VERSION = 1


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# --- terminal status ------------------------------------------------------


class TerminalStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Never produced by a live run: assigned when reading a journal that has no
    # termination record, so a crashed session can't be mistaken for success.
    INTERRUPTED = "interrupted"


EXIT_CODES: dict[TerminalStatus, int] = {
    TerminalStatus.COMPLETED: 0,
    TerminalStatus.PARTIAL: 10,
    TerminalStatus.BLOCKED: 11,
    TerminalStatus.FAILED: 12,
    TerminalStatus.CANCELLED: 13,
    TerminalStatus.INTERRUPTED: 14,
}


# --- approval -------------------------------------------------------------


class ApprovalChoice(str, Enum):
    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"
    DENY = "deny"
    ABORT = "abort"


class ApprovalKind(str, Enum):
    COMMAND = "command"
    PATCH = "patch"
    OTHER = "other"


# --- events (runtime -> subscribers) --------------------------------------


@dataclass(frozen=True, slots=True)
class TurnStarted:
    turn_index: int
    kind: Literal["turn_started"] = "turn_started"


@dataclass(frozen=True, slots=True)
class MessageDelta:
    text: str
    kind: Literal["message_delta"] = "message_delta"


@dataclass(frozen=True, slots=True)
class ToolBegin:
    call_id: str
    tool: str
    display: str
    kind: Literal["tool_begin"] = "tool_begin"


@dataclass(frozen=True, slots=True)
class ToolOutputDelta:
    call_id: str
    text: str
    stream: Literal["stdout", "stderr"] = "stdout"
    kind: Literal["tool_output_delta"] = "tool_output_delta"


@dataclass(frozen=True, slots=True)
class ToolEnd:
    call_id: str
    tool: str
    ok: bool
    summary: str
    duration_ms: int = 0
    kind: Literal["tool_end"] = "tool_end"


@dataclass(frozen=True, slots=True)
class ToolRejected:
    """A tool call that never ran: malformed, denied by policy, or declined.

    Without this the most safety-relevant thing the system does -- refusing a
    destructive command -- produced no output at all, and a headless run could
    not be told apart from one where everything was blocked.
    """

    call_id: str
    tool: str
    display: str
    reason: str
    rule_id: str = ""
    step: int = -1
    kind: Literal["tool_rejected"] = "tool_rejected"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """What the user is shown must be what runs: ``display`` is rendered from
    the same normalized operation the executor receives."""

    request_id: str
    approval_kind: ApprovalKind
    tool: str
    display: str
    reason: str
    detail: str = ""
    options: tuple[ApprovalChoice, ...] = (
        ApprovalChoice.ONCE,
        ApprovalChoice.SESSION,
        ApprovalChoice.ALWAYS,
        ApprovalChoice.DENY,
        ApprovalChoice.ABORT,
    )
    kind: Literal["approval_request"] = "approval_request"


@dataclass(frozen=True, slots=True)
class TokenCount:
    usage: Usage
    session_total: Usage
    kind: Literal["token_count"] = "token_count"


@dataclass(frozen=True, slots=True)
class TurnComplete:
    turn_index: int
    text: str
    kind: Literal["turn_complete"] = "turn_complete"


@dataclass(frozen=True, slots=True)
class Termination:
    status: TerminalStatus
    reason: str
    summary: str = ""
    kind: Literal["termination"] = "termination"


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    message: str
    category: str = "internal"
    fatal: bool = False
    kind: Literal["error"] = "error"


@dataclass(frozen=True, slots=True)
class Notice:
    """Runtime-to-user information that is not an error: disclosure banner,
    dirty-worktree warning, capability degradation."""

    text: str
    level: Literal["info", "warning"] = "info"
    kind: Literal["notice"] = "notice"


Event = (
    TurnStarted
    | MessageDelta
    | ToolBegin
    | ToolOutputDelta
    | ToolEnd
    | ToolRejected
    | ApprovalRequest
    | TokenCount
    | TurnComplete
    | Termination
    | ErrorEvent
    | Notice
)


# --- ops (subscribers -> runtime) -----------------------------------------


@dataclass(frozen=True, slots=True)
class UserInput:
    text: str
    kind: Literal["user_input"] = "user_input"


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    request_id: str
    choice: ApprovalChoice
    kind: Literal["approval_decision"] = "approval_decision"


@dataclass(frozen=True, slots=True)
class Interrupt:
    reason: str = "user interrupt"
    kind: Literal["interrupt"] = "interrupt"


@dataclass(frozen=True, slots=True)
class Shutdown:
    kind: Literal["shutdown"] = "shutdown"


# The inbound half of the frozen protocol. No subscriber sends ops yet -- the
# CLI answers approvals through a callback -- but the type is what an IDE bridge
# or headless runner binds to, and dropping it would leave the docstring's
# "events out, ops in" describing a one-way interface.
Op = UserInput | ApprovalDecision | Interrupt | Shutdown


@dataclass(slots=True)
class EventSink:
    """Where the runtime publishes. Subscribers are plain callables so tests can
    collect events without any async plumbing.

    Every event passes the redactor on its way out. This is the sink
    redaction.py names second, and it was the one that had no implementation:
    the journal wrote ``[redacted]`` while ``foundry exec --json`` printed the
    same credential verbatim to stdout, so redirecting a run into a CI log
    captured exactly what the audited journal refused to keep.
    """

    _subscribers: list = field(default_factory=list)
    redactor: object | None = None
    _held: str = ""

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def emit(self, event: Event) -> None:
        for ready in self._prepare(event):
            for callback in self._subscribers:
                callback(ready)

    def _prepare(self, event: Event) -> list[Event]:
        """Scrub, holding back the tail of a text stream.

        Scrubbing each delta on its own is not enough: the model's text arrives
        in arbitrary chunks, so a credential it echoes almost always straddles
        two of them and neither fragment matches anything. The renderer then
        reassembles it on screen. So text deltas keep a tail that could still
        turn out to be the start of a secret, and release it once the next chunk
        proves it is not.
        """
        if self.redactor is None:
            return [event]

        if isinstance(event, MessageDelta):
            # The buffer is kept RAW. Storing the scrubbed text back was a hole:
            # the best-effort patterns are unanchored with {20,} minimums, so on
            # a still-growing buffer they matched the shortest valid PREFIX of a
            # registered value, replaced it, and destroyed the exact-match state
            # -- after which the rest of that credential streamed out in the
            # clear. Measured: 28 of a 50-character `sk-` key reached every
            # subscriber, the renderer and `exec --json` alike.
            self._held += event.text
            cut = self._safe_cut(self._held)
            if cut <= 0:
                return []
            emit, self._held = self._held[:cut], self._held[cut:]
            return [dataclasses.replace(event, text=self.redactor.scrub(emit))]

        # Anything else ends the run of text: release what was held, in order.
        pending: list[Event] = []
        if self._held:
            pending.append(MessageDelta(text=self.redactor.scrub(self._held)))
            self._held = ""
        pending.append(self._scrub(event))
        return pending

    def _hold_size(self) -> int:
        """Enough to span the longest credential Foundry actually holds, which
        is the removal this module guarantees. A pattern match longer than this
        can still straddle -- pattern scanning is best-effort by design."""
        longest = getattr(self.redactor, "longest_registered", 0)
        return max(int(longest or 0), 64)

    def _safe_cut(self, buffer: str) -> int:
        """How much of the raw buffer can be released now.

        Two conditions. Keep a tail long enough that a value still arriving is
        not emitted piecemeal, and never cut *through* a registered value that
        is already complete -- otherwise its head would be scrubbed in one delta
        and its tail released untouched in the next.
        """
        cut = len(buffer) - self._hold_size()
        if cut <= 0:
            return 0
        for value in getattr(self.redactor, "registered_values", lambda: ())():
            start = buffer.find(value)
            while start != -1:
                if start < cut < start + len(value):
                    cut = start
                start = buffer.find(value, start + 1)
        return max(cut, 0)

    def flush(self) -> None:
        """Release any held text. Called when a stream ends without a following
        event, so nothing is lost at shutdown."""
        if self._held and self.redactor is not None:
            text = self.redactor.scrub(self._held)
            self._held = ""
            for callback in self._subscribers:
                callback(MessageDelta(text=text))

    def _scrub(self, event: Event) -> Event:
        if self.redactor is None:
            return event
        # Exact type, not isinstance: ApprovalChoice and TerminalStatus are str
        # subclasses, and rewriting one into a plain string would break every
        # subscriber that compares against the enum.
        changed = {}
        for f in dataclasses.fields(event):
            value = getattr(event, f.name)
            if type(value) is not str or not value:
                continue
            scrubbed = self.redactor.scrub(value)
            if scrubbed != value:
                changed[f.name] = scrubbed
        return dataclasses.replace(event, **changed) if changed else event
