# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

How much of the context window is left, above the prompt (D-054), item 15:
once the conversation has begun, a dim "N% context left" line sits above
`foundry>`, as codex shows it under its input. It is 100 less the share
`/status` shows, from the same estimate, never below 0, and yellow under 20%.

## Changes

- `cli/repl.py`: `context_left`, `prompt_for` and `CONTEXT_LOW_PERCENT`;
  `read_message` takes the prompt, and the REPL passes it.
- `tests/test_context_left.py` (new, 5 tests).
- Docs: D-054; README (the prompt paragraph); roadmap item 15.

## Decisions and open points

- The figure is the one `/status` shows, not the window less the output
  reserve: one figure for one window.
- No status line fixed at the bottom of the screen (a full-screen UI, which
  D-052 rejected).

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2157 passed, 1 skipped (2152 before, plus 5 in
  `tests/test_context_left.py`).
- Mutation check (scratch script): 9 mutants -- the clamp at zero,
  agreement with `/status`, left rather than used, the plain prompt before
  the first turn and the line after it, yellow and its threshold, the prompt
  reaching `input`, and the REPL passing it. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/context-left`, from main at `b2d3807`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_context_left.py` |
<!-- auto:end -->
