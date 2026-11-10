# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The sessions a session's start removed were said before `exec --json`
listened, so a script never heard of it (D-168, found in P9). They are
journaled as before and said with what a resume found, once the front end
listens.

## Changes

- `cli/wiring.py`: the sweep keeps what it will say; `_announce_resume`
  becomes `_announce_found`, which says it, then what a resume found.
- `cli/session_commands.py`, `cli/unattended.py`: `/new` and `record` say it
  too; the other front ends already called the announcement.
- Tests: `tests/test_retention.py` -- heard on `exec --json` after
  `session_started`, on `/new` and `record`; what is left for next time,
  said and journaled. The tests that imported `_announce_resume` import
  `_announce_found`.
- Docs: D-168; design (the event types).

## Decisions and open points

- None.

## Verification

- `tests/test_retention.py`: 36 passed.
- Mutation check: 7 mutants -- the sweep not said, said twice, what went
  not kept for saying, what was left not said, `/new`, `record` and `exec`
  not announcing -- all killed.
- Full suite: 4865 passed, 1 skipped, stacked on D-167 (20 min 0 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/sweep-notice`, from main at `8c4e0ce`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `src/foundry/cli/session_commands.py` |
| M | `src/foundry/cli/unattended.py` |
| M | `src/foundry/cli/wiring.py` |
| M | `tests/test_goal.py` |
| M | `tests/test_mode_model.py` |
| M | `tests/test_plan.py` |
| M | `tests/test_plan_approval.py` |
| M | `tests/test_resume.py` |
| M | `tests/test_retention.py` |
<!-- auto:end -->
