# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/copy`, as codex's (D-123), item 84: the model's latest answer -- or, with
`/copy N`, one of its code blocks -- on the clipboard, as it was shown.

## Changes

- `cli/copying.py` (new): `latest_answer` (a `finish`'s summary, or the
  latest message), `as_shown`, `copy_text` (Windows PowerShell, the text as
  base64 on its input) and the command.
- `cli/markdown.py`: `code_blocks`, finding fences as the renderer does
  (`_closes`, now shared).
- `cli/repl.py`: `/copy [N]`.
- `tests/test_copy.py` (new, 42 tests).
- Docs: D-123; README; design; threat model; roadmap item 84.

## Decisions and open points

- Code blocks only: codex's picker also offers a blockquote, and a status's
  fields after `/status`.
- Windows only, as `/image`'s clipboard read is.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4191 passed, 1 skipped (4149 before, plus 42 in
  `tests/test_copy.py`).
- Mutation check (scratch script): 28 mutants over which answer, what
  is stripped, the choice of block, what is said, how PowerShell is started,
  and the fences. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-84`, from main at `3ff9cff`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| A | `src/foundry/cli/copying.py` |
| M | `src/foundry/cli/markdown.py` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_copy.py` |
<!-- auto:end -->
