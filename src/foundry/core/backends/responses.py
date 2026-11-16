"""OpenAI Responses API adapter.

Required for the corporate gateway, where the OpenAI-family models speak
Responses (D-022). Two differences from Chat Completions shape this file:

* the conversation is a flat list of typed *items*, not messages with embedded
  tool calls -- a function call and its output are siblings, both referencing
  the same ``call_id``;
* streaming is a typed event protocol (``response.output_text.delta`` and
  friends) rather than choice deltas.

Nothing is stored (``store: false``), so a reasoning model's reasoning is asked
for encrypted and sent back with the turn it belongs to, as codex does (D-089).

The gateway's exact behaviour is unverified until fixtures are captured from it,
so this adapter reads the shapes defensively and reports anything it cannot
parse as a protocol error rather than guessing.
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
    wire_arguments,
)
from foundry.core.conversation import (
    Capabilities,
    ImageBlock,
    Message,
    ModelTurn,
    OpaqueBlock,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    TurnRequest,
    Usage,
)
from foundry.core.errors import StreamDropped, TransientError
from foundry.core.images import data_url
from foundry.core.httpc import (HttpClient, NotStreaming, open_retrying_stream,
                                retry_with_backoff)

# The opaque block a turn's encrypted reasoning rides in (D-089).
PROVIDER = "openai_responses"
INCLUDE_REASONING = "reasoning.encrypted_content"


def _replayed(message: Message, model: str) -> list[dict[str, Any]]:
    """The reasoning items a turn's opaque block kept, to send back before
    its message and calls -- only to the model that produced them: another
    cannot read them, and may refuse the request."""
    for block in message.blocks:
        if isinstance(block, OpaqueBlock) and block.provider == PROVIDER:
            try:
                data = json.loads(block.data)
            except ValueError:
                return []
            if not isinstance(data, dict) or data.get("model") != model:
                return []
            items = data.get("items")
            return [i for i in items if isinstance(i, dict) and i.get("type") == "reasoning"
                    ] if isinstance(items, list) else []
    return []


def _kept_reasoning(output: Any) -> list[dict[str, Any]]:
    """What can be sent back of a response's reasoning: each item's
    encrypted content and summary. Not its id: nothing is stored
    (``store: false``), so an id would name an item the server does not have."""
    kept = []
    for item in output or []:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        encrypted = item.get("encrypted_content")
        if isinstance(encrypted, str) and encrypted:
            summary = item.get("summary")
            kept.append({"type": "reasoning",
                         "summary": summary if isinstance(summary, list) else [],
                         "encrypted_content": encrypted})
    return kept


def _has_reasoning(output: Any) -> bool:
    return any(isinstance(i, dict) and i.get("type") == "reasoning" for i in output or [])


def _opaque(kept: list[dict[str, Any]], model: str) -> OpaqueBlock | None:
    return OpaqueBlock(provider=PROVIDER, data=json.dumps({"model": model, "items": kept})
                       ) if kept else None


def _message_to_items(message: Message, model: str = "") -> list[dict[str, Any]]:
    if message.role is Role.TOOL:
        return [
            {"type": "function_call_output", "call_id": block.call_id,
             "output": block.content}
            for block in message.blocks if isinstance(block, ToolResultBlock)
        ]

    # The turn's own reasoning first, as the model produced it (D-089).
    items: list[dict[str, Any]] = (_replayed(message, model)
                                   if message.role is Role.ASSISTANT else [])
    text = "".join(b.text for b in message.blocks if isinstance(b, TextBlock))
    images = [b for b in message.blocks if isinstance(b, ImageBlock)]
    if text or images:
        content_type = "output_text" if message.role is Role.ASSISTANT else "input_text"
        items.append({
            "type": "message", "role": message.role.value,
            "content": [_image_part(i) for i in images]
            + ([{"type": content_type, "text": text}] if text else []),
        })
    for block in message.blocks:
        if isinstance(block, ToolUseBlock):
            items.append({
                "type": "function_call", "call_id": block.call_id,
                "name": block.name, "arguments": block.arguments,
            })
    return items


def _image_part(block: ImageBlock) -> dict[str, Any]:
    return {"type": "input_image", "image_url": data_url(block)}


def _tool_images_item(results: list[ToolResultBlock]) -> list[dict[str, Any]]:
    """The images a run of tool results carried, as the user message after
    them: the portable place for them, and codex's (D-045)."""
    content: list[dict[str, Any]] = []
    for result in results:
        if result.images:
            names = ", ".join(i.name or "an image" for i in result.images)
            content.append({"type": "input_text",
                            "text": f"The image(s) from tool call {result.call_id}: {names}"})
            content.extend(_image_part(i) for i in result.images)
    return [{"type": "message", "role": "user", "content": content}] if content else []


