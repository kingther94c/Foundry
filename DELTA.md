# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Plan approval (D-038). In an interactive session in plan mode, `/approve`
shows the plan and asks whether edits should ask first (`default`) or apply
without asking (`accept_edits`). On yes, plan mode ends, the system prompt's
permissions paragraph is built again so the model is told the rules that now
hold, a `mode_change` record is journaled, and the work starts at once. Only
the user can approve; a resume starts in the configured mode again.

## Changes

- `core/prompts.py`: `mode_line(mode)` (the permissions paragraph's own line
  for a mode) and `system_prompt(policy)` (base prompt plus permissions
  paragraph, as a session sends it); plan mode's line says the user approves
  with `/approve`.
- `core/session.py`: `EventType.MODE_CHANGE`, synced on write.
- `core/runtime.py`: each `policy_decision` records the `mode` it was made in.
- `core/resume.py`: `JournalState.mode` (the last `mode_change`'s `to`);
  `resume_note(state, outside, mode)` says when the switch did not carry over.
- `cli/app.py`: `/approve` in the REPL (`_approve_plan`), which runs the next
  turn with the approval note; `_assemble` builds the prompt with
  `system_prompt`; a resume notice when the session had been switched.
- `tests/test_plan_approval.py` (new, 8 tests).
- Docs: D-038 (and D-037's deferred item pointed at it); design.md sections 6
  and 9; requirements.md, the plan mode baseline; threat-model row; README.

## Decisions and open points

- Approving starts the work: the next turn runs with a note saying plan mode
  is over, what the new mode allows, and to carry the plan out. Declining, an
  unknown answer, or no answer at all keeps planning and writes nothing.
- The system prompt is rebuilt rather than contradicted by a note: the
  permissions paragraph exists to tell the model the truth about what will be
  allowed.
- Approval does not carry over a resume, like every other grant (D-035); the
  resumed model is told.
- Not done: consulting where plan mode came from. A managed `mode` is not a
  floor today (a user config overrides it), so there is nothing to defer to;
  D-038 records that `/approve` must refuse if that ever changes.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1680 passed (1672 before, plus 8 in `tests/test_plan_approval.py`).
- Mutation check (scratch script): 14 mutants -- only in plan mode, prompt
  rebuilt, journaled, the edits choice, unknown and missing answers decline,
  the mode switched, the work starting, the resume notice and note (including
  "same mode says nothing"), the decision's mode, resume reading the record,
  the model told how -- all caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/plan-approval`, from main at `14ae502`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/prompts.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_plan_approval.py` |
<!-- auto:end -->
