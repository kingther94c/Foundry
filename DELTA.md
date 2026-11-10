# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The plan after the codex-parity list (D-146): the roadmap's status brought up
to 2026-11-10, the 106 parity items folded into a table, and a new "After
parity" section ordering what is owed next (P0-P5). README and AGENT.md give
the current test count.

## Changes

- `docs/roadmap.md`: status table and summary as of 2026-11-10 (the
  2026-09-01 notes kept beneath it); the parity list as a table of item and
  decision; the "After parity" section.
- `docs/decision-log.md`: D-146.
- `README.md`, `AGENT.md`: status and test count.

## Decisions and open points

- D-146: no more parity items for now; verify against a model first. Until
  an endpoint is available, the model is played by an agent behind a local
  relay that is not part of the repository.

## Verification

- The full suite on main at `59d1ea0`: 4657 passed, 1 skipped (17m57s).
- Docs only; the parity table was generated from the list and checked to
  hold items 1-106, each with its decision.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/after-parity`, from main at `59d1ea0`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `AGENT.md` |
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
<!-- auto:end -->
