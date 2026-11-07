# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

codex's instruction settings (D-142), item 103: `developer_instructions`,
told to the model after Foundry's own instructions, and `compact_prompt` (or
`experimental_compact_prompt_file`), what a compaction asks for. codex's
replacements for the built-in instructions are refused with the reason.

## Changes

- `core/config.py`: the three `[runtime]` settings, the user's only; the
  file read and checked when the configuration is; `model_instructions_file`,
  `instructions` and `experimental_instructions_file` told why not.
- `core/context.py`: the developer instructions in the system message,
  between Foundry's instructions and the notes.
- `core/runtime.py`: `compact_prompt`; `cli/app.py` passes both, and
  `/status` lists the developer instructions; `cli/agents.py` gives a
  sub-agent the same.
- `tests/test_instruction_settings.py` (new, 22 tests).
- Docs: D-142; README; design; threat model; roadmap item 103.

## Decisions and open points

- The built-in instructions are not replaceable: they carry the rules the
  runtime checks a finish against.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4550 passed, 1 skipped (4528 before, plus 22 in
  `tests/test_instruction_settings.py`).
- Mutation check (scratch script): 18 mutants. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-103`, from main at `bc4f20c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/agents.py` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_instruction_settings.py` |
<!-- auto:end -->
