# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

codex's smaller commands (D-130), item 91: `/recap`, `/clear`, `/rollout`,
`/pwd`; and `/permissions`, `/cd`, `/usage`, `/debug-config`, `/ps` and
`/stop` told Foundry's way.

## Changes

- `cli/repl.py`: the four commands; six more of codex's in
  `CODEX_COMMANDS`.
- `tests/test_small_commands.py` (new, 10 tests).
- Docs: D-130; README; roadmap item 91.

## Decisions and open points

- `/recap` is an answer in the conversation, read-only; `/compact` is the
  one that replaces it.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4379 passed, 1 skipped (4369 before, plus 10 in
  `tests/test_small_commands.py`).
- Mutation check (scratch script): 8 mutants over the four commands
  and the hints. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-91`, from main at `fbe8389`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_small_commands.py` |
<!-- auto:end -->
