# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The terminal title, and `/title` (D-139), item 100, as codex's: an
interactive session says in the title what it is doing -- `Working`, or
`[ ! ] Action Required` while an approval waits -- then the session's name
and the project's.

## Changes

- `cli/title.py` (new): `clean`, `compose`.
- `core/config.py`: `[runtime] terminal_title`, codex's ids and aliases;
  codex's `[tui]` table is told where it lives.
- `cli/render.py`: `Renderer.title`, `clear_title`; `work` and
  `ask_approval` set it.
- `cli/app.py`: an interactive session's renderer gets the items and
  `_title_facts`; `_close` clears the title.
- `cli/repl.py`: the prompt sets it ready; `/title [ITEM...|none]`.
- `tests/test_terminal_title.py` (new, 24 tests).
- Docs: D-139; README; threat model (escape injection); roadmap item 100.

## Decisions and open points

- No spinner: it would need a timer writing to the terminal. Items codex
  has and Foundry has no value for (git branch, rate limits) are refused.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4477 passed, 1 skipped (4453 before, plus 24 in
  `tests/test_terminal_title.py`).
- Mutation check (scratch script): 21 mutants. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-100`, from main at `32a74f6`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/cli/repl.py` |
| A | `src/foundry/cli/title.py` |
| M | `src/foundry/core/config.py` |
| A | `tests/test_terminal_title.py` |
<!-- auto:end -->
