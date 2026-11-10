# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The re-plan after P6-P8 (D-166): P9, a third round with the model played
offline, over what landed since the second, and what it found. Docs only;
the fixes are D-167 and D-168.

## Changes

- Docs: D-166; roadmap (P9, the status table).

## Decisions and open points

- What is left waits on others: P8's lock on a download, P4 on the
  corporate side, P1's rerun and P5 on a model endpoint (tried again: the
  gateway's upstream still refuses).
- The write fence (D-165) is a V2 decision, and the user's.

## Verification

- Docs only: the full suite on main before it (4860 passed, 1 skipped), and
  `delta.py check`.
- The round: four scenarios, every task completed, the suite green after
  each, the credential in no file and no request.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/replan-p9`, from main at `a83b731`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
<!-- auto:end -->
