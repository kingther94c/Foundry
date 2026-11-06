# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

How long a task took (D-073), item 34: `worked for 1m 12s` under each task
as it ends, as codex closes its turns -- the time from the task's first
request to its answer or termination.

## Changes

- `cli/render.py`: `Renderer.clock`, `_task_started`, `_worked()`; the
  start is taken at a task's first `TurnStarted` and cleared by `settle()`.
- `tests/test_worked_for.py` (new, 7 tests).
- Docs: D-073; design (the UI notes); roadmap item 34.

## Decisions and open points

- Nothing for a task under a second. In `exec`, each pass of its loop is
  timed on its own.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2522 passed, 1 skipped (2515 before, plus 7 in
  `tests/test_worked_for.py`).
- Mutation check (scratch script): 7 mutants -- `settle` or the end
  keeping the start, an end without a start, under a second said, each
  round restarting, and a termination or an answer saying nothing. All are
  caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/worked-for`, from main at `28b4d9b`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/render.py` |
| A | `tests/test_worked_for.py` |
<!-- auto:end -->
