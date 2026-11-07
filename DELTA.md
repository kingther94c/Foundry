# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`exec --ephemeral` (D-132), item 93, as codex's: a run that keeps no record
once it ends. `foundry review --ephemeral` too.

## Changes

- `cli/app.py`: `build(ephemeral=)` puts the journal in a temporary folder
  of its own; `_close` and a failed build remove it (`_discard`);
  `--ephemeral` for exec and review, refused with `--resume`.
- `core/events.py`: `SessionStarted.ephemeral`; `cli/render.py` says
  `(ephemeral: not kept)`.
- `tests/test_ephemeral.py` (new, 9 tests).
- Docs: D-132; README; roadmap item 93; design; threat-model row.

## Decisions and open points

- The journal is written as usual while the run goes on -- the finish gate,
  hooks and artifacts need it -- but never among the sessions, so nothing
  lists or resumes it even meanwhile.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4408 passed, 1 skipped (4399 before, plus 9 in
  `tests/test_ephemeral.py`).
- Mutation check (scratch script): 11 mutants over where the journal
  goes, its removal, the flag and what the run says. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-93`, from main at `4e33b36`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/events.py` |
| A | `tests/test_ephemeral.py` |
<!-- auto:end -->
