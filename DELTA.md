# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A file the model cites opens in the editor (D-106), item 67: on a terminal,
`src/app.py:42` in inline code is a hyperlink to that file at that line, as
codex's `file_opener` makes one -- VS Code by default, or the editor
`runtime.file_opener` names, `none` for no links.

## Changes

- `cli/file_links.py` (new): a citation's link, only for an existing file
  inside the workspace, built from the resolved file.
- `cli/markdown.py`: inline code is linked when the linker says where;
  `cli/render.py`: the renderer's linker, from its workspace and opener;
  `cli/app.py`: the session's passed in.
- `core/config.py`: `runtime.file_opener` (codex's five values); `-c
  file_opener=` takes codex's name.
- `prompts/system.md`: the model is asked to cite files in backticks.
- `tests/test_file_links.py` (new, 50 tests).
- Docs: D-106; README; design; threat model; roadmap item 67.

## Decisions and open points

- The target is never taken from the text: the editor's scheme, the
  resolved file's quoted path, the line. A Markdown link stays text (D-070).
- A terminal rich takes for the legacy console gets no hyperlinks.
- Not done: citations outside backticks, paths with spaces, `#L42`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3458 passed, 1 skipped (3408 before, plus 50 in
  `tests/test_file_links.py`).
- Mutation check (scratch script): 34 mutants -- `none` still linking,
  a citation of any length, a prefix taken for it, the workspace unresolved,
  a file outside it, a directory or a missing file linked, the outside case
  raising, the target unquoted or its drive's colon quoted, no slash before a
  drive, no line, column or range, no drive or a drive-relative path, always
  vscode, nothing linked, every span styled, a link losing its color,
  headings, quotes or lines unlinked, the renderer linking nothing or without
  a workspace, its opener ignored, the session's opener or workspace not
  passed, any opener taken, the setting unset, unrecorded or unknown, off by
  default, the model not told. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-67`, from main at `850ed74`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/file_links.py` |
| M | `src/foundry/cli/markdown.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/prompts/system.md` |
| A | `tests/test_file_links.py` |
<!-- auto:end -->
