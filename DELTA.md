# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/resume [SESSION]` (D-134), item 95, as codex's: another of this
workspace's sessions continued from inside one, picked or named; this one
ends, and can be resumed.

## Changes

- `cli/repl.py`: `/resume`, `RESUME_SESSION`.
- `cli/app.py`: `_switch_to_resumed`; the loop goes on in the session it
  returns; `pick_session`'s question is `_ask_which`, shared.
- `tests/test_resume_command.py` (new, 9 tests).
- Docs: D-134; README; roadmap item 95.

## Decisions and open points

- Only this workspace's sessions. The other is opened first, with every
  check a resume makes; one that cannot be is not, and this session goes on.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4433 passed, 1 skipped (4424 before, plus 9 in
  `tests/test_resume_command.py`).
- Mutation check (scratch script): 12 mutants over choosing, opening
  and switching. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-95`, from main at `7cf5838`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_resume_command.py` |
<!-- auto:end -->
