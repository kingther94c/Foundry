# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The checkup's task level (D-206), the last of the five: after the protocol
level, a session is given a small task as `foundry exec` runs one -- a sample
module with an off-by-one and the tests that find it, in a repository the
checkup makes and removes -- with a Foundry folder of its own and leave to run
`python -m unittest`, in at most eight rounds. The checkup then runs the tests
itself and gives one line: fixed and said so, or `TSK0`, `TSKE`, `TSKP`,
`TSKX`, `TSKT`, `TSKS`. `--no-task` leaves it out.

## Changes

- `src/foundry/core/taskcheck.py` (new): the sample, the session's command
  line and configuration, its events read, the tests run, the verdict.
- `src/foundry/cli/checkup.py`: the task level after the protocol's, skipped
  when that failed, when git does not run, or with `--no-task`; the session's
  environment (its own Foundry folder, the credential in its variable, this
  Foundry and this Python first); its events kept beside the report; a line
  while it runs. `src/foundry/cli/app.py`: `--no-task`.
- `tests/fakegateway.py`: a request that offers `finish` is answered as a
  model doing the task -- read, patch, tests, finish -- or doing it badly;
  calls carry their own id and name.
- `tests/test_checkup.py`: 16 new tests; elsewhere the task level stands in;
  the assertions that a level was skipped name the level.
- Documents: decision log D-206; README (the first run, the command table);
  requirements; design's code map; roadmap (status, P15, the suite's size).

## Decisions and open points

- The verdict is the checkup's own test run and an unchanged test file, not
  what the session claimed; a run whose fix works but that did not end
  completed is a warning.
- The session's Foundry folder is a temporary one with a copy of the user's
  `config.toml` and one rule; the credential goes to it in its variable, in
  memory, never in a file.
- It runs by default, being one of the five levels M3's gate asks for; it
  costs a few rounds of the model and a minute or two.
- D-205's "P all not checked" assertions matched "SKIP all not checked" for
  any level; they now name the level.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding):
  5,322 passed, 1 skipped (5,323 collected) in 21 min 23 s.
- The new tests cannot import the code before (no `taskcheck`).
- 34 mutations of the new lines, each killed at the first run.
- A run by hand against the fake gateway first failed with "you must read
  this file before editing it": the scripted model now reads the module
  before patching it, as a real one must.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `bfe8212`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/checkup.py` |
| A | `src/foundry/core/taskcheck.py` |
| M | `tests/fakegateway.py` |
| M | `tests/test_checkup.py` |
<!-- auto:end -->
