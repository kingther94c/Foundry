# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Working, and for how long (D-069), item 30: while the model is asked or a
tool runs, a line says so with the time taken -- `working (12s, Ctrl+C to
stop)` -- as codex shows its `Working` line, so a long test run no longer
looks like a hang.

## Changes

- `cli/render.py`: `elapsed()`, `Working` (the line), `Renderer.work()` /
  `idle()`; up on `turn_started` and `tool_begin`, down before any other
  event is drawn and before an approval is asked.
- `cli/repl.py` `run_task` and `cli/app.py` `_close`: take it down however
  a task or the session ends.
- `tests/test_working.py` (new, 27 tests).
- Docs: D-069; design (the event protocol's UI notes); roadmap item 30.

## Decisions and open points

- Only on a terminal (never into a pipe or a redirected stderr), and
  transient: nothing of it is left once it is down.
- Seen in tests through a forced terminal; not looked at by eye on a real
  Windows console in this session.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2441 passed, 1 skipped (2414 before, plus 27 in
  `tests/test_working.py`).
- Mutation check (scratch script): 12 mutants -- the minutes, the hour
  boundary, the start time, drawing into a pipe, one line at a time, idle not
  stopping, events drawn over it, not up for the model or a tool, up during
  an approval, and `run_task` and `_close` leaving it up. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/working`, from main at `9896dd4`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_working.py` |
<!-- auto:end -->
