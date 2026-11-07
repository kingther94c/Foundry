# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The most one reply may be (D-096), item 57, as codex's
`model_max_output_tokens`: `[backend] max_output_tokens = N` goes on every
request -- `max_tokens` for Claude, `max_output_tokens` on Responses,
`max_completion_tokens` on Chat Completions -- and the room kept for the
answer is at least that much.

## Changes

- `core/config.py`: `MIN_OUTPUT_TOKENS`; `BackendConfig.max_output_tokens`,
  checked in `_validate_backend_setting`; codex's name in `_CALLED_HERE`.
- `core/runtime.py`: `AgentRuntime.max_output_tokens`, put on every
  `TurnRequest`.
- `core/backends/openai_compat.py`: `max_completion_tokens` in place of
  `max_tokens` (only ever sent when a cap is set).
- `cli/app.py`: `build` refuses a cap past half the window, hands it to the
  runtime, and raises the context's reserve to it.
- `tests/test_max_output_tokens.py` (new, 21 tests).
- Docs: D-096; README; design; roadmap item 57.

## Decisions and open points

- `max_completion_tokens` on Chat Completions: OpenAI's reasoning models
  refuse `max_tokens`; a server that knows only `max_tokens` ignores it.
- The reserve is the larger of the cap and 8,000, never less than before.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3017 passed, 1 skipped (2996 before, plus 21 in
  `tests/test_max_output_tokens.py`).
- Mutation check (scratch script): 11 mutants -- any value or a tiny
  cap taken, the floor itself refused, codex's name not told, the runtime
  never sending it, Chat Completions sending `max_tokens`, build dropping it,
  more than half the window taken or half refused, the reserve shrinking
  below the default or ignoring the cap. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/max-output-tokens`, from main at `913496b`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_max_output_tokens.py` |
<!-- auto:end -->
