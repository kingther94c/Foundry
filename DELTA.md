# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/goal`, as codex's (D-127), item 88: a goal the session works toward,
turn after turn -- each task that ends starts the next while the goal is
active -- until the model marks it complete or blocked with `update_goal`,
its token budget runs out, a turn cannot go on or turns do nothing, or the
user pauses it (Ctrl+C). A resume has it back, paused.

## Changes

- `core/goal.py` (new): the goal, its statuses, the user's objective and
  budget, what a response spends, the prompts its turns and its budget send.
- `core/tools/goal.py` (new): `get_goal`, `update_goal`, offered once the
  session has a goal; both read-only (`READ_ONLY_TOOLS`).
- `core/runtime.py`: the goal, `change_goal` (a `goal` record and a notice
  each time), `goal_update`, its tokens and time counted, the wrap-up told
  when its budget runs out mid-task.
- `core/session.py`: the `goal` record. `core/resume.py`: the goal back
  from the journal, with what was spent after its last record.
- `cli/goals.py` (new): `/goal` and the turns it starts (`GoalTurns`);
  `cli/repl.py`: the loop goes on with them, `/status` shows the goal;
  `cli/app.py`: a resumed goal restored paused, and said.
- `tests/test_goal.py` (new, 79 tests).
- Docs: D-127; README; design; threat model; roadmap item 88.

## Decisions and open points

- A resumed active goal is paused, where codex's goes on: a resumed
  session spends nothing until the user says so.
- Three goal turns in a row without a tool call block the goal (Foundry's
  own), so a model that only talks cannot spend without end.
- codex's `create_goal` and goals in `exec` are not here.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4336 passed, 1 skipped (4257 before, plus 79 in
  `tests/test_goal.py`).
- Mutation check (scratch script): 49 mutants over the goal (budget,
  objective, a recorded goal, what is spent, the escaped objective, the
  notices), the runtime (time, tools offered, what the model may mark,
  accounting, the wrap-up), a resume, the turns it starts (Ctrl+C, a failed
  or blocked task, a full context, empty and idle turns), `/goal`'s commands
  and the REPL loop. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-88`, from main at `9a82e14`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/goals.py` |
| M | `src/foundry/cli/repl.py` |
| A | `src/foundry/core/goal.py` |
| M | `src/foundry/core/policy/engine.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| A | `src/foundry/core/tools/goal.py` |
| A | `tests/test_goal.py` |
<!-- auto:end -->
