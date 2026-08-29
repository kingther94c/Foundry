"""Provider-neutral intermediate representation (IR).

Every model backend converts between its wire protocol and these types; nothing
else in the runtime may depend on a provider's own shapes. Frozen dataclasses
throughout: history is append-only and safe to share across the runtime.

Frozen at M0 (design.md section 2). Additive changes only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Literal

IR_VERSION = 1


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# --- content blocks -------------------------------------------------------
# The block union mirrors MCP's shape so a future MCP bridge is mechanical.


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    """A tool call the model asked for.

    ``arguments`` stays a raw JSON string: parsing is the tool layer's job, and
    a parse failure must be reportable as a malformed call rather than crashing
    the adapter.
    """

    call_id: str
    name: str
    arguments: str
    kind: Literal["tool_use"] = "tool_use"

    def parse_arguments(self) -> dict[str, Any]:
        """Raise ``ValueError`` if the model emitted non-object JSON."""
        try:
            parsed = json.loads(self.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"tool arguments are not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"tool arguments must be a JSON object, got {type(parsed).__name__}")
        return parsed


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    call_id: str
    content: str
    is_error: bool = False
    kind: Literal["tool_result"] = "tool_result"


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    blocks: tuple[ContentBlock, ...]

    @staticmethod
    def text(role: Role, text: str) -> Message:
        return Message(role=role, blocks=(TextBlock(text),))

    @property
    def text_content(self) -> str:
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> tuple[ToolUseBlock, ...]:
        return tuple(b for b in self.blocks if isinstance(b, ToolUseBlock))


# --- tool declarations ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """What the model is told about a tool. ``parameters`` is JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]


# --- sampling results -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts as reported by the provider.

    Never estimated: luban measured hand-rolled estimates running ~36% low, and
    budget enforcement on a wrong number is worse than none.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """One sampling result, normalized."""

    text: str
    tool_calls: tuple[ToolUseBlock, ...] = ()
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = StopReason.END_TURN
    model: str = ""

    def as_message(self) -> Message:
        blocks: list[ContentBlock] = []
        if self.text:
            blocks.append(TextBlock(self.text))
        blocks.extend(self.tool_calls)
        return Message(role=Role.ASSISTANT, blocks=tuple(blocks))


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a backend can actually do.

    Probed once per session and journaled. The runtime never silently emulates a
    capability a backend lacks; it degrades along a documented path instead.
    """

    streaming: bool = True
    parallel_tool_calls: bool = False
    prompt_caching: bool = False
    reports_usage: bool = True
    max_context_tokens: int = 128_000
    edit_format: Literal["anchored_patch", "whole_file"] = "anchored_patch"


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """Everything a backend needs to produce one turn. Stateless by design."""

    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    model: str
    max_output_tokens: int | None = None
    temperature: float | None = None

    def with_messages(self, messages: tuple[Message, ...]) -> TurnRequest:
        return replace(self, messages=messages)
