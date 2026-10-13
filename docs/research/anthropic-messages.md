# The Anthropic Messages backend (2026-10-13)

Foundry reached Claude through Anthropic's OpenAI-compatible endpoint, which has
no prompt caching: every round re-sent the whole transcript at full price. In
the dogfooding runs of 2026-10-12, Foundry spent 1.69M input tokens building
`examples/earnings_pm` with `cached_input_tokens` at 0 throughout, while the
earnings agent itself, on the native API with caching, read 398k of its 443k
input tokens from cache. This note records what was measured on the native
Messages API and the contract the `anthropic_messages` adapter follows.

## Why the wire protocol, not the SDK

`foundry.core` imports no third-party package (enforced by
`tests/test_architecture_config.py`), and the runtime's dependency budget is
`rich` alone. The official `anthropic` SDK would bring its HTTP stack into an
offline wheelhouse and into core. Both existing adapters already speak their
protocols over `httpc`; this one does the same.

## Measured on the live API

Fixtures in `tests/fixtures/anthropic/` are the exact bytes `claude-opus-5-5`
returned for a two-round tool loop (round two replays round one's content
verbatim) and for the same first round without streaming.

Request (accepted as is):

```http
POST https://api.anthropic.com/v1/messages
content-type: application/json
x-api-key: <key>
anthropic-version: 2023-06-01
anthropic-beta: thinking-binding-controls-2026-08-01,server-side-fallback-2026-07-01

{"model": "claude-opus-5-5", "max_tokens": 64000, "stream": true,
 "system": [{"type": "text", "text": "..."}],
 "tools": [{"name": "...", "description": "...", "input_schema": {...}}],
 "messages": [...],
 "cache_control": {"type": "ephemeral"},
 "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
 "output_config": {"effort": "high"},
 "fallbacks": "default"}
```

Response stream, as observed:

- One JSON object per `data:` line, each with a `type`; `event:` lines repeat
  it. Lines can carry trailing padding spaces inside the JSON. There is no
  `[DONE]`: the stream ends with `message_stop`.
- `message_start.message` carries `model`, `id`, `usage` (`input_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`)
  and `input_transformations` (an array; empty when nothing was dropped).
- `content_block_start` gives `index` and the block's initial shape:
  `{"type": "thinking", "thinking": "", "signature": ""}`,
  `{"type": "text", "text": ""}`, or
  `{"type": "tool_use", "id", "name", "input": {}, "caller": {"type": "direct"}}`
  -- note `caller`, a field no earlier documentation listed.
- `content_block_delta` deltas seen: `thinking_delta` (`thinking`, empty under
  the default display), `signature_delta` (`signature`), `text_delta` (`text`),
  `input_json_delta` (`partial_json`). Then `content_block_stop`.
- `message_delta` carries `delta.stop_reason`, `delta.stop_details`, and a
  `usage` that repeats the input and cache counts with the final
  `output_tokens`.
- Round one: `[thinking, text, tool_use]`, stop `tool_use`, 4202 tokens written
  to the cache. Round two, with round one's content replayed verbatim (the
  `caller` field and the thinking signature included) and a `tool_result`:
  `[thinking, text]`, stop `end_turn`, 4202 tokens read from the cache, 511
  written, `input_transformations` empty -- nothing dropped.

The lesson in `caller`: an adapter that rebuilds content blocks field by field
silently drops whatever the API adds next, and a thinking block whose prefix
changed is dropped or rejected. Replay what the API returned, as returned.

## The adapter's contract

### IR additions (additive, per conversation.py's rule)

- `OpaqueBlock(provider: str, data: str)`: provider content the runtime never
  interprets, carried so an adapter can replay it. `data` is JSON text, which
  keeps the frozen IR immutable. Added to the `ContentBlock` union.
- `ModelTurn.opaque: OpaqueBlock | None = None`, appended by `as_message()`
  after the text and tool-use blocks. Adapters that do not know a provider
  ignore its opaque blocks; the existing two already ignore any block that is
  not text or tool use, and must keep doing so.
- `ModelTurn.notices: tuple[str, ...] = ()`: things the provider reported that
  the user should hear about (dropped thinking, a fallback model taking over, a
  refusal). The runtime emits each as a warning `Notice` and journals them with
  the model response.
- The journal must hold enough to rebuild a request, so the runtime's
  `_block_payload` records an opaque block as
  `{"kind": "opaque", "provider": ..., "data": ...}` rather than `unknown`.

### Request

