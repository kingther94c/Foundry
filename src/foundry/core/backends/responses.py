"""OpenAI Responses API adapter.

Required for the corporate gateway, where the OpenAI-family models speak
Responses (D-022). Two differences from Chat Completions shape this file:

* the conversation is a flat list of typed *items*, not messages with embedded
  tool calls -- a function call and its output are siblings, both referencing
  the same ``call_id``;
* streaming is a typed event protocol (``response.output_text.delta`` and
  friends) rather than choice deltas.

The gateway's exact behaviour is unverified until fixtures are captured from it,
so this adapter reads the shapes defensively and reports anything it cannot
parse as a protocol error rather than guessing.
"""

from __future__ import annotations

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


def _message_to_items(message: Message) -> list[dict[str, Any]]:
    if message.role is Role.TOOL:
        return [
            {"type": "function_call_output", "call_id": block.call_id,
             "output": block.content}
            for block in message.blocks if isinstance(block, ToolResultBlock)
        ]

    items: list[dict[str, Any]] = []
    text = "".join(b.text for b in message.blocks if isinstance(b, TextBlock))
    if text:
        content_type = "output_text" if message.role is Role.ASSISTANT else "input_text"
        items.append({
            "type": "message", "role": message.role.value,
            "content": [{"type": content_type, "text": text}],
        })
    for block in message.blocks:
        if isinstance(block, ToolUseBlock):
            items.append({
                "type": "function_call", "call_id": block.call_id,
                "name": block.name, "arguments": block.arguments,
            })
    return items


def _tool_to_wire(schema: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "name": schema.name,
        "description": schema.description,
        "parameters": schema.parameters,
    }


def build_body(request: TurnRequest, *, stream: bool) -> dict[str, Any]:
    instructions = ""
    items: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role is Role.SYSTEM:
            # Responses carries the system prompt out of band.
            instructions = message.text_content
            continue
        items.extend(_message_to_items(message))

    body: dict[str, Any] = {
        "model": request.model,
        "input": items,
        "stream": stream,
        # Stateless: the full history is sent every turn, so a gateway that does
        # not persist state behaves the same as one that does.
        "store": False,
    }
    if instructions:
        body["instructions"] = instructions
    if request.tools:
        body["tools"] = [_tool_to_wire(t) for t in request.tools]
    if request.max_output_tokens is not None:
        body["max_output_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        body["temperature"] = request.temperature
    return body


def _usage_from(payload: dict[str, Any] | None) -> Usage:
    if not payload:
        return Usage()
    details = payload.get("input_tokens_details") or {}
    return Usage(
        input_tokens=payload.get("input_tokens", 0),
        output_tokens=payload.get("output_tokens", 0),
        cached_input_tokens=details.get("cached_tokens", 0),
    )


def _parse_output(output: list[dict[str, Any]]) -> tuple[str, tuple[ToolUseBlock, ...]]:
    text_parts: list[str] = []
    calls: list[ToolUseBlock] = []
    for item in output or []:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") in ("output_text", "text"):
                    text_parts.append(part.get("text", ""))
        elif kind == "function_call":
            calls.append(ToolUseBlock(
                call_id=item.get("call_id") or item.get("id", ""),
                name=item.get("name", ""),
                arguments=item.get("arguments", "{}"),
            ))
        # reasoning items are opaque and deliberately not interpreted
    return "".join(text_parts), tuple(calls)


@dataclass(slots=True)
class ResponsesBackend:
    base_url: str
    model: str
    api_key: str = ""
    name: str = "responses"
    extra_headers: dict[str, str] = field(default_factory=dict)
    client: HttpClient = field(default_factory=HttpClient)
    caps: Capabilities = field(default_factory=lambda: Capabilities(parallel_tool_calls=True))
    stream: bool = True
    recorder: Any = None
    max_retries: int = 4

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/responses"

    def _headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        if self.api_key:
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

    def _single(self, body: dict[str, Any]) -> list[StreamEvent]:
        response = retry_with_backoff(
            lambda: self.client.post_json(self.endpoint, body, self._headers()),
            attempts=self.max_retries,
        )
        payload = response.json()
        text, calls = _parse_output(payload.get("output", []))
        usage = _usage_from(payload.get("usage"))
        turn = ModelTurn(
            text=text, tool_calls=calls, usage=usage,
            stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
            model=payload.get("model", body["model"]),
        )
        events: list[StreamEvent] = []
        if text:
            events.append(TextDelta(text))
        events.extend(ToolCallComplete(c) for c in calls)
        events.append(UsageUpdate(usage))
        events.append(TurnFinished(turn))
        return events

    def _stream(self, body: dict[str, Any]) -> Iterator[StreamEvent]:
        text_parts: list[str] = []
        calls: list[ToolUseBlock] = []
        partial: dict[str, dict[str, str]] = {}
        usage = Usage()
        model = body["model"]
        completed = False

        try:
            stream = open_retrying_stream(
                lambda: self.client.stream_sse(
                    self.endpoint, body, self._headers(),
                    # Responses signals completion with its own event, checked
                    # below, rather than the Chat Completions [DONE] sentinel.
                    expect_done_sentinel=False),
                attempts=self.max_retries,
            )
        except NotStreaming:
            self.stream = False
            yield from self._single({**body, "stream": False})
            return

        for event in stream:
            kind = event.get("type", "")

            if kind == "response.output_text.delta":
                delta = event.get("delta", "")
                if delta:
                    text_parts.append(delta)
                    yield TextDelta(delta)

            elif kind == "response.output_item.added":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    partial[str(event.get("output_index", len(partial)))] = {
                        "call_id": item.get("call_id") or item.get("id", ""),
                        "name": item.get("name", ""),
                        "arguments": "",
                    }

            elif kind == "response.function_call_arguments.delta":
                slot = partial.get(str(event.get("output_index", 0)))
                if slot is not None:
                    slot["arguments"] += event.get("delta", "")

            elif kind == "response.output_item.done":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    calls.append(ToolUseBlock(
                        call_id=item.get("call_id") or item.get("id", ""),
                        name=item.get("name", ""),
                        arguments=item.get("arguments", "{}"),
                    ))
                    partial.pop(str(event.get("output_index", 0)), None)

            elif kind in ("response.completed", "response.incomplete"):
                response = event.get("response") or {}
                usage = _usage_from(response.get("usage"))
                model = response.get("model", model)
                completed = True
                if not calls and not text_parts:
                    # Some gateways only send the terminal event.
                    text, parsed = _parse_output(response.get("output", []))
                    if text:
                        text_parts.append(text)
                    calls.extend(parsed)

            elif kind == "response.failed" or kind == "error":
                detail = (event.get("response") or event).get("error") or {}
                raise ProtocolError(f"provider reported a failure: {detail}")

        # Any call whose 'done' event never arrived still has its fragments.
        for slot in partial.values():
            calls.append(ToolUseBlock(call_id=slot["call_id"], name=slot["name"],
                                      arguments=slot["arguments"] or "{}"))

        if not completed:
            # Partial content without a completion event means the connection
            # was cut mid-turn; accepting it presented a truncated answer as the
            # model's complete one. Transient, but not retryable once deltas
            # have been shown -- see the note in httpc.stream_sse. It buys an
            # honest error, and the CLI keeps the session open.
            raise TransientError(
                "the response stream ended without a completion event; "
                "the connection was cut mid-turn"
            )

        for call in calls:
            yield ToolCallComplete(call)
        yield UsageUpdate(usage)
        yield TurnFinished(ModelTurn(
            text="".join(text_parts), tool_calls=tuple(calls), usage=usage,
            stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
            model=model,
        ))
