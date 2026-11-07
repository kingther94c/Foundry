# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The model's context window on every protocol, and compaction before it is
full (D-095), item 56: `backend.max_context_tokens` was applied to Claude
alone -- the OpenAI shapes planned against 128,000 tokens whatever the model
held -- and in a small window compaction started past the point the window
counts as full, so a long task ran out instead of compacting.

## Changes

- `cli/app.py`: `build` sets the window on any backend, after it is made,
  keeping its other capabilities.
- `core/context.py`: `compaction_threshold()` is also at most the window less
  the room kept for the answer.
- `core/config.py`: the comment on `max_context_tokens`.
- `tests/test_context_window.py` (new, 14 tests);
  `tests/test_compact_limit.py`: one test gives its context no reserve.
- Docs: D-095; README; design; roadmap item 56.

## Decisions and open points

- With the default room for the answer, windows of 40,000 tokens or more
  compact where they did.
- Seen in passing: Claude's requests ask for up to 64,000 output tokens
  while the room kept for the answer is 8,000; a `max_output_tokens`
  setting (codex's `model_max_output_tokens`) is the next item.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2996 passed, 1 skipped (2982 before, plus 14 in
  `tests/test_context_window.py`).
- The small-window task ends `partial(context window exhausted)` under the
  old threshold and compacts under the new one.
- Mutation check (scratch script): 6 mutants -- the window never
  applied, applied to Claude alone, the other capabilities lost, compaction
  past the full window, the room for the answer ignored, one token early. All
  are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/context-window`, from main at `dec1947`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/context.py` |
| M | `tests/test_compact_limit.py` |
| A | `tests/test_context_window.py` |
<!-- auto:end -->
