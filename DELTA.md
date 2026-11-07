# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`@name` finds the file it names (D-107), item 68: `@app.py` attaches
`src/app.py` when it is the only file git knows by that name, as codex's @
file search finds one; a name several files share says which they are.

## Changes

- `cli/repl.py`: a mention that is no file at its path, and looks meant as
  one, is looked for among `git ls-files --cached --others
  --exclude-standard` (asked once a message); `files_named` matches whole
  names at the end of a path, in any case.
- `tests/test_mention_names.py` (new, 25 tests).
- Docs: D-107; README; design; threat model; roadmap item 68.

## Decisions and open points

- An ignored file is never found by its name alone; its path still
  attaches it (D-058).
- A path that is a file wins; several matches attach none.
- Not done: codex's picker (no list while typing at `input()`), fuzzy
  matching.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3483 passed, 1 skipped (3458 before, plus 25 in
  `tests/test_mention_names.py`).
- Mutation check (scratch script): 24 mutants -- every word looked
  for, a quoted name not, git asked for every name, names never looked for,
  the first of several taken or one called several, every match said, no
  sign of more or more said at five, several then the first, a listed file
  that is gone, a path that is no file, ignored files listed, new or tracked
  files not, git's failure raised, a failed listing read, an empty name
  listed, backslashes or case kept, part of a name taken, a file at the top
  missed, unsorted, six said. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-68`, from main at `86265b7`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_mention_names.py` |
<!-- auto:end -->
