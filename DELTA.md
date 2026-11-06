# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/mode` and `/model` (D-047), item 9 of the codex-parity order: the mode and
the model can be chosen again without leaving the session. `/mode NAME`
switches among the four modes as `/approve` already did out of plan mode;
`/model NAME` sends the following requests to another model behind the same
endpoint. Each change is journaled, told to the model with the next message,
kept by `/new`, and named (not carried) by a resume.

## Changes

- `cli/repl.py`: `set_mode`, `set_model`, `tell_model`; the `/mode [NAME]`
  and `/model [NAME]` commands.
- `core/session.py`: `model_change` records, synced like `mode_change`.
- `core/resume.py`: `JournalState.switched_model`.
- `cli/app.py`: `Wiring.chosen`; `/new` builds the next session with it; a
  resume says which model the session had been switched to.
- `tests/test_mode_model.py` (new, 25 tests).
- Docs: D-047; design.md (the policy's mode and the model); a threat-model
  row; README; roadmap (item 9 struck, a new item 10).

## Decisions and open points

- `/model` does not rebuild the system prompt: Anthropic's newer models bind
  each thinking block to the conversation before it, and a rewritten prefix
  gets a 400 on an enforced account. A model switch alone is not such an
  edit.
- Found while doing this: `/approve` and `/mode` rebuild the permissions
  paragraph, and masking and compaction rewrite earlier messages, so the
  Anthropic path can hit that 400 today. It is roadmap item 10, next.
- Another endpoint or protocol is another session (`--profile`); no model
  picker, since the three protocols share no listing call.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1972 passed (1947 before, plus 25 in `tests/test_mode_model.py`).
- Mutation check (scratch script): 25 mutants -- either spelling and
  any case, an unchanged mode or model, the policy, the prompt rebuilt, both
  records, `chosen`, both lines to the model, both notices, notes that
  accumulate, a whole-name match, `runtime.model` and the config's model,
  both commands in the table, the sync, the resume's reading and both of its
  conditions, and `/new` keeping the choice. All are caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mode-model`, from main at `9fe4afc`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_mode_model.py` |
<!-- auto:end -->
