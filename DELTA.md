# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The re-plan after P3 (D-162): P3 marked done, and three phases that need no
one else -- session retention (P6), a restricted-token sandbox researched
(P7), and the wheelhouse's hash-pinning tooling (P8). Docs only.

## Changes

- Docs: D-162; roadmap (the status table and test count, P3 done, P4 without
  session retention, P6 to P8).

## Decisions and open points

- Session retention leaves P4: it needs nothing from the corporate side.
- P8's lock waits on the user allowing a download from PyPI; its tooling
  does not.

## Verification

- Docs only: the full suite on main before it, and `delta.py check`.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/replan-p6-p8`, from main at `3f83b7a`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
<!-- auto:end -->
