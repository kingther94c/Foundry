"""Anthropic Messages adapter.

Speaks the native Messages API over httpc, not the SDK (foundry.core imports no
third-party package). The contract is docs/research/anthropic-messages.md; the
two rules that shape this file:

* the assistant content the API returned is replayed verbatim, carried in an
  OpaqueBlock(provider="anthropic"): rebuilding blocks field by field silently
  drops whatever the API adds next (caller was the first such field), and a
  thinking block whose prefix changed is dropped or rejected;
* the system prompt, tools and history are built the same way every round, so
  the top-level cache_control lets the next round read the cache.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Iterator

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
    Message,
    ModelTurn,
    OpaqueBlock,
    Role,
    StopReason,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    TurnRequest,
    Usage,
)
from foundry.core.errors import FatalError, ProtocolError, TransientError
from foundry.core.httpc import (HttpClient, NotStreaming, open_retrying_stream,
                                retry_with_backoff)

PROVIDER = "anthropic"
ANTHROPIC_VERSION = "2023-06-01"
BETA_THINKING_BINDING = "thinking-binding-controls-2026-08-01"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS_STREAM = 64000
DEFAULT_MAX_TOKENS_SINGLE = 16000

_STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "pause_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}

_TRANSIENT_ERROR_TYPES = ("overloaded_error", "api_error")


# --- request ----------------------------------------------------------------


def _tool_to_wire(schema: ToolSchema) -> dict[str, Any]:
    return {"name": schema.name, "description": schema.description,
            "input_schema": schema.parameters}


def _assistant_content(message: Message) -> list[dict[str, Any]]:
    for block in message.blocks:
        if isinstance(block, OpaqueBlock) and block.provider == PROVIDER:
            # Verbatim: nothing added, nothing removed.
            content = json.loads(block.data)
            return content if isinstance(content, list) else []

    # A turn some other way produced (another backend, a scripted turn).
    built: list[dict[str, Any]] = []
    text = message.text_content
    if text:
        built.append({"type": "text", "text": text})
    for block in message.blocks:
        if isinstance(block, ToolUseBlock):
            try:
                arguments = json.loads(block.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            built.append({"type": "tool_use", "id": block.call_id,
                          "name": block.name, "input": arguments})
    return built


def _build_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []

    def flush_user() -> None:
        # tool_result blocks must precede any text in a user message.
        content = results + texts
        if content:
            wire.append({"role": "user", "content": content})
        results.clear()
        texts.clear()

    for message in messages:
        if message.role is Role.SYSTEM:
            continue
        if message.role is Role.ASSISTANT:
            content = _assistant_content(message)
            if not content:
                continue
            flush_user()
            wire.append({"role": "assistant", "content": content})
            continue
        # User-side: user text and tool results merge into one user message.
        for block in message.blocks:
            if isinstance(block, ToolResultBlock):
                result: dict[str, Any] = {"type": "tool_result",
                                          "tool_use_id": block.call_id,
                                          "content": block.content}
                if block.is_error:
                    result["is_error"] = True
                results.append(result)
        text = message.text_content
        if text:
            texts.append({"type": "text", "text": text})
    flush_user()
    return wire


def build_body(request: TurnRequest, *, stream: bool, effort: str | None = None,
               fallbacks: str | None = "default") -> dict[str, Any]:
    system = "\n\n".join(m.text_content for m in request.messages
                         if m.role is Role.SYSTEM)
    if request.max_output_tokens is not None:
        max_tokens = request.max_output_tokens
    else:
        max_tokens = DEFAULT_MAX_TOKENS_STREAM if stream else DEFAULT_MAX_TOKENS_SINGLE

    body: dict[str, Any] = {"model": request.model, "max_tokens": max_tokens,
                            "stream": stream}
    if system:
        body["system"] = [{"type": "text", "text": system}]
    if request.tools:
        body["tools"] = [_tool_to_wire(t) for t in request.tools]
    body["messages"] = _build_messages(request.messages)
    body["cache_control"] = {"type": "ephemeral"}
    body["thinking"] = {"type": "adaptive",
                        "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    if effort:
        body["output_config"] = {"effort": effort}
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if fallbacks:
        body["fallbacks"] = fallbacks
    # Never tool_choice: forced choices are a 400 on this model.
    return body


# --- response -----------------------------------------------------------------


def _merge_usage(into: dict[str, Any], payload: dict[str, Any] | None) -> None:
    """Later usage objects override earlier ones field by field."""
    for key, value in (payload or {}).items():
        if value is not None:
            into[key] = value


def _usage_from(fields: dict[str, Any]) -> Usage:
    def number(key: str) -> int:
        value = fields.get(key)
        return value if isinstance(value, int) else 0

    cache_read = number("cache_read_input_tokens")
    return Usage(
        input_tokens=number("input_tokens") + number("cache_creation_input_tokens") + cache_read,
        output_tokens=number("output_tokens"),
        cached_input_tokens=cache_read,
    )


# A content block paired with the raw JSON text of its tool input (None for
# blocks that are not tool use).
_Pair = tuple[dict[str, Any], "str | None"]


def _apply_fallback(pairs: list[_Pair], notices: list[str]) -> list[_Pair]:
    last = None
    for i, (block, _raw) in enumerate(pairs):
        if block.get("type") == "fallback":
            last = i
    if last is None:
        return pairs
    fallback = pairs[last][0]
    # Before the last fallback block is a declined attempt: keep its text only.
    declined = [p for p in pairs[:last] if p[0].get("type") == "text"]
    from_model = (fallback.get("from") or {}).get("model") or "unknown"
    to_model = (fallback.get("to") or {}).get("model") or "unknown"
    notices.append(f"{from_model} declined this turn; the API re-ran it on the "
                   f"fallback model {to_model}")
    return declined + pairs[last + 1:]


def _transformation_notices(transformations: Any) -> list[str]:
    notices: list[str] = []
    for entry in transformations or []:
        if isinstance(entry, dict) and entry.get("type") == "thinking_dropped":
            path = entry.get("path")
            if not isinstance(path, str):
                path = json.dumps(path)
            notices.append(f"the API dropped a replayed thinking block at {path}: "
                           f"{entry.get('reason') or 'no reason given'}")
    return notices


def _assemble(pairs: list[_Pair], *, stop_reason: str | None, stop_details: Any,
              usage: Usage, transformations: Any, model: str) -> ModelTurn:
    notices = _transformation_notices(transformations)
    pairs = _apply_fallback(pairs, notices)
    blocks = [block for block, _raw in pairs]
    text = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
    stop = _STOP_REASONS.get(stop_reason or "end_turn", StopReason.END_TURN)

    calls: tuple[ToolUseBlock, ...] = ()
    if stop is StopReason.REFUSAL:
        category = stop_details.get("category") if isinstance(stop_details, dict) else None
        notices.append("the model declined to answer"
                       + (f" (category: {category})" if category else ""))
    else:
        calls = tuple(
            ToolUseBlock(call_id=b.get("id") or f"toolu_{i}", name=b.get("name") or "",
                         arguments=raw or "{}")
            for i, (b, raw) in enumerate(pairs) if b.get("type") == "tool_use"
        )

    opaque = OpaqueBlock(provider=PROVIDER, data=json.dumps(blocks)) if blocks else None
    return ModelTurn(text=text, tool_calls=calls, usage=usage, stop_reason=stop,
                     model=model, opaque=opaque, notices=tuple(notices))


def _turn_from_json(payload: Any, default_model: str) -> ModelTurn:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise ProtocolError("response carried no content array")
    pairs: list[_Pair] = []
    for block in payload["content"]:
        if not isinstance(block, dict):
            continue
        raw = None
        if block.get("type") == "tool_use":
            raw = json.dumps(block.get("input") or {})
        pairs.append((copy.deepcopy(block), raw))
    usage_fields: dict[str, Any] = {}
    _merge_usage(usage_fields, payload.get("usage"))
    return _assemble(pairs, stop_reason=payload.get("stop_reason"),
                     stop_details=payload.get("stop_details"),
                     usage=_usage_from(usage_fields),
                     transformations=payload.get("input_transformations"),
                     model=payload.get("model") or default_model)


def _raise_api_error(event: dict[str, Any]) -> None:
    error = event.get("error") or {}
    kind = error.get("type") or "unknown_error"
    message = error.get("message") or "no message"
    detail = json.dumps(event)[:500]
    if kind in _TRANSIENT_ERROR_TYPES:
        raise TransientError(f"the API reported {kind}: {message}", payload=detail)
    raise FatalError(f"the API reported {kind}: {message}", payload=detail)


def _close_block(blocks: dict[int, dict[str, Any]], raw_json: dict[int, str],
                 index: int) -> None:
    """At content_block_stop a tool-use block's input becomes the parsed JSON."""
    block = blocks.get(index)
    if block is None or block.get("type") != "tool_use" or index not in raw_json:
        return
    raw = raw_json[index]
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # The raw text still reaches the tool layer, which reports a malformed
        # call; the block kept for replay needs an object.
        parsed = {}
    block["input"] = parsed if isinstance(parsed, dict) else {}


