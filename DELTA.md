# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A reasoning model's reasoning, carried across rounds on Responses (D-089),
item 50, as codex carries it: with `store: false`, the reasoning is asked
for encrypted (`include: ["reasoning.encrypted_content"]`) once reasoning is
configured or the model has shown it reasons, kept in the turn's opaque
block, and sent back before that turn's message and calls -- to the same
model only, without item ids.

## Changes

- `core/backends/responses.py`: `PROVIDER`; `_kept_reasoning`,
  `_has_reasoning`, `_opaque`, `_replayed`; `build_body(include_reasoning=)`;
  `ResponsesBackend.reasons`; both paths keep the reasoning, tagged with the
  model the request named.
- `tests/test_responses_reasoning.py` (new, 19 tests).
- Docs: D-089; README; design; roadmap item 50.

## Decisions and open points

- Tagged with the model asked for, not the dated snapshot that answered:
  under the snapshot's name it would never match a request again.
- Not asked for before the model has shown it reasons (unless configured):
  a model that does not reason may refuse the field.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2850 passed, 1 skipped (2831 before, plus 19 in
  `tests/test_responses_reasoning.py`).
- Mutation check (scratch script): 19 mutants -- another model's or
  another provider's reasoning sent, any item sent as reasoning, a damaged
  block crashing, the id sent, reasoning without content kept, never sent
  back, a user's block sent, the model not compared, never or always asked
  for, configured reasoning not asking, a response's or a stream's reasoning
  not noticed or not kept, the terminal output not read, a stream's turn
  without it, tagged with the answering model on either path. All are caught
  (the last after a test was added).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/responses-reasoning`, from main at `d6ece44`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/backends/responses.py` |
| A | `tests/test_responses_reasoning.py` |
<!-- auto:end -->
