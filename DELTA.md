# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`exec --fork SESSION [TASK]` and `exec fork [--last | SESSION] [TASK]`
(D-133), item 94, as codex's: a copy of a recorded session continued
headlessly, the source kept as it was.

## Changes

- `cli/app.py`: `cmd_exec` forks with `_fork` (D-126), then resumes the
  copy, dropping it when it cannot go on; `--fork`; `exec_resume` reads
  `exec fork` as it reads `exec resume`, its parsing loop now
  `_exec_words`, which also names the word in the error.
- `tests/test_exec_fork.py` (new, 16 tests).
- Docs: D-133; README; roadmap item 94.

## Decisions and open points

- The task defaults to carrying on, as with `--resume`; a completed source
  needs one. `--fork` with `--resume` or `--ephemeral` is refused.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4424 passed, 1 skipped (4408 before, plus 16 in
  `tests/test_exec_fork.py`).
- Mutation check (scratch script): 11 mutants over the fork, its
  checks and the `exec fork` spelling. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-94`, from main at `46e8dbf`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_exec_fork.py` |
<!-- auto:end -->
