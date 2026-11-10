# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

CI failed from D-165 on; the cause is almost certainly the sandbox probe's
tests, which measure the machine, not Foundry (D-169). They are skipped,
saying why, on a CI runner and where no process starts under the probe's
tokens.

## Changes

- `tests/test_sandbox_probe.py`: `_why_not_here`, and the `probe` fixture
  skipping on it; three tests of it.
- Docs: D-169; research/windows-sandbox.md (its tests).

## Decisions and open points

- The CI log was not read (it needs a signed-in account); the next CI run
  confirms the cause or does not.

## Verification

- `tests/test_sandbox_probe.py`: 9 passed here; with `CI=true`, 3 passed and
  6 skipped, saying why.
- Mutation check: 3 mutants -- a CI runner measured, a process that does not
  start measured, every machine skipped -- all killed.
- Full suite: 4868 passed, 1 skipped (20 min 43 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/probe-where-measured`, from main at `302f94d`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/research/windows-sandbox.md` |
| M | `tests/test_sandbox_probe.py` |
<!-- auto:end -->
