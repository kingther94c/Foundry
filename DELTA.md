# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

codex's slash commands (D-101), item 62: `/approvals`, `/mention`, `/logout`
and `/feedback` are answered with what Foundry has instead, not "unknown
command".

## Changes

- `cli/repl.py`: `CODEX_COMMANDS`, read by `dispatch` after the user's own
  prompts and before "unknown command".
- `tests/test_codex_commands.py` (new, 8 tests).
- Docs: D-101; README; roadmap item 62.

## Decisions and open points

- Hints, not aliases, as for codex's flags (D-097).

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3264 passed, 1 skipped (3256 before, plus 8 in
  `tests/test_codex_commands.py`).
- Mutation check (scratch script): 5 mutants -- never told, told and
  called unknown too, a mode left out, `/logout` not known, codex's name ahead
  of the user's prompt. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/codex-commands`, from main at `f12f7c2`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_codex_commands.py` |
<!-- auto:end -->
