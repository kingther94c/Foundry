# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`@path` in a typed message (D-058), item 19: the file goes with the
message as read_file would show it -- numbered, its first window, cut to a
tool result's size -- and counts as read. Only a file in the workspace, at
most ten, each once; a rule refusing `read_file` for it refuses the mention;
the content is scrubbed. Only typed messages are looked at, never a slash
command's or a repository's prompt.

## Changes

- `cli/repl.py`: `attach_mentions`, `_MENTION`, `MENTION_NOTE`,
  `MAX_MENTIONS`; the REPL passes typed messages through it; `/help` says so.
- `tests/test_mentions.py` (new, 20 tests).
- `tests/test_resume_picker.py`: D-057's resume test started two sessions in
  one second, and ids of one second sort by their random part, so it passed
  by chance; it now gives them ids.
- Docs: D-058; threat model; README (the prompt paragraph); roadmap item 19.

## Decisions and open points

- No completion while typing: the Windows console's line input has no hook
  for it without a third-party library.
- A miss is said only when the name looks meant as a file, so `@property`
  in pasted code passes quietly.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2242 passed, 1 skipped (2222 before, plus 20 in
  `tests/test_mentions.py`).
- Mutation check (scratch script): 17 mutants -- the word boundary,
  quoted spaces, trailing punctuation and which, when a miss is said, a
  folder, once each, the limit, refusing and asking rules, scrubbing, typed
  messages only, an unreadable file, the attached line, and `/help`. All are
  caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mentions`, from main at `490ed56`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_mentions.py` |
| M | `tests/test_resume_picker.py` |
<!-- auto:end -->
