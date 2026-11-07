# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Ctrl+C twice at the prompt ends the session (D-077), item 38: one says how,
so a Ctrl+C meant for a task that had just ended no longer ends the session.

## Changes

- `cli/repl.py`: `repl()` counts Ctrl+C at the prompt; `CTRL_C_AGAIN`; the
  line after a stopped task says how the session ends now.
- `tests/test_ctrl_c_twice.py` (new, 6 tests).
- Docs: D-077; README; design (the cancellation chain); roadmap item 38.

## Decisions and open points

- Anything read between two presses starts the count over; end of input
  still ends the session at once.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2592 passed, 1 skipped (2586 before, plus 6 in
  `tests/test_ctrl_c_twice.py`).
- Mutation check (scratch script): 6 mutants -- never armed, no hint,
  armed staying armed, an empty line not counting, end of input hinted, one
  press ending it. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/ctrl-c-twice`, from main at `04ccf1b`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_ctrl_c_twice.py` |
<!-- auto:end -->
