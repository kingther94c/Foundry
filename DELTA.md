# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`run_command`'s description says which commands a rule can allow (D-183):
against the real endpoint, a model's first command used `$env:` and
`2>&1`, and was refused although `python -m pytest*` was allowed.

## Changes

- `tools/command.py`: `PLAIN_COMMANDS` in the description.
- Tests: `tests/test_plain_commands.py` (the description; each clause held
  to the segmenter and the policy; both output streams).
- Docs: D-183; design (the segmenter).

## Decisions and open points

- Found by the P1 rerun against Claude (s3), the first with a real model.
- The segmenter and the policy are unchanged.

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (3, all killed): the sentence left out; `^` not named; the
  lone `&` not named.
- Full suite as CI runs it: 4922 passed, 1 skipped (23 min 35 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
