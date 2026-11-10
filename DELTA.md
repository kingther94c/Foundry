# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The status after D-169: README and the roadmap's test count.
Docs only.

## Changes

- README (the status: D-146 to D-169, 4,868 tests); roadmap (the test
  count).

## Decisions and open points

- None.

## Verification

- Docs only: the full suite on D-169 (4868 passed, 1 skipped); CI
  green on it, and `delta.py check`.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/status-d169`, from main at `178ad7e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/roadmap.md` |
<!-- auto:end -->
