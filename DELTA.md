# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

P5 begins (D-187): ResearchOS v32, `compare --resample cases`, which
resamples whole snapshots, built by Foundry on the real Anthropic API.

## Changes

- `ResearchOS/researchos/compare.py`, `__main__.py`: Foundry's build, as written.
- Tests: `tests/test_compare_resample.py` (Foundry's), and
  `tests/test_resample_cases_acceptance.py` (the hidden suite).
- `ResearchOS/SPEC.md` (the v32 section), `HISTORY.md`.
- The macro run: a dated amendment to `PLAN.md`; `secondary.py` gives the
  comparison both ways; a line in its README.
- Docs: D-187; roadmap (status, P5); the acceptance scorecard.

## Decisions and open points

- The user chose this of three candidates; the others were a weekly
  forward step and a list of when pending keys resolve.

## Verification

- Hidden suite: 49 new tests, 1179 in all, passed by Foundry's build on its
  first run; ResearchOS as a whole, 2324 passed.
- 20,000 fuzzed calls against the hand-written reference: no mismatch.
- Full suite as CI runs it: 4928 passed, 1 skipped (19 min 42 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
