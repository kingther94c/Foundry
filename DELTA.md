# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A task's ending is not the session's (D-063), item 24: at the interactive
prompt, a task that ends with `finish`, a budget or a full context says how
and the prompt comes back, as codex's does; only the user ends the session.
The calls the task left -- `finish` among them -- are answered before
anything is sent again.

## Changes

- `core/runtime.py`: `answer_left_calls()` (finish as accepted with its
  status, the rest as not run); `_ended`, kept by `_terminate`.
- `cli/repl.py`: every task's ending goes back to the prompt, with a line
  saying the session is still open; the calls left are answered first.
- `tests/test_task_end.py` (new, 9 tests).
- Docs: D-063; design (the termination state machine); README; roadmap
  item 24.

## Decisions and open points

- Unattended runs end with their task, and their journals are unchanged; a
  resume answers what they left, as before.
- An interactive session's exit code stays 13 when its user ends it.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2318 passed, 1 skipped (2309 before, plus 9 in
  `tests/test_task_end.py`), including a run through `main()` against the
  fake model server whose second request carries the tool message for
  finish, and a resume that rebuilds the live history with nothing to
  repair.
- Mutation check (scratch script): 8 mutants -- the answers, the line,
  the session going on, the ending kept, finish accepted, the generic reason
  left out, the fallback, and not-run as an error. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/task-end`, from main at `c4a7e3a`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_task_end.py` |
<!-- auto:end -->
