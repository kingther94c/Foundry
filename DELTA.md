# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Retries you can see (D-085), item 46, as codex's "Reconnecting...": each
retry of a model request is said before its wait (`server error (HTTP 503);
retrying in 4s (try 2 of 4)`), in the terminal and in `exec --json`,
journaled as `model_retry` and counted by `foundry report`. A 429 that says
the quota is spent (`insufficient_quota`) is not retried: the task ends
blocked at once.

## Changes

- `core/httpc.py`: `retry_with_backoff` and `open_retrying_stream` take
  `on_retry`, called before each wait; `insufficient_quota` raises
  `QuotaExceeded`.
- `core/errors.py`: `QuotaExceeded` (a `FatalError`, category `quota`).
- `core/backends/*`: an `on_retry` field, passed on both paths.
- `core/runtime.py`: the runtime sets the backend's `on_retry`; `_retrying`
  journals and says it; `QuotaExceeded` ends the task blocked.
- `core/session.py`: `EventType.MODEL_RETRY`.
- `cli/report.py`: `model retries` / `model_retries`.
- `tests/test_visible_retries.py` (new, 22 tests).
- Docs: D-085; design; requirements; roadmap item 46; README.

## Decisions and open points

- Told before the wait, not after: the wait is what needs explaining.
- A spent quota is blocked, not failed: the task itself did nothing wrong.
- Not changed: what is retried, how often, how long.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2748 passed, 1 skipped (2726 before, plus 22 in
  `tests/test_visible_retries.py`).
- Mutation check (scratch script): 21 mutants -- retries never told,
  told after the wait, the try counted from 1, the cap not applied, a stream
  retried silently, a spent quota retried, each backend's two paths silent,
  the runtime not listening, not journaled, the provider's words dropped,
  said as info, tries swapped, a wait rounded, a spent quota failing the
  task, the report and its summary counting none. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/visible-retries`, from main at `df081de`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/report.py` |
| M | `src/foundry/core/backends/anthropic.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/errors.py` |
| M | `src/foundry/core/httpc.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_visible_retries.py` |
<!-- auto:end -->
