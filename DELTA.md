# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Named placeholders in custom prompts (D-071), item 32: `$FILE` in a prompt
is filled from `FILE=src/x.py` on its command line, as codex's prompts take
them. A named value not given stops the prompt and says which.

## Changes

- `core/commands.py`: `named_placeholders()`, `usage_hint()`; `expand()`
  fills `$NAME` and raises `PromptError` for a missing one.
- `core/errors.py`: `PromptError` (category `prompt`).
- `cli/repl.py`: a prompt missing a value sends nothing and says so; `/help`
  shows the named values.
- `tests/test_named_placeholders.py` (new, 23 tests).
- Docs: D-071; README; roadmap item 32.

## Decisions and open points

- A named placeholder is a whole capitalised word: `$PSVersionTable` and
  `$env:PATH` are not; `KEY=value` is named only when the prompt uses KEY.
- An existing prompt with a literal `$HOME` now asks for `HOME=...`; the
  message says `$$HOME` writes a literal one.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2496 passed, 1 skipped (2473 before, plus 23 in
  `tests/test_named_placeholders.py`).
- Mutation check (scratch script): 10 mutants -- a whole word, capitals
  only, each name once, the hint and the prompt's own hint, any KEY=value
  named, holes sent, the first value winning, sent anyway, and /help without
  the names. All are caught; one more, reading a missing value as empty, is
  equivalent (a missing one has already stopped the prompt).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/named-placeholders`, from main at `704a404`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/commands.py` |
| M | `src/foundry/core/errors.py` |
| A | `tests/test_named_placeholders.py` |
<!-- auto:end -->
