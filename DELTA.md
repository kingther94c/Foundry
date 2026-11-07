# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/side` and `/btw`, as codex's (D-129), item 90: a question asked with the
conversation so far, read-only, then dropped from it -- the conversation
goes back to where it was, journaled as a rewind.

## Changes

- `core/runtime.py`: `aside` and `back_from_aside` (the history, what the
  model had read, its plan). `core/tools/base.py`: `ReadTracker.snapshot`
  and `restore`.
- `cli/repl.py`: `/side`, `/btw`; a side question spends no note or image
  owed to the next message, and is not compacted while it runs.
- `tests/test_side.py` (new, 13 tests).
- Docs: D-129; README; design; threat model; roadmap item 90.

## Decisions and open points

- One question, not a side conversation of several turns as codex's.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4369 passed, 1 skipped (4356 before, plus 13 in
  `tests/test_side.py`).
- Mutation check (scratch script): 13 mutants over going back (the
  history, the reads, the plan, a stopped question), read-only, the note
  and images kept, compaction off meanwhile, and the commands. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-90`, from main at `1af9871`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/base.py` |
| A | `tests/test_side.py` |
<!-- auto:end -->
