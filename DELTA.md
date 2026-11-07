# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`review_model` (D-143), item 104, as codex's: the model `/review`'s task and
`foundry review` run on; the session goes back to its own after a `/review`.

## Changes

- `core/config.py`: `backend.review_model`, a model's name.
- `cli/repl.py`: a task may name its model; `/review`'s is the review
  model, switched to and back, each switch journaled; `/status` says it.
- `cli/app.py`: `build(review=)`; foundry review runs on it unless `-m`
  names one.
- `tests/test_review_model.py` (new, 15 tests).
- Docs: D-143; README; roadmap item 104.

## Decisions and open points

- The review model is behind the session's endpoint, as `/model`'s is.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4565 passed, 1 skipped (4550 before, plus 15 in
  `tests/test_review_model.py`).
- Mutation check (scratch script): 10 mutants. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-104`, from main at `cb26c52`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| A | `tests/test_review_model.py` |
<!-- auto:end -->
