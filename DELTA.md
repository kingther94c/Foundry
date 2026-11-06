# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The model's reasoning, shown where the provider gives it (D-044), the sixth
step of the codex-parity order. Every adapter now yields a whole
`ReasoningComplete` before the answer it led to -- Claude's thinking summary
(now asked for: the default leaves it empty), a Responses reasoning item's
summary (asked for only with `backend.reasoning_summary`), or the
`reasoning_content` a Chat Completions server streams. The runtime shows it as
a `Reasoning` event, dim under "thinking", and journals it on the
`model_response`; it never becomes part of the conversation.

## Changes

- `core/backends/base.py`: the `ReasoningComplete` stream event.
- `core/backends/anthropic.py`: `thinking.display = "summarized"` (the
  `thinking_display` field, None to omit it); a thinking block's text is
  yielded when the block ends, and from a non-streamed response.
- `core/backends/openai_compat.py`: `reasoning_content` / `reasoning`
  collected and yielded when the answer starts or at the end.
- `core/backends/responses.py`: `reasoning: {summary}` when configured;
  reasoning items yielded when done (summary, else `reasoning_text`), once.
  Also fixed: a terminal-only response's text is yielded as a delta, so the
  screen shows the answer (it was recorded but never drawn).
- `core/events.py`: the `Reasoning` event. `core/runtime.py`: emits it unless
  `show_reasoning` is off or the request is a compaction's; journals
  `reasoning` on `model_response`.
- `core/config.py`: `backend.reasoning_summary` (`auto`, `concise`,
  `detailed`, `none`) and `runtime.show_reasoning`, both reachable with `-c`.
  `cli/app.py` wires them; `cli/render.py` draws it, cut to eight lines unless
  verbose.
- `tests/test_reasoning.py` (new, 26 tests); `tests/test_backend_anthropic.py`
  expects the display field.
- Docs: D-044; design.md sections 3 and 8; threat-model row; README; roadmap.

## Decisions and open points

- Reasoning arrives whole, not delta by delta: the redactor sees one string,
  and a summary is a few sentences. Live streaming of long raw reasoning is
  deferred.
- Responses does not ask for a summary by default: a model that does not
  reason answers 400.
- Deferred: Anthropic's `display = "updates"` (beta), a `/reasoning` toggle.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1862 passed (1836 before, plus 26 in `tests/test_reasoning.py`).
- Mutation check (scratch script): 34 mutants over the request fields, each
  adapter's yield and its order, stripping, odd shapes, the shown-once guard,
  the terminal-only text, the runtime's gates and journal record, the
  renderer's cut, escaping and sanitizing, the config checks and the wiring.
  32 caught on the first run; of the two survivors, one was redundant code (a
  flush at the first tool-call delta, which the end-of-stream flush already
  covers -- removed) and one is equivalent (the thinking-block type check: no
  other block type carries a `thinking` string; kept as intent).
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/reasoning`, from main at `dbb3e11`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/backends/anthropic.py` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/events.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_backend_anthropic.py` |
| A | `tests/test_reasoning.py` |
<!-- auto:end -->
