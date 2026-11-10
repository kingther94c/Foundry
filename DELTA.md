# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A failure an OpenAI-family provider reports inside a 200 answer -- an error
event or `response.failed` in a Responses stream, an error chunk or a
`finish_reason` of `error` in Chat Completions, a failed answer that was not
streamed -- is now a failed turn asked again (D-099), or a stop when its code
says waiting cannot help, instead of a cut answer kept as whole (Chat) or a
run ended `failed` on a passing server error (Responses). Found by P1's
stand-in model behind a relay that injects these (D-146).

## Changes

- `core/backends/base.py`: `raise_reported_error`, the classifier both
  OpenAI-family adapters use: quota -> `QuotaExceeded`; a permanent code
  (context too long, refused or invalid request, an integer code < 500 other
  than 429) -> `FatalError`; anything else -> `StreamDropped`, carrying the
  wait a "try again in Ns" message names.
- `core/backends/openai_compat.py`: error chunks (OpenRouter's and vLLM's
  shapes) and `finish_reason: "error"`, streamed or not.
- `core/backends/responses.py`: the `error` event read with its code and
  message at the top level (or nested), `response.failed`, and a failed
  answer that was not streamed.
- `core/runtime.py`: a re-asked turn waits at least what the provider named.
- Docs: D-147, design (error taxonomy), roadmap (P1's first round).

## Decisions and open points

- D-147: unknown codes are asked again, as codex does; the try count of
  D-099 bounds that.
- Open from the same P1 round, not in this branch: a read-only sub-agent is
  still offered tools it may not use (D-148, its own branch); the unattended
  prompt says dirty files "need explicit approval", and command output keeps
  Windows line ends (D-149, after it); a tool-call budget is not told to the
  model.

## Verification

- `tests/test_reported_errors.py` (29): each shape asked again with nothing
  of it kept, each permanent one stopping at once with one request, the wait
  hint, non-streamed answers, and the classifier's table.
- Mutation check: 16 mutants of the fix (each branch removed or loosened);
  all killed after two tests were added for the two that first survived.
- The relay scenarios that showed the defect, run again on this branch.
- `tests/test_backend_responses.py`: the test that took `response.failed`
  for a protocol error now asks for it to be asked again; with
  `test_reported_errors.py`, 42 passed.
- Full suite before that update: 4685 passed, 1 skipped, 1 failed -- that
  test, asserting the old behaviour.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/stream-errors`, from main at `3a52f40`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_backend_responses.py` |
| A | `tests/test_reported_errors.py` |
<!-- auto:end -->
