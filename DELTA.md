# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`-C DIR`, as codex spells it (D-072), item 33: `-C`, `--cd` and
`--workspace` name the one setting on `run`, `resume`, `exec`, `undo` and
`record`, so `foundry -C ../api` works as `codex -C ../api` does.

## Changes

- `cli/app.py`: the five `--workspace` arguments take `-C` and `--cd` too.
- `tests/test_cd_flag.py` (new, 19 tests).
- Docs: D-072; README (the line before the flags); roadmap item 33.

## Decisions and open points

- The same destination under three names: every workspace check is made
  against the directory the user named, whichever spelling they typed.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2515 passed, 1 skipped (2496 before, plus 19 in
  `tests/test_cd_flag.py`).
- Mutation check (scratch script): 5 mutants -- each command losing
  a name or its destination. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/cd-flag`, from main at `6557f03`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_cd_flag.py` |
<!-- auto:end -->
