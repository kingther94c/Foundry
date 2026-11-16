# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

CI was still red on main after the last fix, and this time its annotations
said why: in both jobs, two tests of `verify/mutate.py` saw a mutation
survive that should have been killed. A mutation as long as the text it
replaces (`a + b` for `a - b`), written in the second the file was made, is
the same size and the same second to Python, which ran the original's cached
bytecode for it. CI's machine is fast enough to write both in one second;
this one never writes bytecode at all (`PYTHONDONTWRITEBYTECODE=1` is set
here), so it could not fail here. Each run of the tests in `mutate.py` now
compiles into a temporary cache of its own.

## Changes

- `verify/mutate.py`: `tests_pass` runs pytest with `PYTHONPYCACHEPREFIX`
  set to a new temporary folder, so no bytecode is reused from one run to
  the next or written into the root.
- `tests/test_verify_kit.py`: one more test, which writes bytecode whatever
  the environment says and keeps the original's time on every write of the
  mutated file, so the second is the same on any machine.
- `AGENT.md`: CI writes bytecode, and how to run the suite as it does.
  `docs/roadmap.md`: the suite's size.

## Decisions and open points

- No decision entry: this mends D-199's own landing, as the last fix did.
- The relay's staged write, landed with the last fix, stays: it closed a
  real race, though not the one CI met.

## Verification

- Full suite as CI runs it, with `PYTHONDONTWRITEBYTECODE` unset so that
  bytecode is written as on CI (`python -X warn_default_encoding -m pytest
  tests -q --junitxml=...`): 5129 passed, 1 skipped, of the 5,130 the
  roadmap names; 20 min 56 s on Windows.
- The new test against `mutate.py` as it was: both mutations reported as
  surviving, the test failing; with the change it passes. CI's two failing
  tests, run here with `PYTHONDONTWRITEBYTECODE` unset against the old
  `mutate.py`: one of the two failed, as the timing fell; with the change
  both pass.
- Mutation check of the change itself: the run's own cache not used, and
  one cache inside the root shared by every run -- both killed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/ci-fix-bytecode`, from main at `d97460c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `AGENT.md` |
| M | `docs/roadmap.md` |
| M | `tests/test_verify_kit.py` |
| M | `verify/mutate.py` |
<!-- auto:end -->