- `system`: the text of every `Role.SYSTEM` message, joined with a blank line,
  as one `{"type": "text"}` block. Built once from the same IR every round, so
  it is byte-stable.
- `tools`: `{"name", "description", "input_schema": parameters}` in registry
  order.
- `messages`:
  - A user message: `{"role": "user", "content": [{"type": "text", "text"}]}`.
  - An assistant message carrying an `OpaqueBlock(provider="anthropic")`: its
    `data`, parsed, is the content -- verbatim, nothing added or removed.
    Without one (a turn some other way produced), text then
    `{"type": "tool_use", "id", "name", "input"}` built from the IR.
  - `Role.TOOL` messages become `tool_result` blocks
    (`{"type": "tool_result", "tool_use_id", "content", "is_error"}`, with
    `is_error` only when true). Consecutive user-side messages -- tool results
    and user text -- merge into one user message, `tool_result` blocks first,
    because the API requires them before any text.
  - An assistant message with no content at all is left out.
- `max_tokens`: the request's own limit, else 64000 when streaming and 16000
  when not (long agentic turns need the room; thinking counts toward it).
- `cache_control: {"type": "ephemeral"}` at the top level: the API caches up
  to the last cacheable block, and the next round reads it.
- `thinking: {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior":
  "drop_block"}}` with beta `thinking-binding-controls-2026-08-01`. Observation
  masking (D-031) rewrites old tool results under context pressure, and on an
  account created on or after 2026-08-31 a replayed thinking block after such
  an edit is a 400. `drop_block` makes the API drop those blocks instead and
  report each in `input_transformations`, which become notices.
- `output_config: {"effort": ...}` only when configured; no `temperature`
  unless the request sets one (claude-opus-5-5 rejects sampling parameters),
  never `tool_choice` (forced choices are a 400 on this model).
- `fallbacks: "default"` with beta `server-side-fallback-2026-07-01`, unless
  turned off: a safety-classifier decline is re-run on Anthropic's recommended
  fallback model inside the same call instead of ending the turn.
- Headers: `x-api-key`, `anthropic-version: 2023-06-01`, `anthropic-beta`
  joining the betas in use with commas. Never `Authorization`.

### Response

- Assemble blocks by `index` from `content_block_start` plus deltas:
  `text_delta` appends to `text` (and streams as `TextDelta`), `thinking_delta`
  to `thinking`, `signature_delta` to `signature`, `input_json_delta`
  accumulates; at `content_block_stop` a tool-use block's `input` becomes the
  parsed JSON (`{}` when empty). Unknown delta types are ignored; unknown
  block types are kept as they started.
- The opaque content is every block, in index order, after the fallback rule
  below. The turn's text is the concatenation of its text blocks; its tool
  calls are its tool_use blocks (`arguments` = the raw accumulated JSON text,
  `"{}"` when empty).
- Fallback rule: if any block has `type == "fallback"`, everything before the
  last such block is a declined attempt. From that part keep only `text`
  blocks; drop thinking, redacted thinking, tool use and anything else. Drop the
  fallback block itself. Tool calls and text come from what remains. Add a
  notice naming `from.model` and `to.model`.
- `stop_reason`: `end_turn` and `stop_sequence` and `pause_turn` -> END_TURN,
  `tool_use` -> TOOL_USE, `max_tokens` and `model_context_window_exceeded` ->
  MAX_TOKENS, `refusal` -> REFUSAL. A refused turn yields no tool calls, and a
  notice says the model declined, with `stop_details.category` when present
  (`stop_details` may be null).
- Usage: `input_tokens` = input + cache creation + cache read (all prompt
  tokens, as the other adapters report), `cached_input_tokens` = cache read,
  `output_tokens` from the last usage seen. Later usage objects override
  earlier ones field by field.
- `input_transformations` entries with `type == "thinking_dropped"` become
  notices that name the `path` and `reason`.
- An `error` event raises `TransientError` for `overloaded_error` and
  `api_error`, `FatalError` otherwise, with the API's message. A stream that
  ends without `message_stop` raises `TransientError`, as the Chat Completions
  adapter does for a missing `[DONE]`; no content before the end is the same.
- Non-streaming: the response JSON's `content`, `stop_reason`, `usage`,
  `input_transformations` go through the same assembly. A server that answers
  a stream request with JSON degrades to this path, as the other adapters do.

### Capabilities

`parallel_tool_calls=True`, `prompt_caching=True`, `max_context_tokens`
200000 unless configured: the smallest window among current Claude models, so
masking (which starts at half of it) is never late.
