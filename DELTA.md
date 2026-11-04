# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A visible plan (D-037). A tenth tool, `update_plan {explanation?, plan:
[{step, status}]}`, lets the model keep a plan for its task; the CLI draws it
as a checklist each time it changes, `/plan` shows it again, `exec --json`
streams it as a `plan_updated` event, and a resumed session restores it. The
plan is the model's own account, so it is never evidence: `finish` lists the
steps not marked completed and changes no status over them.

## Changes

- `core/tools/plan.py` (new): `UpdatePlan` (read-only; at most 20 steps of at
  most 200 characters, at most one `in_progress`, an empty plan clears it;
  each step reduced to one line of plain text), `one_line`, `plan_payload`,
  `steps_from_payload` (lenient, for resume), `describe` (the counts the model
  hears back).
- `core/events.py`: `StepStatus`, `PlanStep`, `PlanUpdated` (added to
  `Event`); `EventSink` now scrubs strings nested anywhere in an event, not
  only its top-level fields, and still leaves enums as enums.
- `core/tools/registry.py`: registers `UpdatePlan`.
- `core/policy/engine.py`: `update_plan` joins `READ_ONLY_TOOLS`, so a
  built-in rule allows it in every mode.
- `core/runtime.py`: `AgentRuntime.plan` / `plan_explanation`; an
  `update_plan` result sets them, records `plan` in its `tool_result`, and
  emits `PlanUpdated`; `_finalize` appends the steps not marked completed to
  the summary and warns when such a finish is `completed`.
- `core/resume.py`: `JournalState.plan` / `plan_explanation`, from the newest
  `tool_result` that carries a plan.
- `cli/render.py`: `print_plan` (an escaped checklist); the renderer draws
  `PlanUpdated` and skips `update_plan`'s begin and ok lines.
- `cli/app.py`: `/plan` in the REPL; a resume restores the plan and announces
  it as a `PlanUpdated` with an empty `call_id`.
- `prompts/system.md`, `core/prompts.py`: when to use `update_plan`; the plan
  mode paragraph points at it.
- `tests/test_plan.py` (new, 37 tests).
- Docs: D-037; design.md (the event list and `PlanUpdated`, nested scrubbing,
  the tool, the `plan` field of `tool_result`); requirements.md section 5.2
  (the tenth row); threat-model row; roadmap; README command table.

## Decisions and open points

- The plan never decides a status. Downgrading `completed` while steps are
  open would turn a bookkeeping slip into a failed task, and a model could
  clear its plan first; listing the open steps shows the disagreement instead.
- The plan belongs to the session, not the task: the model's history keeps
  every call it made (masking never touches a call's arguments), and a resumed
  or continued task should keep the plan that history shows.
- The tool schema uses only the keywords `finish` already uses (no
  `maxItems`); the limits are checked in `validate` and stated in the
  description, so a gateway model that rejects less common keywords still
  gets the tool.
- Deferred: approving a plan in plan mode from the session (the permissions
  paragraph is part of the system prompt, which a mode switch would change);
  plan counts in `foundry report`.
- `docs/overview.html` and `demo/` still say nine tools: the first is a dated
  snapshot (it predates resume and undo as well), the second is a separate
  miniature with its own tool list.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1672 passed (1635 before, plus 37 in `tests/test_plan.py`).
- `exec --json` end to end through `main()` against a local fake model: the
  plan arrives as a `plan_updated` line, is not rejected under `dont_ask`, and
  the termination summary lists the open steps.
- Mutation check (scratch script): 27 mutants across the validation rules,
  nested scrubbing, the built-in allow rule, the journal record, the event,
  the finish listing and its warning, resume, `/plan`, the renderer and the
  prompts. 25 caught -- the restore cap only after a test was added for it.
  The two that survive change nothing: scrubbing an enum with `isinstance`
  instead of an exact type returns the same enum (nothing in it to scrub), and
  rich does not read `[>]` as a tag, so its escape is cosmetic (the `[x]`
  escape is needed, and its mutant is caught).
- Not run against a live model: the local gateway answers 404 (see the
  2026-11-04 note on the test endpoint).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/update-plan`, from main at `c2cfb0d`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/events.py` |
| M | `src/foundry/core/policy/engine.py` |
| M | `src/foundry/core/prompts.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| A | `src/foundry/core/tools/plan.py` |
| M | `src/foundry/core/tools/registry.py` |
| M | `src/foundry/prompts/system.md` |
| A | `tests/test_plan.py` |
<!-- auto:end -->
