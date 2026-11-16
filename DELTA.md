# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

CI went red on main after D-199 landed: the Foundry suite failed in both
jobs that run it, though it passes here, whole, in a fresh clone checked out
as CI's is, with CI's command. A job's log opens only to someone signed in
to GitHub, which an agent here is not, so the failure could not be read.
This makes CI name each failed test as an annotation, which the public API
returns to anyone, and closes a race in the relay that a slow machine can
lose -- the one candidate found by reading D-199's tests.

## Changes

- `scripts/ci_annotate.py` (new): each failed or erroring test of a JUnit
  report as one `::error` line, ten at most, escaped for a workflow command.
- `.github/workflows/ci.yml`: the `test` and `run-from-clone` jobs write a
  JUnit report and, when the suite fails, run the script on it.
- `verify/relay.py`: a request's meta is written first and the request
  whole, through a staged file: whoever waits for `N.request.json` (the
  player, and one of the kit's tests) could read it empty, or before its
  meta existed.
- Tests: `tests/test_ci_annotate.py` (5, new); `tests/test_verify_kit.py`,
  one more.
- `AGENT.md`: the script, and where CI's failures can be read.
  `docs/roadmap.md`: the suite's size.

## Decisions and open points

- No decision entry: this mends D-199's own landing.
- Open: whether the relay's race is what failed in CI. If CI is still red,
  the annotations of the run say what is.

## Verification

- Full suite as CI runs it (`python -X warn_default_encoding -m pytest
  tests -q --junitxml=...`): 5,128 passed, 1 skipped, of 5,129 collected.
- Mutation check (`verify/mutate.py`): 13 mutations of the relay, the
  script and the workflow, each text found exactly once first; all 13
  killed by the new tests.
- Before this change, D-199's main was run whole in a fresh clone checked
  out with `core.autocrlf=true`, with CI's command and environment
  variables, and under load: green each time. The CI failure is not
  reproduced here; the next run's annotations say whether it is gone.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/ci-fix-annotations`, from main at `6f74e7e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `.github/workflows/ci.yml` |
| M | `AGENT.md` |
| M | `docs/roadmap.md` |
| A | `scripts/ci_annotate.py` |
| A | `tests/test_ci_annotate.py` |
| M | `tests/test_verify_kit.py` |
| M | `verify/relay.py` |
<!-- auto:end -->
