# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Undo for a session's patches (D-036). `apply_patch` now records, for each file
it changes, an exact copy of what the file held before and a digest of what it
wrote. `foundry undo [session] [--last] [--dry-run] [--yes]`, and `/undo` in a
session, put that content back -- only where the file still holds exactly what
the session wrote, so an edit made since (by the user or a command) is never
discarded.

## Changes

- `core/tools/patch.py`: `_apply_one` records `changes` (path, `before`
  artifact, `before_sha`, `after`, `kept`; a move gives two linked entries) in
  the tool's metadata; `_write_atomic` returns the bytes it wrote. A store that
  cannot keep the content costs undo that file, never the patch.
- `core/session.py`: `ArtifactStore.put_exact` (keeps bytes only if redaction
  would not change them) and `read_bytes`; `EventType.UNDO`, synced on write.
- `core/runtime.py`: `tool_result` carries `changes`; `files_changed(ordinal,
  paths)` moves the last-mutation mark and forgets the files read.
- `core/undo.py` (new): `recorded_changes`, `plan_undo` (per file, newest
  first, stopping at an outside edit, a change between patches, content not
  kept, or a move that cannot be reverted whole), `apply_undo` (re-checks each
  file, verifies the kept copy's digest, writes through `Workspace.resolve`),
  `undo_payload`, `read_source`, `other_changes`, `undo_note`.
- `core/resume.py`: `JournalState.undone`, named in the resume note.
- `core/errors.py`: `UndoError`.
- `cli/app.py`: `foundry undo` (`cmd_undo`), `/undo` in the REPL
  (`_undo_last`), a resume notice when files were undone.
- `tests/test_undo.py` (new, 18 tests).
- Docs: D-036; design.md section 9 (the `undo` type, the mechanism);
  threat-model row and V2 line; roadmap; README command table.

## Decisions and open points

- Exact pre-images as artifacts, not a shadow git repository: a snapshot of
  the workspace holds the user's changes too, and restoring it would discard
  them. Content holding a known credential is not kept at all, because a
  redacted copy written back would corrupt the file.
- Exit codes for `foundry undo`: 0 when everything planned was undone (or there
  was nothing), 1 when some of it could not be or the undo was refused or
  declined, 2 when it would need to ask and stdin is not a terminal.
- Deferred: redo; undoing back to a chosen patch; removing directories a patch
  created.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q
  tests`: 1635 passed (1617 before, plus 18 in `tests/test_undo.py`).
- Mutation check (scratch script): 20 mutants across the undo guards (edited
  since, at plan and at apply; changed between; not kept; artifact missing or
  damaged; move link; containment; undone once; `--last`; credential kept;
  journaled; evidence reset; read again; resume note; REPL note and prompt;
  nobody to ask; dry run; move digest) -- all caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/checkpoint-undo`, from main at `bfea681`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/errors.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| M | `src/foundry/core/tools/patch.py` |
| A | `src/foundry/core/undo.py` |
| A | `tests/test_undo.py` |
<!-- auto:end -->
