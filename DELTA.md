# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What a patch changed, in view (D-075), item 36: once a patch lands, each
file it changed is shown under it -- `updated src/app.py (+2 -1)` and its
diff -- whether or not anyone was asked first, as codex shows an edit.

## Changes

- `core/tools/patch.py`: `file_diff()`, `bounded_diff()`, `_text_of()`,
  `_header()`; `_Planned.old_text`; `metadata["diff"]` for what landed.
- `core/runtime.py`: `ToolEnd.output` takes the diff when there is no
  command tail.
- `cli/render.py`: `_patch_diff()` -- 40 lines, coloured, all with `-v`.
- `tests/test_patch_diff.py` (new, 18 tests).
- Docs: D-075; design (the `ToolEnd` entry); roadmap item 36; threat model.

## Decisions and open points

- Only files that landed; the model's tool result and the journal are
  unchanged. A line over 1,000 characters is replaced by its length, never
  cut, so the redactor sees each credential whole.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2565 passed, 1 skipped (2547 before, plus 18 in
  `tests/test_patch_diff.py`).
- Mutation check (scratch script): 18 mutants -- file headers kept,
  removed lines not counted, a long line cut, an unreadable file shown as
  empty, unbounded, the rest not counted, a binary deletion decoded anyway,
  nothing shown, a deletion's lines not read, an update shown as all new, CR
  kept in the lines, a move not named, the runtime dropping it, shown as a
  command's tail, never cut, the cut unsaid, added lines not green, control
  sequences kept. All are caught (a first run's survivor, CRLF normalization
  before `splitlines`, was redundant and is gone).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/patch-diff`, from main at `bdedc9c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/patch.py` |
| A | `tests/test_patch_diff.py` |
<!-- auto:end -->
