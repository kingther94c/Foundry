# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A response stream cut part-way is asked again (D-099), item 60, as codex's
`stream_max_retries`: the runtime sends the same request again, up to
`[backend] stream_max_retries` times (5), saying so, instead of ending the task
blocked with half an answer on screen.

## Changes

- `core/errors.py`: `StreamDropped`, a `TransientError` for a stream that
  broke after it began.
- `core/httpc.py`: `open_retrying_stream` marks failures after the first event
  (`_begun`); the old "not retried" note rewritten.
- `core/backends/anthropic.py`, `openai_compat.py`, `responses.py`: their
  "ended without" checks, and Claude's transient error event in a stream,
  raise `StreamDropped`.
- `core/runtime.py`: `_sample` asks again on `StreamDropped` with doubling
  waits (`stream_max_retries`, `sleep`); `_sample_once` is the old body and
  notes whether text was shown; `_retrying` says "asking again" and journals
  `cut`.
- `core/config.py`: `BackendConfig.stream_max_retries` (0 or more).
- `cli/app.py`: build hands it to the runtime.
- `tests/test_stream_retries.py` (new, 22 tests).
- Docs: D-099; README; design; roadmap item 60.

## Decisions and open points

- Safe to send again: a turn's tool calls run only once it has finished.
- A failure before the stream began keeps only the request's own retries.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3244 passed, 1 skipped (3222 before, plus 22 in
  `tests/test_stream_retries.py`).
- Mutation check (scratch script): 18 mutants -- never asked again, the
  setting ignored or one try short, no backoff, asked again after a cancel,
  what was shown misreported either way or always, the journal not told, told
  as a retry, a cut connection or payload not carried, each adapter's mark
  removed, any value taken, build dropping it. All are caught; one more
  (marking the first event too) is equivalent and not counted.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/stream-retries`, from main at `9005715`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/backends/anthropic.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/errors.py` |
| M | `src/foundry/core/httpc.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_stream_retries.py` |
<!-- auto:end -->
