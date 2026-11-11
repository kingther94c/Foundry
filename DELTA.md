# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A resume names the model the session last ran on (D-178, P11): resumed on
the model `/model` had switched it to, a session was warned it "ran on" the
one it started with.

## Changes

- `cli/wiring.py`: `_announce_found` compares the profile and the model the
  session last ran on.
- Tests: `tests/test_mode_model.py` (no warning on the switched model; a
  warning on another model, and on another profile naming the last model).
- Docs: D-178; design (resume).

## Decisions and open points

- Found by the fifth round (s20): `/model relay-model-2`, `/new`, `/resume`
  said "this session ran on personal (relay-model); it continues on personal
  (relay-model-2)".

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (3, all killed): the model it started on, as before; never
  warned; the last model not named (killed once a test changed the profile).
- Full suite as CI runs it, with D-179 on top: 4889 passed, 1 skipped
  (20 min 55 s), before the docs; with them, the docs' own tests pass.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
