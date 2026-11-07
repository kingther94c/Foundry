# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/fork` and `foundry fork`, as codex's (D-126), item 87: a copy of a
session -- its journal record for record, under a header of its own --
resumed and continued apart from it; the source is kept as it was.

## Changes

- `core/resume.py`: `fork_journal`; `JournalState.fork_of` while a fork
  has not run; the resume note for a fork's first run.
- `cli/app.py`: `foundry fork` (resume's arguments); `/fork` handled in the
  session loop (`_switch_to_fork`); a fork that cannot be resumed is
  removed; the resume notice names a fork.
- `cli/repl.py`: `/fork`.
- `tests/test_fork.py` (new, 14 tests).
- Docs: D-126; README; design; threat model; roadmap item 87.

## Decisions and open points

- The source's names stay the source's; a fork is named with /rename.
- codex's `exec fork` and `--worktree` are not here.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4257 passed, 1 skipped (4243 before, plus 14 in
  `tests/test_fork.py`).
- Mutation check (scratch script): 19 mutants over the copy (what is
  kept, the header, the artifacts, a failed copy), the fork's first note,
  `foundry fork`, and `/fork` (the switch, a fork that cannot go on, the
  session it came from). All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-87`, from main at `8d29163`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/resume.py` |
| A | `tests/test_fork.py` |
<!-- auto:end -->
