# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/warnings` (D-138), item 99, as codex's: the warnings and errors the
session has shown, again.

## Changes

- `cli/render.py`: the renderer keeps each warning and error it shows, with
  its time, the latest `KEPT_WARNINGS` (200).
- `cli/repl.py`: `/warnings`.
- `tests/test_warnings.py` (new, 6 tests).
- Docs: D-138; README; roadmap item 99.

## Decisions and open points

- Information notices are not kept; a new session starts with none; nothing
  more goes to the journal.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4453 passed, 1 skipped (4447 before, plus 6 in
  `tests/test_warnings.py`).
- Mutation check (scratch script): 9 mutants. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-99`, from main at `2f0d0f8`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_warnings.py` |
<!-- auto:end -->
