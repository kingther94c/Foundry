# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What P9 to P11 against Claude turned up (D-186): a resume after any finished
task said a call had no recorded result (the finish itself), and a real model
cited event ids in a summary the user reads.

## Changes

- `cli/wiring.py`: the resume and fork notice leaves an accepted finish out of
  the count, and the clause out when it is zero.
- `core/tools/finish.py`: the description says the summary is the user's.
- Tests: `tests/test_resume.py`, `tests/test_subsystem_audit.py`.
- Docs: D-186; design (termination, resume).

## Decisions and open points

- Found by rerunning P9 to P11 against Claude (s15, s17, s20, s22).

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (4, all killed): finish counted again; nothing counted; zero said
  again; the summary note dropped.
- Full suite as CI runs it: 4928 passed, 1 skipped (21 min 18 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
