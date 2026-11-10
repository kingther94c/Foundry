# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A read-only task -- a review, `/side`, `/recap`, a read-only sub-agent -- is
offered only the tools it may use: its requests leave out every mutator,
which its policy refuses whatever the arguments (D-148). Found by P1's
stand-in model (D-146). Plan mode is unchanged.

## Changes

- `core/runtime.py`: `_offered_tools()`, the request's tool list, filtered
  by tool kind while `policy.read_only` is set.
- Docs: D-148, design (read-only tasks).

## Decisions and open points

- A mutator called anyway is still refused by the policy, with the task's
  reason; nothing in dispatch changed.
- Plan mode keeps its tools: it plans the changes it will make.

## Verification

- `tests/test_read_only_tools.py` (6): a read-only sub-agent, a writable one,
  a hidden tool called anyway, `/side`, `/review`, and plan mode.
- Mutation check: 4 mutants (never filtered, always filtered, plan mode
  filtered too, the request not using the filter), all killed.
- Full suite, rebased on D-147: 4692 passed, 1 skipped.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/read-only-tools`, from main at `df6b90c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_read_only_tools.py` |
<!-- auto:end -->
