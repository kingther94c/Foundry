# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

An approval is the user's choice of mode, as `/mode`'s is (D-172, P10): a
fork of an approved session was back in plan mode. A fork's note says it
copies the conversation, not the files.

## Changes

- `cli/slash_commands.py`: `/approve` records the mode in `wiring.chosen`.
- `core/resume.py`: the fork note says the files are not copied.
- Tests: `tests/test_plan_approval.py` (the choice, for `y` and `e`; nothing
  chosen when declined; a fork of an approved session, end to end);
  `tests/test_fork.py` (the note).
- Docs: D-172; design (what `/new`, `/fork` and `/resume` keep).

## Decisions and open points

- Found by the fourth round (s16b): `/mode plan`, `/approve`, `y`, `/fork`,
  and the fork asked to plan again.
- A resume from the command line still starts in the configured mode.

## Verification

- Without the change, the three new tests fail; with it, the module passes.
- Mutations (3, all killed): the choice not recorded; plan recorded as the
  choice; the fork note's sentence on files dropped.
- Full suite as CI runs it: 4872 passed, 1 skipped (20 min 36 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
