# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Session resume (D-035): `foundry resume` and `foundry exec --resume` rebuild a
session's conversation from its journal and continue it, without weakening the
evidence chain, the dirty-file guard, redaction or HEAD attribution. Also fixes
a gap in the evidence chain found on the way: a partly applied patch did not
count as a change.

## Changes

- runtime: a patch that landed some files and rejected others moves the
  mutation mark (`_last_mutation`), so earlier commands cannot vouch for it.

## Decisions and open points

## Verification

- `python -m pytest -q tests/test_subsystem_audit.py`: the new partial-patch
  test fails without the fix and passes with it.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/session-resume`, from main at `beb771e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_subsystem_audit.py` |
<!-- auto:end -->
