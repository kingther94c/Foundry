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
    with_query,
    MODELS_MAX_BYTES,
    MODELS_TIMEOUT_S,
    model_ids,
    raise_reported_error,
    ReasoningComplete,
    RequestRecord,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
    TurnFinished,
    UsageUpdate,
)
from foundry.core.conversation import (
    Capabilities,
    ImageBlock,
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
from foundry.core.errors import ProtocolError, StreamDropped, TransientError
from foundry.core.images import data_url
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
    images = [b for b in message.blocks if isinstance(b, ImageBlock)]
    if images:
        # Content parts only when there is an image: a plain string stays the
        # shape every server takes (D-045).
        wire["content"] = [_image_part(i) for i in images] + (
            [{"type": "text", "text": text}] if text else [])
    elif text or not tool_calls:
        wire["content"] = text
    if tool_calls:
        wire["tool_calls"] = tool_calls
    return [wire]


def _image_part(block: ImageBlock) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": data_url(block)}}


def _tool_images_message(results: list[ToolResultBlock]) -> dict[str, Any] | None:
    """The images a run of tool results carried, as the user message after
    them: a tool message takes text only, so this is where the image goes --
    what codex does too (D-045)."""
    parts: list[dict[str, Any]] = []
    for result in results:
        if result.images:
            names = ", ".join(i.name or "an image" for i in result.images)
            parts.append({"type": "text",
                          "text": f"The image(s) from tool call {result.call_id}: {names}"})
            parts.extend(_image_part(i) for i in result.images)
    return {"role": "user", "content": parts} if parts else None


def _tool_to_wire(schema: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": schema.parameters,
        },
    }


def build_body(request: TurnRequest, *, stream: bool,
               effort: str | None = None,
               verbosity: str | None = None) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    results: list[ToolResultBlock] = []
    for message in request.messages:
        if message.role is Role.TOOL:
            results.extend(b for b in message.blocks if isinstance(b, ToolResultBlock))
        elif results:
            pending = _tool_images_message(results)
            messages.extend([pending] if pending else [])
            results.clear()
        messages.extend(_message_to_wire(message))
    pending = _tool_images_message(results)
    messages.extend([pending] if pending else [])
    body: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "stream": stream,
    }
    if request.tools:
        body["tools"] = [_tool_to_wire(t) for t in request.tools]
    if request.max_output_tokens is not None:
        # OpenAI's current name (D-096): its reasoning models refuse
        # max_tokens, and servers that know only that one ignore this.
        body["max_completion_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if stream:
        body["stream_options"] = {"include_usage": True}
    # Opt-in (D-061): a server that does not know the field may reject it.
    if effort:
        body["reasoning_effort"] = effort
    # Opt-in too (D-090): GPT-5's answer length.
    if verbosity:
        body["verbosity"] = verbosity
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


def _reasoning_text(part: Any) -> str:
    """Reasoning some Chat Completions servers add beside the answer (D-044):
    `reasoning_content` (DeepSeek, vLLM) or `reasoning` (OpenRouter, newer
    vLLM). Never sent back -- DeepSeek refuses a request that replays it."""
    if not isinstance(part, dict):
        return ""
    for key in ("reasoning_content", "reasoning"):
        value = part.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


@dataclass(slots=True)
class OpenAICompatBackend:
    base_url: str
    model: str
    api_key: str = ""
    name: str = "openai_compat"
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Added to every request's URL (D-084).
    query_params: dict[str, str] = field(default_factory=dict)
    client: HttpClient = field(default_factory=HttpClient)
    caps: Capabilities = field(default_factory=Capabilities)
    stream: bool = True
    recorder: Any = None
    max_retries: int = 4
    # Told of each retry before its wait (D-085); the runtime sets it.
    on_retry: Any = None
    # reasoning_effort (D-061); None sends nothing.
    effort: str | None = None
    # verbosity (D-090); None sends nothing.
    verbosity: str | None = None

    @property
    def endpoint(self) -> str:
        return with_query(f"{self.base_url.rstrip('/')}/chat/completions", self.query_params)

    def _headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        if self.api_key:
            # The only place a credential is applied. It is never journaled.
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def capabilities(self) -> Capabilities:
        return self.caps

    def list_models(self) -> list[str]:
        """The models the endpoint lists (``GET /models``), for /model (D-067)."""
        response = self.client.get_json(with_query(f"{self.base_url.rstrip('/')}/models",
                                                   self.query_params), self._headers(),
                                        timeout=MODELS_TIMEOUT_S, max_bytes=MODELS_MAX_BYTES)
        return model_ids(response.json())

    def stream_turn(self, request: TurnRequest) -> Iterator[StreamEvent]:
        body = build_body(request, stream=self.stream, effort=self.effort,
                          verbosity=self.verbosity)
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
            attempts=self.max_retries, on_retry=self.on_retry,
        )
        payload = response.json()
        choices = payload.get("choices") or []
        if isinstance(payload.get("error"), dict):
            raise_reported_error(payload["error"], where="in its answer")
        if not choices:
            raise ProtocolError("response contained no choices")
        if choices[0].get("finish_reason") == "error":
            raise StreamDropped("the provider ended its answer with an error and said no more")
        message = choices[0].get("message", {})

        text = message.get("content") or ""
        reasoning = _reasoning_text(message).strip()
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
        if reasoning:
            events.append(ReasoningComplete(reasoning))
        if text:
            events.append(TextDelta(text))
        events.extend(ToolCallComplete(c) for c in calls)
        events.append(UsageUpdate(turn.usage))
        events.append(TurnFinished(turn))
        return events

    # -- streaming --------------------------------------------------------

    def _stream(self, body: dict[str, Any]) -> Iterator[StreamEvent]:
        text_parts: list[str] = []
        # Reasoning streams first and is shown whole once the answer starts.
        thinking: list[str] = []
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
                attempts=self.max_retries, on_retry=self.on_retry,
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
            # An error reported in the stream (D-147): OpenRouter's beside a
            # finish_reason of "error", vLLM's as the chunk itself. The stream
            # may still close with [DONE], and what came before it is not the
            # model's whole answer.
            if isinstance(chunk.get("error"), dict):
                raise_reported_error(chunk["error"], where="mid-turn")
            if chunk.get("object") == "error":
                raise_reported_error(chunk, where="mid-turn")
            if chunk.get("usage"):
                usage = _usage_from(chunk["usage"])
            model = chunk.get("model", model)
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                if finish_reason == "error":
                    raise StreamDropped("the provider ended the turn with an error part-way "
                                        "and said no more")

                piece = _reasoning_text(delta)
                if piece:
                    thinking.append(piece)
                content = delta.get("content")
                # Tool calls are yielded at the end, after the flush below.
                if thinking and content:
                    reasoning = "".join(thinking).strip()
                    thinking.clear()
                    if reasoning:
                        yield ReasoningComplete(reasoning)
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
            raise StreamDropped(
                "the response stream carried no content before its terminator; "
                "the provider closed the turn without answering"
            )
        reasoning = "".join(thinking).strip()
        if reasoning:
            yield ReasoningComplete(reasoning)
        for c in calls:
            yield ToolCallComplete(c)

        yield UsageUpdate(usage)
        yield TurnFinished(ModelTurn(
            text="".join(text_parts), tool_calls=calls, usage=usage,
            stop_reason=_STOP_REASONS.get(finish_reason, StopReason.END_TURN),
            model=model,
        ))
