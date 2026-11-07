# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry archive`, `unarchive` and `delete`, `/archive` and `/delete`, as
codex's (D-128), item 89: a session, by id or name, moved whole out of every
list and lookup and back, or deleted for good once the user says so.

## Changes

- `core/resume.py`: `ARCHIVED` (`archived_sessions/`), `session_named`,
  `move_session`, `delete_session`; a resume of an archived session says so.
- `cli/app.py`: `foundry archive`, `unarchive`, `delete [--force]`,
  `foundry sessions --archived`; `/archive` and `/delete` done once the
  session is closed.
- `cli/repl.py`: `/archive`, `/delete` (asked first).
- `tests/test_archive.py` (new, 20 tests).
- Docs: D-128; README; design; threat model; roadmap item 89.

## Decisions and open points

- A name finds the newest session of that name in any workspace; the delete
  question shows which one, and `--force` takes only an id, as codex's.
- `/archive` and `/delete` end the session rather than start another.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4356 passed, 1 skipped (4336 before, plus 20 in
  `tests/test_archive.py`).
- Mutation check (scratch script): 18 mutants over the lookup, the
  move (in use, over another), the delete (in use, asked, forced by id),
  the listing, and `/archive` and `/delete`. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-89`, from main at `75c7a24`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/resume.py` |
| A | `tests/test_archive.py` |
<!-- auto:end -->
