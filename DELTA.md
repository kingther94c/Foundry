# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

As an interactive session ends (D-059), item 20, one dim line says the
tokens it used and the `foundry resume <id>` command that continues it (with
`--workspace` from another folder), as codex prints them. On `/exit`, Ctrl+C,
a one-shot task, and `/new` for the session it ends; nothing for a session
that never began; an incomplete journal is said instead of a resume.

## Changes

- `cli/app.py`: `farewell`, `_say_farewell`, called where `cmd_session`
  closes a session.
- `tests/test_farewell.py` (new, 9 tests).
- Docs: D-059; README; roadmap item 20.

## Decisions and open points

- `exec` is unchanged; that it does not report its session id is the next
  gap.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2251 passed, 1 skipped (2242 before, plus 9 in
  `tests/test_farewell.py`).
- Mutation check (scratch script): 10 mutants -- usage alone, nothing
  for nothing, cached and only when some, the incomplete journal, the
  workspace from another folder and not from it, `/new`, the end, and no
  blank line. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/farewell`, from main at `3e08aac`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_farewell.py` |
<!-- auto:end -->
