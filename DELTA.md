# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A task's budget is 100 rounds and 500 tool calls (D-214), up from 40 and
200, at the user's pick of the round limit D-213 named. On the gateway,
r-long -- seven steps -- has finished in 34, 38 and 41 rounds and twice ran
out of 40 before it was done; every other probe takes 5 to 13. codex, Claude
Code, opencode and Gemini CLI set no such limit by default, OpenHands 500
iterations and goose 1,000 turns; SWE-agent stops at US$3 instead.

## Changes

- `src/foundry/core/runtime.py` (`Budget`) and `src/foundry/core/config.py`:
  the two defaults.
- `verify/probes/run_auto.py`: `--rounds N`; with none, Foundry's default
  (the runner wrote 40 itself before); each run says how many rounds it
  took; the run's config written by `home_config`.
- Tests: `tests/test_task_budget.py` (new, 6), `tests/test_verify_kit.py`
  (one), `tests/test_project_doc_settings.py` (the default it read).
- Documents: decision log D-214, roadmap, design, README (the budget and
  what happens when it runs out).

## Decisions and open points

- Still a limit, not none: Foundry has no default ceiling on a task's
  tokens or cost, so rounds and calls are what bound an unattended run and
  a Stop hook that keeps sending a task on.
- Five calls to a round, as the two limits have always had.
- Not here: telling the model its budget as it goes (goose, codex's
  rollout_budget); a token or cost ceiling the user sets.
- Seen in the runs, not changed: after a compaction under r-long's
  12,000-token limit the model reads back more than D-209's half-threshold
  of room, so the next compaction is due at once (eight in one run).

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding),
  with the documents written: 5,375 passed, 1 skipped in 22 min 33 s. The suite collects 5,376 tests.
- Mutations of the new lines: 13, each killed.
- On the gateway (openclaw/foundry, r-long with --rounds 100, two runs, 92
  requests, plus 2 to see the gateway answered): both completed and passed
  their hidden tests, in 38 and 41 rounds (46 requests each; eight and five
  compactions).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `a70fdda`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_project_doc_settings.py` |
| A | `tests/test_task_budget.py` |
| M | `tests/test_verify_kit.py` |
| M | `verify/probes/run_auto.py` |
<!-- auto:end -->
