# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The rest of codex's slash commands (D-140), item 101: `/cwd` is `/pwd`, and
each command in codex's popup Foundry has no counterpart for is told what
Foundry has instead.

## Changes

- `cli/repl.py`: `/cwd`; `CODEX_COMMANDS` gains the rest of codex's.
- `tests/test_codex_commands_rest.py` (new, 27 tests), holding codex's
  list against Foundry's commands and the hints.
- Docs: D-140; README; roadmap item 101.

## Decisions and open points

- codex's debugging commands (`/test-approval`, `/debug-m-*`) are left out.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4504 passed, 1 skipped (4477 before, plus 27 in
  `tests/test_codex_commands_rest.py`).
- Mutation check (scratch script): 6 mutants. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-101`, from main at `321f579`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_codex_commands_rest.py` |
<!-- auto:end -->
