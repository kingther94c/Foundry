# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`exec --output-schema FILE` (D-051), item 12 and the last of the
codex-parity order: a script asks for the run's result in a shape it chose.
The schema becomes a `result` argument of `finish`, required with
`completed` and checked by Foundry itself against a JSON Schema subset; a
mismatch goes back to the model as an invalid call it can correct. `exec`
prints the result -- scrubbed -- as its only output on stdout, `-o` writes
it, and `--json` carries it in the termination event.

## Changes

- `core/json_schema.py` (new, standard library): `check_schema` refuses any
  keyword outside the subset, naming where; `problems` lists what is wrong
  with a value, by place; `embedded` moves a schema under finish's `result`
  with its refs.
- `core/tools/finish.py`: `result_schema`; `result` in the parameters and the
  description; checked in `validate`.
- `core/runtime.py`: `TurnOutcome.result_json`, through `_finalize` and
  `_terminate` to the termination record and event.
- `core/events.py`: `Termination.result`; the event scrubber now scrubs
  lists and dicts (keys too).
- `cli/app.py`: `--output-schema`, the schema file loaded and checked before
  the run (64 KB at most), the result on stdout and in `-o`, the summary on
  stderr when there is none, and the finish reminder asking for it.
- `tests/test_output_schema.py` (new, 83 tests).
- Docs: D-051; design.md (finish); threat-model.md (a new row); README (the
  exec row); roadmap item 12 -- the list is done.

## Decisions and open points

- Checked by Foundry, not the provider's structured output: the last turn
  here is a tool call, the three protocols spell structured output three
  ways, and what came back would still need checking.
- A result does not vouch for itself: `completed` still needs its claims to
  hold; a downgraded run prints its result and exits 10.
- Deferred: `format`, `patternProperties`, `if`/`then`/`else`, tuple `items`;
  a result in the REPL.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2126 passed, 1 skipped (2043 before, plus 83 in
  `tests/test_output_schema.py`).
- Through `main()` against the fake model server: the result alone on stdout
  and in `-o`; a blocked run leaves stdout empty; `--json`'s termination
  event; a secret in the result scrubbed; bad, oversized and missing schema
  files refused with exit 2.
- Mutation check (scratch script): 55 mutants -- every keyword the
  subset enforces, both depth guards, unsupported keywords and malformed
  schemas refused, refs resolved and moved, definitions hoisted, the result
  required, checked and plain JSON, the result offered only with a schema,
  the description and parameters, the result through finalize, the journal
  and the event, list and dict scrubbing, and each step of `exec`. All are
  caught; one equivalent mutant showed a redundant branch in `_same`, which
  is gone.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/output-schema`, from main at `f82ccc4`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/events.py` |
| A | `src/foundry/core/json_schema.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/finish.py` |
| A | `tests/test_output_schema.py` |
<!-- auto:end -->
