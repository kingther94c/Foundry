"""Chat Completions adapter.

Covers the personal path (api.openai.com), local OpenAI-compatible endpoints
used for free smoke tests, and most enterprise gateways. It translates the IR to
the wire format and back -- nothing else. It holds no reference to the policy
engine or the tool executor, so it cannot start a second loop.
"""

from __future__ import annotations

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
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    TurnRequest,
    Usage,
)
from foundry.core.errors import ProtocolError, TransientError
from foundry.core.httpc import (HttpClient, NotStreaming, open_retrying_stream,
                                retry_with_backoff)

_STOP_REASONS = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


def _message_to_wire(message: Message) -> list[dict[str, Any]]:
    if message.role is Role.TOOL:
        return [
            {"role": "tool", "tool_call_id": block.call_id, "content": block.content}
            for block in message.blocks if isinstance(block, ToolResultBlock)
        ]

    text = "".join(b.text for b in message.blocks if isinstance(b, TextBlock))
    tool_calls = [
        {"id": b.call_id, "type": "function",
         "function": {"name": b.name, "arguments": b.arguments}}
        for b in message.blocks if isinstance(b, ToolUseBlock)
    ]
    wire: dict[str, Any] = {"role": message.role.value}
    if text or not tool_calls:
        wire["content"] = text
    if tool_calls:
        wire["tool_calls"] = tool_calls
    return [wire]


def _tool_to_wire(schema: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": schema.parameters,
        },
    }


def build_body(request: TurnRequest, *, stream: bool) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        messages.extend(_message_to_wire(message))
    body: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "stream": stream,
    }
    if request.tools:
        body["tools"] = [_tool_to_wire(t) for t in request.tools]
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def _usage_from(payload: dict[str, Any] | None) -> Usage:
    if not payload:
        return Usage()
    details = payload.get("prompt_tokens_details") or {}
    return Usage(
        input_tokens=payload.get("prompt_tokens", 0),
        output_tokens=payload.get("completion_tokens", 0),
        cached_input_tokens=details.get("cached_tokens", 0),
    )


@dataclass(slots=True)
class OpenAICompatBackend:
    base_url: str
    model: str
    api_key: str = ""
    name: str = "openai_compat"
    extra_headers: dict[str, str] = field(default_factory=dict)
    client: HttpClient = field(default_factory=HttpClient)
    caps: Capabilities = field(default_factory=Capabilities)
    stream: bool = True
    recorder: Any = None
    max_retries: int = 4

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        if self.api_key:
            # The only place a credential is applied. It is never journaled.
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def capabilities(self) -> Capabilities:
        return self.caps

    def stream_turn(self, request: TurnRequest) -> Iterator[StreamEvent]:
        body = build_body(request, stream=self.stream)
        if self.recorder is not None:
            self.recorder.record(RequestRecord(model=request.model, body=body,
                                               endpoint=self.endpoint))
        if self.stream:
            return self._stream(body)
        return iter(self._single(body))

    # -- non-streaming ----------------------------------------------------

    def _single(self, body: dict[str, Any]) -> list[StreamEvent]:
        response = retry_with_backoff(
            lambda: self.client.post_json(self.endpoint, body, self._headers()),
            attempts=self.max_retries,
        )
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise ProtocolError("response contained no choices")
        message = choices[0].get("message", {})

        text = message.get("content") or ""
        # `or "{}"`, not a dict default: a present-but-null `arguments` -- the
        # shape a gateway produces for a no-argument call when an upstream
        # proto3 field is unset -- put None into ToolUseBlock.arguments, and
        # json.loads(None) raises TypeError, which parse_arguments does not
        # catch. The streaming path already normalized it; this one crashed.
        calls = tuple(
            ToolUseBlock(call_id=c.get("id") or f"call_{i}",
                         name=(c.get("function") or {}).get("name") or "",
                         arguments=(c.get("function") or {}).get("arguments") or "{}")
            for i, c in enumerate(message.get("tool_calls") or [])
        )
        turn = ModelTurn(
            text=text, tool_calls=calls, usage=_usage_from(payload.get("usage")),
            stop_reason=_STOP_REASONS.get(choices[0].get("finish_reason", "stop"),
                                          StopReason.END_TURN),
            model=payload.get("model", body["model"]),
        )
        events: list[StreamEvent] = []
        if text:
            events.append(TextDelta(text))
        events.extend(ToolCallComplete(c) for c in calls)
        events.append(UsageUpdate(turn.usage))
        events.append(TurnFinished(turn))
        return events

    # -- streaming --------------------------------------------------------

    def _stream(self, body: dict[str, Any]) -> Iterator[StreamEvent]:
        text_parts: list[str] = []
        # Tool call fragments arrive interleaved and keyed by index, not id.
        partial: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish_reason = "stop"
        model = body["model"]
        saw_chunk = False

        try:
            # Retried like any other request. Applying retry_with_backoff only
            # to the non-streaming path left request_max_retries and the
            # Retry-After handling dead on the default path, so a single 429
            # from a real gateway killed an in-progress task.
            chunks = open_retrying_stream(
                lambda: self.client.stream_sse(self.endpoint, body, self._headers()),
                attempts=self.max_retries,
            )
        except NotStreaming:
            # The endpoint does not stream. Degrade for the rest of the session
            # rather than returning an empty turn, which would look like the
            # model had nothing to say. This costs one repeated request, on the
            # first turn only, which is the price of discovering the capability
            # from behaviour instead of from a separate probe call.
            self.stream = False
            # stream_options must go with it. Spreading only `stream` left
            # `stream_options: {include_usage: true}` in a non-streaming body,
            # which OpenAI and strict gateways reject with 400 -- so the degrade
            # path built to rescue the turn failed it instead.
            degraded = {k: v for k, v in body.items() if k != "stream_options"}
            yield from self._single({**degraded, "stream": False})
            return

        for chunk in chunks:
            saw_chunk = True
            if chunk.get("usage"):
                usage = _usage_from(chunk["usage"])
            model = chunk.get("model", model)
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield TextDelta(content)

                for fragment in delta.get("tool_calls") or []:
                    index = fragment.get("index", 0)
                    slot = partial.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if fragment.get("id"):
                        slot["id"] = fragment["id"]
                    function = fragment.get("function") or {}
                    if function.get("name"):
                        slot["name"] = function["name"]
                    if function.get("arguments"):
                        slot["arguments"] += function["arguments"]

        calls = tuple(
            ToolUseBlock(call_id=slot["id"] or f"call_{index}",
                         name=slot["name"],
                         arguments=slot["arguments"] or "{}")
            for index, slot in sorted(partial.items())
        )

        # The truncation guard catches "ended with no terminator" but not
        # "terminator arrived with no content". A gateway that opens the stream,
        # faults internally and closes with just `data: [DONE]` produced a
        # successful, empty turn: the run reported partial with an empty answer
        # and zero tokens, and nothing said the provider had failed.
        if not saw_chunk:
            raise TransientError(
                "the response stream carried no content before its terminator; "
                "the provider closed the turn without answering"
            )
        for c in calls:
            yield ToolCallComplete(c)

        yield UsageUpdate(usage)
        yield TurnFinished(ModelTurn(
            text="".join(text_parts), tool_calls=calls, usage=usage,
            stop_reason=_STOP_REASONS.get(finish_reason, StopReason.END_TURN),
            model=model,
        ))
