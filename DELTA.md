# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry review` (D-131), item 92, as `codex review`: `--uncommitted`,
`--base BRANCH`, `--commit SHA [--title T]`, or the review's instructions
(`-` reads them from stdin), reviewed headlessly with every edit and command
refused; the review is the run's finish summary.

## Changes

- `cli/repl.py`: `review_task`, the review's prompt for `/review` and
  `foundry review` alike; `/review` uses it.
- `cli/app.py`: `cmd_review` and its parser; `cmd_exec` takes a read-only
  reason for the whole run; `_task_from_stdin` names what it reads;
  `exec_resume` reads `foundry exec review ...` as `foundry review ...`.
- `tests/test_review_command.py` (new, 20 tests);
  `tests/test_completion.py` offers `review`.
- Docs: D-131; README; roadmap item 92; threat-model row.

## Decisions and open points

- The review ends through `finish` (completed, the review as its summary),
  so "completed" still comes only from the finish gate; a review left in
  prose exits partial, as an exec run does.
- `--mode` takes only `dont_ask`, so `--mode accept_edits` is refused rather
  than read as an abbreviation of `--model`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4399 passed, 1 skipped (4379 before, plus 20 in
  `tests/test_review_command.py`).
- Mutation check (scratch script): 31 mutants over the command, the
  prompt and `/review`. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-92`, from main at `dc08eeb`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `tests/test_completion.py` |
| A | `tests/test_review_command.py` |
<!-- auto:end -->
