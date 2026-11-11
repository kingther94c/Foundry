# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`update_goal` asks for a report only of a goal with a budget (D-175, P10),
as its description says; the user is already told every goal's usage as it
completes.

## Changes

- `core/goal.py`: `view` adds `report` to a completed goal's answer only
  when it has a token budget.
- Tests: `tests/test_goal.py` (a goal without a budget, completed, carries
  no report and the user is told its usage; the view of one; the budgeted
  ones keep theirs).
- Docs: D-175; design (goal tools).

## Decisions and open points

- Found by the fourth round (s15): the model was asked to report the tokens
  of a goal without a budget, after Foundry had shown them.

## Verification

- The new and changed tests pass; with the old view they fail.
- Mutations (3, all killed): the report whatever the budget; no report even
  with a budget; the report on any view of a budgeted goal.
- Full suite as CI runs it: 4879 passed, 1 skipped (21 min 23 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
