# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The status after P9 (D-166 to D-168): README and the roadmap's test count.
Docs only.

## Changes

- README (the status: D-146 to D-168, 4,865 tests); roadmap (the test
  count).

## Decisions and open points

- None.

## Verification

- Docs only: the full suite on D-167 and D-168 stacked (4865 passed,
  1 skipped), and `delta.py check`.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/status-p9`, from main at `ee96a2c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/roadmap.md` |
<!-- auto:end -->
