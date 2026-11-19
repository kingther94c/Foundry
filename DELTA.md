# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A check Foundry runs itself before it accepts `finish` (D-213), P13's verify
before finish, at the user's pick. `foundry exec --verify COMMAND`, or
`runtime.verify_command` in the user's own config: when the model finishes as
completed, Foundry runs COMMAND as a recorded command; while it fails the
finish is refused with what it printed and the task goes on, and the third
failure, or one on a budget's last request, ends the task partial. Read
first in aider, OpenHands, SWE-agent, Claude Code, codex, opencode and Cline.

## Changes

- `src/foundry/core/runtime_ending.py`: `_verify_finish`, `verify_problem`,
  the constants; `_finalize` says the check passed, or ends partial.
- `src/foundry/core/runtime.py`: the check at each finish, the per-task count,
  a cancel during it.
- `src/foundry/core/config.py`: `runtime.verify_command`, the user's layers
  only; `src/foundry/cli/app.py` and `cli/wiring.py`: `exec --verify`, a list
  of commands refused, none in a review, the finish tool told.
- `src/foundry/core/tools/finish.py`: the note in its description;
  `src/foundry/core/session.py`: the `verify` record.
- `verify/probes/run_auto.py`: `--verify`, and each run's failed checks.
- Tests: `tests/test_verify_finish.py` (new, 17), `tests/test_verify_kit.py`
  (one).
- Documents: decision log D-213, roadmap, design, README, threat model.

## Decisions and open points

- Three checks, as aider's reflections and OpenHands' critic; at the limit
  partial, not failed: none of the agents read marks such a run failed, and
  `completed` is what Foundry will not say without evidence.
- Only a `completed` finish is checked; plan mode, a review and a sub-agent
  run none. A check after every edit, as aider's, is not done.
- The model is told of the check in the finish tool's description, as
  aider's system prompt tells it of the test command.
- One live run each shows the mechanism on a real model, not a gain. r-long
  met the default of 40 rounds again, with its work done: the next thing to
  look at.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding),
  with the documents written: 5,368 passed, 1 skipped in 22 min 52 s. The suite collects 5,369 tests.
- Mutations of the new lines: 29, each killed (two survived the first pass
  and the tests were made to catch them: a cancel during a check that read
  0, and a timeout whose kill left exit code 0).
- On the gateway (openclaw/foundry, `--verify "python -m pytest -q"`, 58
  requests, plus 2 to see the quota was back): o-money completed in 13
  requests, the check passing at the first finish; r-long's hidden tests
  passed but its 40 rounds ran out first, and it finished partial, so
  nothing was checked.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `af8d089`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/wiring.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/runtime_ending.py` |
| M | `src/foundry/core/session.py` |
| M | `src/foundry/core/tools/finish.py` |
| A | `tests/test_verify_finish.py` |
| M | `tests/test_verify_kit.py` |
| M | `verify/probes/run_auto.py` |
<!-- auto:end -->