# --- the backend -------------------------------------------------------------


@dataclass(slots=True)
class AnthropicBackend:
    base_url: str = "https://api.anthropic.com/v1"
    model: str = ""
    api_key: str = ""
    name: str = "anthropic_messages"
    extra_headers: dict[str, str] = field(default_factory=dict)
    client: HttpClient = field(default_factory=HttpClient)
    caps: Capabilities = field(default_factory=lambda: Capabilities(
        parallel_tool_calls=True, prompt_caching=True, max_context_tokens=200_000))
    stream: bool = True
    recorder: Any = None
    max_retries: int = 4
    effort: str | None = None
    fallbacks: str | None = "default"

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/messages"

    def _betas(self) -> list[str]:
        betas = [BETA_THINKING_BINDING]
        if self.fallbacks:
            betas.append(BETA_FALLBACK)
        return betas

    def _headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        headers["anthropic-version"] = ANTHROPIC_VERSION
        headers["anthropic-beta"] = ",".join(self._betas())
        if self.api_key:
            # The only place a credential is applied. It is never journaled,
            # and never sent as Authorization.
            headers["x-api-key"] = self.api_key
        return headers

    def capabilities(self) -> Capabilities:
        return self.caps

    def _body(self, request: TurnRequest, *, stream: bool) -> dict[str, Any]:
        return build_body(request, stream=stream, effort=self.effort,
                          fallbacks=self.fallbacks)

    def stream_turn(self, request: TurnRequest) -> Iterator[StreamEvent]:
        body = self._body(request, stream=self.stream)
        if self.recorder is not None:
            self.recorder.record(RequestRecord(model=request.model, body=body,
                                               endpoint=self.endpoint))
        if self.stream:
            return self._stream(request, body)
        return iter(self._single(body))

    # -- non-streaming ---------------------------------------------------

    def _single(self, body: dict[str, Any]) -> list[StreamEvent]:
        response = retry_with_backoff(
            lambda: self.client.post_json(self.endpoint, body, self._headers()),
            attempts=self.max_retries,
        )
        return self._events_from_json(response.json(), body["model"])

    @staticmethod
    def _events_from_json(payload: Any, default_model: str) -> list[StreamEvent]:
        if isinstance(payload, dict) and payload.get("type") == "error":
            _raise_api_error(payload)
        turn = _turn_from_json(payload, default_model)
        events: list[StreamEvent] = []
        if turn.text:
            events.append(TextDelta(turn.text))
        events.extend(ToolCallComplete(c) for c in turn.tool_calls)
        events.append(UsageUpdate(turn.usage))
        events.append(TurnFinished(turn))
        return events

    # -- streaming -------------------------------------------------------

    def _stream(self, request: TurnRequest, body: dict[str, Any]) -> Iterator[StreamEvent]:
        try:
            events = open_retrying_stream(
                lambda: self.client.stream_sse(
                    self.endpoint, body, self._headers(),
                    # Messages ends with its own message_stop event, checked
                    # below, not the Chat Completions [DONE] sentinel.
                    expect_done_sentinel=False),
                attempts=self.max_retries,
            )
        except NotStreaming:
            # Degrade for the rest of the session, as the other adapters do,
            # with one repeated request on the first turn only. The body is
            # rebuilt rather than spread so max_tokens takes the non-streaming
            # default: the streaming one is too large for a single response.
            self.stream = False
            yield from self._single(self._body(request, stream=False))
            return

        blocks: dict[int, dict[str, Any]] = {}
        raw_json: dict[int, str] = {}
        usage_fields: dict[str, Any] = {}
        transformations: list[Any] = []
        stop_reason: str | None = None
        stop_details: Any = None
        model = body["model"]
        stopped = False

        for event in events:
            kind = event.get("type", "")

            if kind == "message_start":
                message = event.get("message") or {}
                model = message.get("model") or model
                _merge_usage(usage_fields, message.get("usage"))
                transformations.extend(message.get("input_transformations") or [])

            elif kind == "content_block_start":
                index = event.get("index", len(blocks))
                block = copy.deepcopy(event.get("content_block") or {})
                blocks[index] = block
                if block.get("type") == "tool_use":
                    raw_json[index] = ""

            elif kind == "content_block_delta":
                index = event.get("index", 0)
                block = blocks.get(index)
                delta = event.get("delta") or {}
                dtype = delta.get("type")
                if block is None:
                    continue
                if dtype == "text_delta":
                    piece = delta.get("text") or ""
                    block["text"] = (block.get("text") or "") + piece
                    if piece:
                        yield TextDelta(piece)
                elif dtype == "thinking_delta":
                    block["thinking"] = ((block.get("thinking") or "")
                                         + (delta.get("thinking") or ""))
                elif dtype == "signature_delta":
                    block["signature"] = ((block.get("signature") or "")
                                          + (delta.get("signature") or ""))
                elif dtype == "input_json_delta":
                    raw_json[index] = (raw_json.get(index, "")
                                       + (delta.get("partial_json") or ""))
                # Unknown delta types are ignored.

            elif kind == "content_block_stop":
                _close_block(blocks, raw_json, event.get("index", 0))

            elif kind == "message_delta":
                delta = event.get("delta") or {}
                if delta.get("stop_reason") is not None:
                    stop_reason = delta["stop_reason"]
                if "stop_details" in delta:
                    stop_details = delta["stop_details"]
                _merge_usage(usage_fields, event.get("usage"))
                # After a mid-stream fallback the final message_delta reports
                # input_transformations again, for the fallback attempt.
                transformations.extend(event.get("input_transformations")
                                       or delta.get("input_transformations") or [])

            elif kind == "message_stop":
                stopped = True
                break

            elif kind == "error":
                _raise_api_error(event)

        if not stopped:
            # A connection cut mid-turn and a stream that closed with no
            # content at all are both not the model's complete answer.
            raise TransientError(
                "the response stream ended without message_stop; "
                "the connection was cut mid-turn"
            )

        pairs: list[_Pair] = []
        for index in sorted(blocks):
            if index in raw_json:
                _close_block(blocks, raw_json, index)
            pairs.append((blocks[index], raw_json.get(index)))
        turn = _assemble(pairs, stop_reason=stop_reason, stop_details=stop_details,
                         usage=_usage_from(usage_fields),
                         transformations=transformations, model=model)
        for call in turn.tool_calls:
            yield ToolCallComplete(call)
        yield UsageUpdate(turn.usage)
        yield TurnFinished(turn)
