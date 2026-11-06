# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A message of several lines at the prompt (D-052), item 13 -- the first gap
after the codex-parity list. Pasting a stack trace or a block of code used
to send each line as its own task. Now a paste, which arrives faster than
anyone types, is read as one message, and lines typed between two lines of
three double quotes are one message too (Ctrl+C inside discards them).

## Changes

- `cli/repl.py`: `read_message` and `input_waiting` (`msvcrt.kbhit()` on
  Windows, `select` elsewhere, 30 ms between lines; only for a terminal);
  the REPL reads messages, skips blank ones, and `/help` says how.
- `tests/test_multiline.py` (new, 13 tests), including a real paste: a
  child with a console of its own puts three lines into its input buffer as
  key events and reads one message.
- Docs: D-052; design.md (the cli line); README; roadmap -- the list is
  done, and item 13 opens what comes after it.

## Decisions and open points

- No trailing-backslash continuation: a Windows path ends in one.
- No `prompt_toolkit`: a dependency the target machines may not install;
  the console already gives line editing and history.
- A pasted docstring opens no block: three quotes start one only when typed.
- Deferred: a key for a new line in a typed message, and editing an earlier
  message.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2139 passed, 1 skipped (2126 before, plus 13 in
  `tests/test_multiline.py`).
- Probed in a real console before the change: after a three-line paste,
  `input()` returns the first line and `msvcrt.kbhit()` is true; after a
  typed line and Enter it is false.
- Mutation check (scratch script): 13 mutants -- the terminal check,
  asking the console, giving up after the gap, a paste opening no block, the
  closing line, Ctrl+C, asking again after each line, trimming the ends,
  saying so, the single line, skipping a blank message, the REPL reading
  messages, and the help. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/multiline`, from main at `1fb8df1`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_multiline.py` |
<!-- auto:end -->
