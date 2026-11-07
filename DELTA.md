# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`exec --trust` (D-137), item 98, as codex's: the repository trusted from
the command line, for an unattended run; `foundry review` takes it too.

## Changes

- `cli/app.py`: `build(trust=)` records the trust before anything of the
  repository's is read; `--trust` for exec and review; the unattended note
  names it.
- `tests/test_exec_trust.py` (new, 7 tests).
- Docs: D-137; README; requirements (trust on first use); threat model
  (trust boundaries); roadmap item 98.

## Decisions and open points

- Kept for later sessions, and even when the run is `--ephemeral`, as
  codex's; a folder outside Git is refused before anything is recorded.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4447 passed, 1 skipped (4440 before, plus 7 in
  `tests/test_exec_trust.py`).
- Mutation check (scratch script): 6 mutants. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-98`, from main at `58d47b3`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_exec_trust.py` |
<!-- auto:end -->
