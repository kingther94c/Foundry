# ResearchOS

SPEC.md is the specification and is authoritative: implement exactly what it
says, with the module, class and function names it gives.

## Conventions

- Python 3.12. Dependencies: the standard library, `anthropic`, and the
  installed local package `tools` (see SPEC.md "Using tools").
- Only `researchos/llm.py` imports `anthropic`. Every other module receives
  its agents as objects with a `json(system, prompt, schema, *, prefix=None)`
  method, so tests use fakes and never touch the network or need a key.
- Every `open()` / `read_text()` / `write_text()` passes `encoding="utf-8"`;
  `EncodingWarning` is an error under pytest.
- Source files are ASCII.
- Tests live in tests/ and run with `python -m pytest -q`.

## The Claude API, as this project uses it

- Model `claude-opus-5-5`. Thinking is always on: never send `thinking`,
  `temperature`, `top_p` or `top_k` (they are a 400 on this model), and never
  force a tool with `tool_choice`.
- Structured output: `output_config={"effort": ..., "format": {"type":
  "json_schema", "schema": schema}}`. The response's content is a list of
  typed blocks (it can start with `thinking` blocks); the JSON is the text of
  the first block whose `type == "text"`.
- In a strict schema every object sets `"additionalProperties": False`; the
  API also limits how many union-typed parameters (type arrays, `anyOf`) a
  request may carry, so do not use them.
- Prompt caching: a block with `"cache_control": {"type": "ephemeral"}` caches
  the prompt up to and including that block. Put it on the end of the part
  several requests share, not on the varying tail.
- `stop_reason` is `"end_turn"`, `"max_tokens"` or `"refusal"` here; a
  refusal carries `stop_details` (which may be None) with a `category`.
- `response.usage` has `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens` (the last two may
  be None).
