# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/export`, as codex's (D-124), item 85: the whole conversation as Markdown,
read from the journal -- on the clipboard, or in a new file at PATH.

## Changes

- `cli/exporting.py` (new): `conversation_markdown` (codex's sections, from
  the journal's records) and the command (clipboard, or a file opened to be
  created).
- `cli/repl.py`: `/export [PATH]`.
- `tests/test_export.py` (new, 19 tests).
- Docs: D-124; README; design; threat model; roadmap item 85.

## Decisions and open points

- `## Result` (how a task ended) is Foundry's own section; tool calls, `!`
  commands, compactions and rewinds are lines under `## Activity`.
- A hook's message to the model and a compaction's own request are left
  out: the user did not see them.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4210 passed, 1 skipped (4191 before, plus 19 in
  `tests/test_export.py`).
- Mutation check (scratch script): 24 mutants over each record's
  section, what is left out, the layout, the destination and never writing
  over a file, and the typed words after a resume note. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-85`, from main at `6737863`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| A | `src/foundry/cli/exporting.py` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_export.py` |
<!-- auto:end -->
