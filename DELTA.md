# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

P14 (D-207): the macro run scores the snapshots that match, and names the
rest. Its plan already said that a snapshot whose rebuilt `case.json` does
not match `inputs.sha256` is not scored; `forecast_arm.py restore` instead
stopped at the first one, so a single stale snapshot -- 2026-10-30's is
expected to be one if the private copy is lost -- would have kept the other
seventeen from the 2026-12-07 scoring. The plan gains a dated amendment, in
the words the user approved.

## Changes

- `ResearchOS/runs/2026-11-macro-calibration-run/forecast_arm.py`: `restore`
  takes each snapshot on its own, leaves out and names one that does not
  match or was not rebuilt, and never overwrites an arm's case that differs;
  `setup` still takes every snapshot or none.
- `.../secondary.py`: `scoring_sets()`; a snapshot left out in either arm is
  left out of both; `not_scored` in `secondary-<date>.json`, and printed.
- `.../PLAN.md`: "Amendment, 2026-11-18, before the 2026-12-07 scoring".
  `.../RUNBOOK.md`: rebuilding the cases without the private copy.
- `ResearchOS/tests/test_macro_run_scoring.py` (new): six tests.
- Documents: decision log D-207; roadmap (status, P14, what waits on the
  user).

## Decisions and open points

- The rule is the plan's own, unchanged; only the scripts now follow it.
- Nothing is rehearsed before 2026-12-07: `secondary.py` fetches the
  outcomes, so a trial run would be a scoring the plan does not have.
- Still for the user: whether `~/macro-run-inputs.tgz` exists, before
  2026-12-07; with it, all eighteen snapshots are scored.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding):
  5,322 passed, 1 skipped (5,323 collected) in 21 min 55 s.
- ResearchOS suite (without `test_llm.py`, as `anthropic` is not installed
  here; CI runs it): 2,316 passed.
- On the scripts before, five of the six new tests fail; the sixth is
  `setup`'s, which did not change.
- 11 mutations of the new lines, each killed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `abc39d3`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `ResearchOS/runs/2026-11-macro-calibration-run/PLAN.md` |
| M | `ResearchOS/runs/2026-11-macro-calibration-run/RUNBOOK.md` |
| M | `ResearchOS/runs/2026-11-macro-calibration-run/forecast_arm.py` |
| M | `ResearchOS/runs/2026-11-macro-calibration-run/secondary.py` |
| A | `ResearchOS/tests/test_macro_run_scoring.py` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
<!-- auto:end -->