def _tool_to_wire(schema: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "name": schema.name,
        "description": schema.description,
        "parameters": schema.parameters,
    }


def build_body(request: TurnRequest, *, stream: bool,
               reasoning_summary: str | None = None,
               effort: str | None = None,
               include_reasoning: bool = False,
               verbosity: str | None = None) -> dict[str, Any]:
    instructions = ""
    items: list[dict[str, Any]] = []
    results: list[ToolResultBlock] = []
    for message in request.messages:
        if message.role is Role.SYSTEM:
            # Responses carries the system prompt out of band.
            instructions = message.text_content
            continue
        if message.role is Role.TOOL:
            results.extend(b for b in message.blocks if isinstance(b, ToolResultBlock))
        elif results:
            items.extend(_tool_images_item(results))
            results.clear()
        items.extend(_message_to_items(message, request.model))
    items.extend(_tool_images_item(results))

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
    # Opt-in (D-044, D-061): a model that does not reason rejects the field.
    reasoning = {key: value for key, value in (("effort", effort),
                                                ("summary", reasoning_summary)) if value}
    if reasoning:
        body["reasoning"] = reasoning
    if include_reasoning:
        # Stateless, the reasoning comes back encrypted to be sent again, as
        # codex asks for it (D-089): dropped, a reasoning model started each
        # round of a tool loop from nothing.
        body["include"] = [INCLUDE_REASONING]
    # Opt-in (D-090): GPT-5's answer length, as codex sends model_verbosity.
    if verbosity:
        body["text"] = {"verbosity": verbosity}
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


def _reasoning_of(item: Any) -> str:
    """A reasoning item's readable text: its summary, or else the reasoning
    text an open-weights model returns in its content. Never replayed."""
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return ""
    for key, part_type in (("summary", "summary_text"), ("content", "reasoning_text")):
        parts = [p.get("text") for p in item.get(key) or []
                 if isinstance(p, dict) and p.get("type") == part_type]
        text = "\n\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if text:
            return text
    return ""


def _reasonings(output: Any) -> list[ReasoningComplete]:
    return [ReasoningComplete(text) for text in map(_reasoning_of, output or []) if text]


def _parse_output(output: list[dict[str, Any]]) -> tuple[str, tuple[ToolUseBlock, ...]]:
    text_parts: list[str] = []
    calls: list[ToolUseBlock] = []
    for item in output or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    text_parts.append(part.get("text") or "")
        elif kind == "function_call":
            # `or`, not a default: a key that is there and null is what a
            # gateway sends for a call with no arguments (D-200).
            calls.append(ToolUseBlock(
                call_id=item.get("call_id") or item.get("id") or "",
                name=item.get("name") or "",
                arguments=wire_arguments(item.get("arguments")),
            ))
        # Reasoning items are shown (_reasoning_of) and, encrypted, sent back
        # (_kept_reasoning, D-089).
    return "".join(text_parts), tuple(calls)


def _stop_reason(response: dict[str, Any], calls: Any, *, cut: bool = False) -> StopReason:
    """Why the turn ended (D-200); ``cut`` is the ``response.incomplete``
    event. An answer stopped at the output ceiling, or refused, is not a
    finished one, and both used to be reported as one. A status of
    "incomplete" with no reason given does not by itself say the answer was
    cut: the local gateway marks every turn that ends in a tool call that
    way, so such a turn is a tool-use turn."""
    details = response.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, dict) else None
    if reason == "content_filter":
        return StopReason.REFUSAL
    if cut or reason:
        return StopReason.MAX_TOKENS
    if calls:
        return StopReason.TOOL_USE
    if response.get("status") == "incomplete":
        return StopReason.MAX_TOKENS
    return StopReason.END_TURN


