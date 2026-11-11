# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A compaction that is no smaller is not "down to" (D-184): against the real
endpoint, `/compact` of a short conversation printed "about 1,767 tokens
down to 1,782".

## Changes

- `cli/render.py`: the `Compacted` line says "down to" only when smaller.
- Tests: `tests/test_compact_wording.py`.
- Docs: D-184; design (compaction).

## Decisions and open points

- Found by the P1 rerun against Claude (s8).

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (2, all killed): "down to" always; equal taken as smaller.
- Full suite as CI runs it: 4903 passed, 1 skipped (22 min 47 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
