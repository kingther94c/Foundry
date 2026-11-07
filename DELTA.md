# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Model-request hooks, as codex's (D-122), item 83: PreModelRequest and
PostModelRequest told of each try of a model request -- retries and a
compaction's included -- with its status, error and token usage. Foundry
now runs every codex hook event but the subagent ones.

## Changes

- `core/hooks.py`: the two events, matched by nothing; `read_model_request`
  (stdout not read; stderr shown; a failure said).
- `core/runtime.py`: `_sample_once` runs the hooks around each try
  (`_attempt` is the try itself); `_model_hooks` with codex's fields;
  `_retrying` tells a backend's own retry as a try.
- `tests/test_model_hooks.py` (new, 15 tests); `tests/test_hooks.py`:
  the events named.
- Docs: D-122; README; design; threat model; roadmap item 83.

## Decisions and open points

- `reasoning_output_tokens` is 0: Foundry does not count reasoning apart.
- A backend's retry is told as it is announced, before its wait.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4149 passed, 1 skipped (4134 before, plus 15 in
  `tests/test_model_hooks.py`).
- Mutation check (scratch script): 18 mutants over the events, the
  reader, each try's before and after (completed, failed, cancelled), the
  fields told, and the retries. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-83`, from main at `8f5e87b`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_hooks.py` |
| A | `tests/test_model_hooks.py` |
<!-- auto:end -->
