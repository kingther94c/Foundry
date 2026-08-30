"""Deterministic backends for tests and fixture replay.

This is the reason the whole runtime is testable with no network and no
credentials. Two flavours:

* :class:`ScriptedBackend` -- turns handed to it directly, for unit tests.
* :class:`ReplayBackend` -- turns loaded from a recorded fixture file.

Matching is by ordinal, not by request equality. Byte-exact matching would turn
every prompt tweak into a suite-wide failure; blind replay would verify nothing.
So the fixture also carries a structural signature (tool call names, message
count) which replay asserts, and the full request is written out as a test
artifact for a human to diff when something changes shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from foundry.core.backends.base import (
    RequestRecord,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
    TurnFinished,
    UsageUpdate,
)
from foundry.core.conversation import (
    Capabilities,
    ModelTurn,
    StopReason,
    ToolUseBlock,
    TurnRequest,
    Usage,
)
from foundry.core.errors import ProtocolError

FIXTURE_VERSION = 1


@dataclass(frozen=True, slots=True)
class RequestSignature:
    """The structural shape a replayed request must still have."""

    message_count: int
    tool_names: tuple[str, ...]
    model: str

    @staticmethod
    def of(request: TurnRequest) -> RequestSignature:
        return RequestSignature(
            message_count=len(request.messages),
            tool_names=tuple(t.name for t in request.tools),
            model=request.model,
        )

    def as_json(self) -> dict:
        return {
            "message_count": self.message_count,
            "tool_names": list(self.tool_names),
            "model": self.model,
        }

    @staticmethod
    def from_json(obj: dict) -> RequestSignature:
        return RequestSignature(
            message_count=obj.get("message_count", 0),
            tool_names=tuple(obj.get("tool_names", ())),
            model=obj.get("model", ""),
        )

    def mismatch(self, other: RequestSignature) -> str | None:
        if self.tool_names != other.tool_names:
            return f"tool set changed: expected {self.tool_names}, got {other.tool_names}"
        if self.message_count != other.message_count:
            return (f"message count changed: expected {self.message_count}, "
                    f"got {other.message_count}")
        return None


def _turn_to_json(turn: ModelTurn) -> dict:
    return {
        "text": turn.text,
        "tool_calls": [
            {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
            for c in turn.tool_calls
        ],
        "usage": {
            "input_tokens": turn.usage.input_tokens,
            "output_tokens": turn.usage.output_tokens,
            "cached_input_tokens": turn.usage.cached_input_tokens,
        },
        "stop_reason": turn.stop_reason.value,
        "model": turn.model,
    }


def _turn_from_json(obj: dict) -> ModelTurn:
    usage = obj.get("usage", {})
    return ModelTurn(
        text=obj.get("text", ""),
        tool_calls=tuple(
            ToolUseBlock(call_id=c["call_id"], name=c["name"], arguments=c["arguments"])
            for c in obj.get("tool_calls", ())
        ),
        usage=Usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached_input_tokens=usage.get("cached_input_tokens", 0),
        ),
        stop_reason=StopReason(obj.get("stop_reason", StopReason.END_TURN.value)),
        model=obj.get("model", ""),
    )


def _emit(turn: ModelTurn) -> Iterator[StreamEvent]:
    if turn.text:
        # Chunked so streaming consumers are exercised, not just the final text.
        for i in range(0, len(turn.text), 24):
            yield TextDelta(turn.text[i:i + 24])
    for call in turn.tool_calls:
        yield ToolCallComplete(call)
    yield UsageUpdate(turn.usage)
    yield TurnFinished(turn)


@dataclass(slots=True)
class ScriptedBackend:
    """Returns pre-built turns in order. The workhorse of the unit suite."""

    turns: list[ModelTurn]
    name: str = "scripted"
    caps: Capabilities = field(default_factory=Capabilities)
    seen: list[TurnRequest] = field(default_factory=list)

    def capabilities(self) -> Capabilities:
        return self.caps

    def stream_turn(self, request: TurnRequest) -> Iterator[StreamEvent]:
        index = len(self.seen)
        self.seen.append(request)
        if index >= len(self.turns):
            raise ProtocolError(
                f"scripted backend exhausted: no turn {index} (have {len(self.turns)})"
            )
        return _emit(self.turns[index])


@dataclass(slots=True)
class ReplayBackend:
    """Replays a recorded fixture, asserting the request still has the same shape."""

    fixture: Path
    name: str = "replay"
    strict: bool = True
    _entries: list[dict] = field(default_factory=list, init=False)
    _caps: Capabilities = field(default_factory=Capabilities, init=False)
    _index: int = field(default=0, init=False)
    drift: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        data = json.loads(self.fixture.read_text(encoding="utf-8"))
        if data.get("fixture_version") != FIXTURE_VERSION:
            raise ProtocolError(
                f"unsupported fixture version: {data.get('fixture_version')!r}"
            )
        self._entries = data.get("entries", [])
        caps = data.get("capabilities", {})
        self._caps = Capabilities(**caps) if caps else Capabilities()

    def capabilities(self) -> Capabilities:
        return self._caps

    def stream_turn(self, request: TurnRequest) -> Iterator[StreamEvent]:
        if self._index >= len(self._entries):
            raise ProtocolError(
                f"replay fixture exhausted after {len(self._entries)} turns: {self.fixture.name}"
            )
        entry = self._entries[self._index]
        self._index += 1

        expected = RequestSignature.from_json(entry.get("signature", {}))
        actual = RequestSignature.of(request)
        problem = expected.mismatch(actual)
        if problem:
            message = f"{self.fixture.name} turn {self._index - 1}: {problem}"
            if self.strict:
                raise ProtocolError(message)
            self.drift.append(message)

        return _emit(_turn_from_json(entry["turn"]))


def write_fixture(path: Path, entries: list[tuple[TurnRequest, ModelTurn]],
                  capabilities: Capabilities | None = None) -> None:
    """Write a replay fixture. Used by ``foundry record``."""
    caps = capabilities or Capabilities()
    payload = {
        "fixture_version": FIXTURE_VERSION,
        "capabilities": {
            "streaming": caps.streaming,
            "parallel_tool_calls": caps.parallel_tool_calls,
            "prompt_caching": caps.prompt_caching,
            "reports_usage": caps.reports_usage,
            "max_context_tokens": caps.max_context_tokens,
            "edit_format": caps.edit_format,
        },
        "entries": [
            {
                "signature": RequestSignature.of(request).as_json(),
                "turn": _turn_to_json(turn),
            }
            for request, turn in entries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