@dataclass(slots=True)
class ResponsesBackend:
    base_url: str
    model: str
    api_key: str = ""
    name: str = "responses"
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Added to every request's URL (D-084).
    query_params: dict[str, str] = field(default_factory=dict)
    client: HttpClient = field(default_factory=HttpClient)
    caps: Capabilities = field(default_factory=lambda: Capabilities(parallel_tool_calls=True))
    stream: bool = True
    recorder: Any = None
    max_retries: int = 4
    # Told of each retry before its wait (D-085); the runtime sets it.
    on_retry: Any = None
    # reasoning.summary ("auto", "concise", "detailed"); None sends nothing.
    reasoning_summary: str | None = None
    # reasoning.effort (D-061); None sends nothing.
    effort: str | None = None
    # text.verbosity (D-090); None sends nothing.
    verbosity: str | None = None
    # The model has answered with reasoning (D-089): from then on its
    # encrypted reasoning is asked for, as it is whenever reasoning is
    # configured. Never before: a model that does not reason may refuse it.
    reasons: bool = False

    @property
    def endpoint(self) -> str:
        return with_query(f"{self.base_url.rstrip('/')}/responses", self.query_params)

    def _headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        if self.api_key:
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
        body = build_body(request, stream=self.stream,
                          reasoning_summary=self.reasoning_summary, effort=self.effort,
                          include_reasoning=bool(self.reasons or self.effort
                                                 or self.reasoning_summary),
                          verbosity=self.verbosity)
        if self.recorder is not None:
            self.recorder.record(RequestRecord(model=request.model, body=body,
                                               endpoint=self.endpoint))
        if self.stream:
            return self._stream(body)
        return iter(self._single(body))

    def _single(self, body: dict[str, Any]) -> list[StreamEvent]:
        response = retry_with_backoff(
            lambda: self.client.post_json(self.endpoint, body, self._headers()),
            attempts=self.max_retries, on_retry=self.on_retry,
        )
        payload = response.json()
        if payload.get("status") == "failed" or isinstance(payload.get("error"), dict):
            # A failed response answers 200 too (D-147).
            raise_reported_error(payload.get("error"), where="in its answer")
        text, calls = _parse_output(payload.get("output", []))
        usage = _usage_from(payload.get("usage"))
        self.reasons = self.reasons or _has_reasoning(payload.get("output"))
        turn = ModelTurn(
            text=text, tool_calls=calls, usage=usage,
            stop_reason=_stop_reason(payload, calls),
            model=payload.get("model") or body["model"],
            opaque=_opaque(_kept_reasoning(payload.get("output")), body["model"]),
        )
        events: list[StreamEvent] = list(_reasonings(payload.get("output")))
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
        terminal: dict[str, Any] = {}
        cut = False
        shown_reasoning = False
        kept: list[dict[str, Any]] = []

        try:
            stream = open_retrying_stream(
                lambda: self.client.stream_sse(
                    self.endpoint, body, self._headers(),
                    # Responses signals completion with its own event, checked
                    # below, rather than the Chat Completions [DONE] sentinel.
                    expect_done_sentinel=False),
                attempts=self.max_retries, on_retry=self.on_retry,
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
                        "call_id": item.get("call_id") or item.get("id") or "",
                        "name": item.get("name") or "",
                        # What the item already carries: the local gateway
                        # sends a call's arguments whole here, and no delta.
                        "given": wire_arguments(item["arguments"]) if item.get("arguments") else "",
                        "arguments": "",
                    }

            elif kind == "response.function_call_arguments.delta":
                slot = partial.get(str(event.get("output_index", 0)))
                if slot is not None and isinstance(event.get("delta"), str):
                    slot["arguments"] += event["delta"]

            elif kind == "response.function_call_arguments.done":
                # The whole of them, in place of the pieces.
                slot = partial.get(str(event.get("output_index", 0)))
                if slot is not None and isinstance(event.get("arguments"), str) \
                        and event["arguments"]:
                    slot["arguments"] = event["arguments"]

            elif kind == "response.output_item.done":
                item = event.get("item") or {}
                reasoning = _reasoning_of(item)
                if reasoning:
                    shown_reasoning = True
                    yield ReasoningComplete(reasoning)
                if _has_reasoning([item]):
                    self.reasons = True
                    kept.extend(_kept_reasoning([item]))
                if item.get("type") == "function_call":
                    # What was streamed stands in for what the finished item
                    # leaves out: one with no arguments used to discard every
                    # piece that had come before it (D-200).
                    slot = partial.pop(str(event.get("output_index", 0)), None) or {}
                    calls.append(ToolUseBlock(
                        call_id=item.get("call_id") or item.get("id") or slot.get("call_id") or "",
                        name=item.get("name") or slot.get("name") or "",
                        arguments=wire_arguments(item.get("arguments") or slot.get("arguments")
                                                 or slot.get("given")),
                    ))

            elif kind in ("response.completed", "response.incomplete"):
                response = event.get("response") or {}
                usage = _usage_from(response.get("usage"))
                model = response.get("model") or model
                completed = True
                terminal, cut = response, kind == "response.incomplete"
                if not kept:
                    # A gateway that sends only the terminal event.
                    self.reasons = self.reasons or _has_reasoning(response.get("output"))
                    kept = _kept_reasoning(response.get("output"))
                if not calls and not text_parts and not shown_reasoning:
                    # Some gateways only send the terminal event.
                    for shown in _reasonings(response.get("output")):
                        shown_reasoning = True
                        yield shown
                # What the terminal event lists and the stream did not carry
                # (D-200). A gateway may send nothing but this event; or it
                # may stream the text and list the calls only here, and taking
                # the list only when nothing at all had been streamed dropped
                # such a call: the turn ended on its preamble. Streamed text
                # is repeated in the list, and so is a streamed call, so text
                # is taken only when none was streamed, and a call only when
                # the stream did not finish that one.
                text, parsed = _parse_output(response.get("output") or [])
                if text and not text_parts:
                    text_parts.append(text)
                    # Shown like streamed text: the screen draws deltas, so
                    # without one a REPL showed no answer at all.
                    yield TextDelta(text)
                no_calls_streamed = not calls and not partial
                finished = {call.call_id for call in calls}
                for call in parsed:
                    begun = next((key for key, slot in partial.items()
                                  if call.call_id and slot["call_id"] == call.call_id), None)
                    if begun is not None:
                        # Begun in the stream and never finished there: the
                        # list has it whole, unless it has no arguments.
                        slot = partial.pop(begun)
                        if call.arguments == "{}":
                            call = ToolUseBlock(
                                call_id=call.call_id, name=call.name or slot["name"],
                                arguments=slot["arguments"] or slot["given"] or "{}")
                        calls.append(call)
                    elif no_calls_streamed or (call.call_id and call.call_id not in finished):
                        calls.append(call)

            elif kind == "response.failed":
                raise_reported_error((event.get("response") or {}).get("error"),
                                     where="mid-turn")
            elif kind == "error":
                # The API's own error event has its code and message at the
                # top level; some gateways nest them under "error" (D-147).
                nested = event.get("error")
                raise_reported_error(
                    nested if isinstance(nested, dict) else
                    {k: event[k] for k in ("code", "message", "param") if event.get(k)},
                    where="mid-turn")

        # Any call whose 'done' event never arrived still has its fragments.
        for slot in partial.values():
            calls.append(ToolUseBlock(call_id=slot["call_id"], name=slot["name"],
                                      arguments=slot["arguments"] or slot["given"] or "{}"))

        if not completed:
            # Partial content without a completion event means the connection
            # was cut mid-turn; accepting it presented a truncated answer as the
            # model's complete one. The runtime asks the turn again (D-099).
            raise StreamDropped(
                "the response stream ended without a completion event; "
                "the connection was cut mid-turn"
            )

        stop_reason = _stop_reason(terminal, calls, cut=cut)
        if (stop_reason is StopReason.END_TURN and not text_parts and not kept
                and not shown_reasoning and not usage.input_tokens
                and not usage.output_tokens):
            # A completion event with nothing before it and nothing in it: no
            # text, no call, no reasoning, no tokens counted. A gateway that
            # opens the stream, fails inside and closes it tidily sends that,
            # and it was taken for a finished turn with an empty answer. The
            # turn is asked again, as the Chat Completions adapter does for a
            # stream that is only its terminator (D-200).
            raise StreamDropped(
                "the response stream carried nothing before its completion event; "
                "the provider closed the turn without answering"
            )

        for call in calls:
            yield ToolCallComplete(call)
        yield UsageUpdate(usage)
        yield TurnFinished(ModelTurn(
            text="".join(text_parts), tool_calls=tuple(calls), usage=usage,
            stop_reason=stop_reason,
            model=model, opaque=_opaque(kept, body["model"]),
        ))
