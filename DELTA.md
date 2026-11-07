# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

After a reasoning summary the working line goes back up (D-109), item 70,
saying the summary's bold title -- `Exploring the parser` -- or `working`,
as codex's status line does while the model goes on.

## Changes

- `cli/render.py`: `reasoning_title`; a `Reasoning` event puts the working
  line back up after it is printed.
- `tests/test_reasoning_title.py` (new, 15 tests); `tests/test_working.py`
  no longer counts a summary among what leaves the line down.
- Docs: D-109; README; design; threat model; roadmap item 70.

## Decisions and open points

- The first bold run, at most 80 characters on one line, spaces collapsed;
  stripped of control sequences, shown as text.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3524 passed, 1 skipped (3510 before, plus 15 in
  `tests/test_reasoning_title.py`, less the one case taken out of
  `tests/test_working.py`).
- Mutation check (scratch script): 8 mutants -- a title of any
  length, across lines or only at the start, control sequences in it, its
  spaces kept, no words without one, the title never shown, the line not put
  back. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-70`, from main at `89d03ba`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/render.py` |
| A | `tests/test_reasoning_title.py` |
| M | `tests/test_working.py` |
<!-- auto:end -->
