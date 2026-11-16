# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The push of D-200 to D-203 turned two CI jobs red, `run-from-clone` and
`offline-install`: both run `foundry doctor` as a smoke test, on a machine
with no credential, and D-203 made a missing credential fail the check, as
it fails every session. The check stays as D-203 has it; the two steps give
`doctor` a placeholder in `OPENAI_API_KEY`, which goes nowhere, so that it
checks the rest.

## Changes

- `.github/workflows/ci.yml`: the command wrapper's step and the offline
  install's step set `OPENAI_API_KEY` to a placeholder.
- `tests/test_doctor_truth.py`: one more test, which finds every step of
  `ci.yml` that runs `doctor` (two) and holds each to giving it that
  variable.
- `docs/decision-log.md`: D-203's verification says what CI met and what
  changed. `docs/roadmap.md`: the suite's size.

## Decisions and open points

- No decision entry: this mends D-203's own landing.
- `doctor` without a credential still exits 1, as D-203 decided: a session
  could not start there either. CI's annotations for `run-from-clone` read
  "no test report", as its suite never ran; the failing step was the one
  before it.

## Verification

- CI's step reproduced here, with an empty `FOUNDRY_HOME` and no credential
  variable: `foundry.cmd doctor` exits 1 with `credential  none: set
  OPENAI_API_KEY or run foundry login`; with the placeholder it exits 0, and
  the placeholder is not in its output.
- Full suite as CI runs it, with `PYTHONDONTWRITEBYTECODE` unset so that
  bytecode is written as on CI: 5211 passed, 1 skipped, of the 5,212 the
  roadmap names; 20 min 55 s on Windows.
- The new test against `ci.yml` as it was: it fails. 4 mutations of the two
  steps (either one's variable removed, another variable named, `doctor` no
  longer run), each killed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `f6e2273`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `.github/workflows/ci.yml` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `tests/test_doctor_truth.py` |
<!-- auto:end -->
