# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

CI's `researchos` job failed on the push of a1fbaeb, the first CI run since
603db4f. A hidden acceptance test from v28 ran a subprocess with `text=True`
and no encoding, which `-X warn_default_encoding` together with the
suite's `filterwarnings = error::EncodingWarning` turns into four
failures. The test now passes `encoding="utf-8"`; no code changes.

## Changes

- `ResearchOS/tests/test_forecast_score_acceptance.py`:
  `test_each_module_imports_first` passes `encoding="utf-8"` to
  `subprocess.run`, as `tests/test_forecast_score.py` already does.

## Decisions and open points

## Verification

- With CI's commands (`python -X warn_default_encoding -m pytest ...`):
  ResearchOS 2263 passed (4 failed before the fix), Foundry `tests` 1589
  passed, tools 813 passed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/refactoring-plan-foundry-macro-690951`, from main at `a1fbaeb`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `ResearchOS/tests/test_forecast_score_acceptance.py` |
<!-- auto:end -->
