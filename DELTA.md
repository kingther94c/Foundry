# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/image` alone takes the clipboard's image (D-065), item 26: a screenshot
(Win+Shift+S) goes with the next message without being saved first, as
codex's Ctrl+V pastes one. It is read by a constant Windows PowerShell
script, found by its absolute path, and checked as a file's image is.

## Changes

- `cli/repl.py`: `clipboard_image()`, `attach_clipboard()`; `/image` with no
  path uses them (`-i` still needs one); `_may_attach` shared with `attach`.
- `tests/test_clipboard_image.py` (new, 16 tests);
  `tests/test_images.py`: `/image` alone now reads the clipboard (stubbed).
- Docs: D-065; design (the images bullet); threat model (the images row);
  README; roadmap item 26.

## Decisions and open points

- Windows only; elsewhere it says so.
- The tests never touch the user's clipboard: the live ones (Windows) swap
  the clipboard read for a bitmap PowerShell draws, and run the rest of the
  script as it is. Checked once by hand against this machine's clipboard,
  read only: it held no image, and said so in 0.7 s.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2343 passed, 1 skipped (2327 before, plus 16 in
  `tests/test_clipboard_image.py`).
- Mutation check (scratch script): 14 mutants -- `-Sta`, the absolute
  path, no input, the timeout, no window, empty as none, a failed read,
  strict base64, Windows only, no PowerShell, nothing attached when empty,
  the image's name, `/image` alone reading it, and the help. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/clipboard-image`, from main at `4cf6ce8`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_clipboard_image.py` |
| M | `tests/test_images.py` |
<!-- auto:end -->
