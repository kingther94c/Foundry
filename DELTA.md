# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The first macro run's forecasts were made in the environment with the API
key (21 commits on main, ea2defd..b261a57). This branch audits that run
against its plan and writes up its results. Primary endpoint: isolated
views disagree (mean correlation 0.045 against 0.654; difference -0.61,
interval -0.66 to -0.55). On the 330 keys resolved by 2026-11-02, neither
arm is distinguishable from a coin.

## Changes

- `ResearchOS/runs/2026-11-macro-calibration-run/README.md` (new): the
  results. It covers how the run went, verified from git and GitHub's
  push record; the primary and secondary endpoints; the look-ahead scan;
  what follows; and the cost.
- `ResearchOS/README.md`: the run's entry gives its result and links the
  README.

## Decisions and open points

- The 2026-10-30 snapshot hashed where the forecasts were made differs
  from both rebuilds here (11-01 and 11-02) in its dashboard and targets,
  for an unknown reason. Every level its forecasts quote agrees with the
  rebuilds. None of its keys had resolved by 11-02. Only the remote's
  private copy holds it exactly.

## Verification

- The remote changed only allowed files: 36 `prediction.json`, two hash
  files, two spend logs, `primary.json` and `secondary-2026-11-02.json`.
  No code changed, and no `prediction.json` was changed after it was
  added.
- GitHub's activity API: the hashes reached main at 12:45:55 UTC, and the
  first API call came at 12:48:08.
- Spend logs: all 396 calls were served by claude-opus-5-5, 11 per
  snapshot, in the planned order, at $13.81 and $14.13.
- 17 of 18 snapshots rebuilt here from series fetched on 11-02 match
  `inputs.sha256`. The first scoring, redone here on those cases and
  series, gives identical Brier tables, 330 resolved and 210 pending in
  both arms.
- The look-ahead text scan found no claim about an event after its
  snapshot's date.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/refactoring-plan-foundry-macro-690951`, from main at `b261a57`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `ResearchOS/README.md` |
| A | `ResearchOS/runs/2026-11-macro-calibration-run/README.md` |
<!-- auto:end -->
