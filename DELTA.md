# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/prompts:NAME` calls a custom prompt, as codex's (D-112), item 73: where
it went to the model as a task, as typed; and it reaches a prompt a
built-in command's name hides.

## Changes

- `core/commands.py`: a command's name may be two words joined by a colon;
  `/prompts:` alone is a command; `PROMPTS`.
- `cli/repl.py`: `_named_prompt`, `_send_prompt`; `/prompts` told codex's
  way; `/help` lists every custom prompt, a hidden one by its namespace.
- `tests/test_prompts_namespace.py` (new, 27 tests).
- Docs: D-112; README; design; threat model; roadmap item 73.

## Decisions and open points

- The namespace finds only custom prompts; `/NAME` is still the built-in's.
- Another namespace is an unknown command; `/note: x` (a space after the
  colon) is a task, as before.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3642 passed, 1 skipped (3615 before, plus 27 in
  `tests/test_prompts_namespace.py`).
- Mutation check (scratch script): 16 mutants -- no namespace, a name
  after the colon not a word, colons after it, the namespace alone a task or
  case-sensitive, not dispatched, kept in the name, the prompt not sent, no
  prompts, no name or where they go unsaid, `/prompts` unknown, `/help`
  hiding, always using or leaving out the namespace. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-73`, from main at `a328692`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/commands.py` |
| A | `tests/test_prompts_namespace.py` |
<!-- auto:end -->
