# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/rename`, as codex's (D-125), item 86: a session's name, journaled, shown
in its listings and taken wherever its id is -- `foundry resume NAME`.

## Changes

- `core/session.py`: the `renamed` record.
- `core/resume.py`: `session_name` (what a name may be), `journal_name`,
  `named_session`; `resolve_session` takes a name after an id; the state a
  resume reads and the summary a listing shows carry the name.
- `cli/app.py`: the wiring's name; listings show it; `resume --all NAME`
  and `sessions NAME` look in every workspace.
- `cli/repl.py`: `/rename [NAME]`; `/status` shows the name.
- `tests/test_rename.py` (new, 33 tests).
- Docs: D-125; README; design; roadmap item 86.

## Decisions and open points

- Two sessions may share a name: the newer is found. Names match exactly.
- codex also suggests a name from the conversation; here the user gives it.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4243 passed, 1 skipped (4210 before, plus 33 in
  `tests/test_rename.py`).
- Mutation check (scratch script): 27 mutants over what a name may
  be, which name holds, the lookup (id first, workspace, newest), the
  listings, `/status`, and the command. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-86`, from main at `525836f`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_rename.py` |
<!-- auto:end -->
