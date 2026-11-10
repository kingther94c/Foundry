"""The ModelBackend contract.

An adapter translates between the IR and one wire protocol. It never calls a
tool, never consults policy, and never runs a loop of its own -- those belong to
AgentRuntime, and a provider that owned them would fork the runtime in practice.

Frozen at M0 (design.md section 8).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterator, Literal, Mapping, NoReturn, Protocol, runtime_checkable

from foundry.core.conversation import Capabilities, ModelTurn, ToolUseBlock, TurnRequest, Usage
from foundry.core.errors import FatalError, ProtocolError, QuotaExceeded, StreamDropped

# Codes an OpenAI-family provider reports inside an answer that asking again
# cannot fix (D-147). Every other code -- server_error, rate_limit_exceeded,
# an overload, one Foundry has never seen -- has the turn asked again, as
# codex does.
PERMANENT_ERROR_CODES = frozenset({"context_length_exceeded", "invalid_prompt",
                                   "invalid_request_error", "usage_not_included"})
_TRY_AGAIN = re.compile(r"try again in (\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE)


def raise_reported_error(error: Any, *, where: str) -> NoReturn:
    """A failure the provider reported inside a successful HTTP answer: an
    error event or chunk part-way through a stream, or a response whose
    status is failed (D-147).

    Taken for the end of the turn, a cut answer was kept as the model's whole
    one; taken for a protocol fault, a passing server error ended the run.
    It is asked again, as a cut stream is (D-099), unless its code says
    waiting cannot help.
    """
    if not isinstance(error, dict):
        error = {"message": str(error)} if error else {}
    code = error.get("code") or error.get("type") or "an unnamed error"
    message = str(error.get("message") or "no message")
    detail = json.dumps(error, default=str)[:500]
    text = f"the provider reported {code} {where}: {message}"
    if "insufficient_quota" in (code, error.get("type")):
        raise QuotaExceeded(f"the account's quota is used up ({text}); waiting will "
                            "not help, so check its plan and billing", payload=detail)
    if isinstance(code, int):
        # vLLM puts the HTTP status there.
        permanent = code < 500 and code != 429
    else:
        permanent = bool({code, error.get("type")} & PERMANENT_ERROR_CODES)
    if permanent:
        raise FatalError(text, payload=detail)
    hint = _TRY_AGAIN.search(message)
    wait = None
    if hint:
        wait = float(hint.group(1)) / (1000.0 if hint.group(2).lower() == "ms" else 1.0)
    raise StreamDropped(text, payload=detail, retry_after=wait)

# The model list (D-067): asked for only when the user types /model, so it
# waits less than a turn would, and is never retried.
MODELS_TIMEOUT_S = 15.0
MODELS_MAX_BYTES = 1 << 20


def with_query(url: str, query: Mapping[str, str]) -> str:
    """``url`` with the backend's ``query_params`` (D-084) -- Azure's
    ``api-version``, say -- after whatever query it already has."""
    if not query:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(dict(query))


def model_ids(payload: object) -> list[str]:
    """The ids in a model list, as OpenAI's and Anthropic's ``/models`` both
    shape it (``{"data": [{"id": ...}, ...]}``): sorted, each once. An entry
    without a string id is skipped; a payload with no list is a ProtocolError."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ProtocolError("the endpoint's answer holds no list of models")
    return sorted({entry["id"] for entry in data
                   if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                   and entry["id"]})


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
class ReasoningComplete:
    """A finished stretch of the model's reasoning, as the provider shows it --
    a summary, or the raw text some Chat Completions servers stream (D-044).

    Whole, never in pieces, so the redactor sees it as one string; and it is
    never part of the conversation the runtime keeps. Adding a stream event is
    additive: a consumer that does not know it skips it.
    """

    text: str
    kind: Literal["reasoning"] = "reasoning"


@dataclass(frozen=True, slots=True)
class TurnFinished:
    turn: ModelTurn
    kind: Literal["turn_finished"] = "turn_finished"


StreamEvent = TextDelta | ToolCallComplete | UsageUpdate | ReasoningComplete | TurnFinished


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
