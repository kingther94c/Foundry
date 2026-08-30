"""The runtime's outward protocol: events out, ops in.

The CLI is one subscriber among future others (headless runner, IDE bridge), so
nothing here may know about terminals. Approval is an event/response pair rather
than a blocking prompt inside a tool, which is what lets a user interrupt while
an approval is pending and lets headless mode answer every ASK with DENY.

Frozen at M0 (design.md section 3). Additive changes only.
"""

from __future__ import annotations

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


Op = UserInput | ApprovalDecision | Interrupt | Shutdown


@dataclass(slots=True)
class EventSink:
    """Where the runtime publishes. Subscribers are plain callables so tests can
    collect events without any async plumbing."""

    _subscribers: list = field(default_factory=list)

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def emit(self, event: Event) -> None:
        for callback in self._subscribers:
            callback(event)
