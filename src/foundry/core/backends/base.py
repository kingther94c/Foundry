"""The ModelBackend contract.

An adapter translates between the IR and one wire protocol. It never calls a
tool, never consults policy, and never runs a loop of its own -- those belong to
AgentRuntime, and a provider that owned them would fork the runtime in practice.

Frozen at M0 (design.md section 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal, Protocol, runtime_checkable

from foundry.core.conversation import Capabilities, ModelTurn, ToolUseBlock, TurnRequest, Usage


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str
    kind: Literal["text_delta"] = "text_delta"


@dataclass(frozen=True, slots=True)
class ToolCallComplete:
    call: ToolUseBlock
    kind: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True, slots=True)
class UsageUpdate:
    usage: Usage
    kind: Literal["usage"] = "usage"


@dataclass(frozen=True, slots=True)
class TurnFinished:
    turn: ModelTurn
    kind: Literal["turn_finished"] = "turn_finished"


StreamEvent = TextDelta | ToolCallComplete | UsageUpdate | TurnFinished


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """What was sent, minus authorization.

    Auth headers are never journaled; replay re-injects them at the HTTP layer.
    This is what lets the journal double as a replay fixture without carrying a
    bearer token in it.
    """

    model: str
    body: dict
    endpoint: str = ""


@runtime_checkable
class ModelBackend(Protocol):
    name: str

    def capabilities(self) -> Capabilities:
        """Probed once per session and journaled; the runtime degrades along a
        documented path rather than emulating what a backend lacks."""
        ...

    def stream_turn(self, request: TurnRequest) -> Iterator[StreamEvent]:
        """Yield deltas, then exactly one ``TurnFinished`` as the last event."""
        ...


def collect_turn(events: Iterator[StreamEvent]) -> ModelTurn:
    """Drain a stream to its final turn. Used by non-streaming callers."""
    turn: ModelTurn | None = None
    for event in events:
        if isinstance(event, TurnFinished):
            turn = event.turn
    if turn is None:
        from foundry.core.errors import ProtocolError

        raise ProtocolError("backend stream ended without a finished turn")
    return turn
