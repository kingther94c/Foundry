# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`exec --color always|never|auto` (D-136), item 97, as codex's; `foundry
review` takes it too.

## Changes

- `cli/app.py`: `_stderr_console`, `--color` for exec and review.
- `cli/codex_flags.py`: D-097's hint for `--color` is dropped, now that
  the commands codex has it on take it; `tests/test_codex_flags.py` with it.
- `tests/test_exec_color.py` (new, 9 tests).
- Docs: D-136; README; roadmap item 97.

## Decisions and open points

- `always` wins over `NO_COLOR`, as asked for by name; stdout is never
  coloured.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4440 passed, 1 skipped (4444 before, plus 9 in
  `tests/test_exec_color.py`).
- Mutation check (scratch script): 6 mutants. All are caught. The count falls by 13 elsewhere: --color is no longer a codex-only flag, so its row and the 12 cases tests/test_codex_flags.py runs for each such flag go.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-97`, from main at `c12a990`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/codex_flags.py` |
| M | `tests/test_codex_flags.py` |
| A | `tests/test_exec_color.py` |
<!-- auto:end -->
