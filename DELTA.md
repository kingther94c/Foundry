# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What Foundry took from other coding agents (D-192 to D-196), after the
user asked to learn from popular projects rather than build our own: the
prompt's working rules; a patch that breaks a Python file's syntax is
refused, as SWE-agent's edit is; a command failing the same way a third
time is told so; and `apply_patch` reads the envelope shapes real models
sent, so 25 of the 26 patches the probes saw refused now parse.

## Changes

- `prompts/system.md`, `core/prompts.py`, `cli/unattended.py`,
  `core/tools/finish.py`: the working rules, what an unattended run is told,
  the reminder after a prose reply, the summary `finish` asks for (D-192).
- `core/tools/patch.py`: an added file's lines without '+' (D-193); the
  Python syntax check (D-194); the envelope readings (D-196).
- `core/runtime.py`: `FailureTracker.failed_again`, and the note (D-195).
- Tests: `test_learned_prompt.py`, `test_patch_syntax.py`,
  `test_same_failure.py`; `test_patch_leniency.py` grows; updates to
  `test_tools_patch.py` and `test_question_reply.py`.
- Docs: D-192 to D-196, an update to D-189; design, roadmap.

## Decisions and open points

- Not taken: a separate raw-content write tool (Claude Code, opencode,
  OpenHands); D-193's reading removes the failure without a second write
  path. Outer retries, a larger round budget with a graceful last turn, and
  an auto-test before `finish` are left until a task shows the need.

## Verification

- Mutation check: 29 mutations of D-192 to D-196, all killed -- two once a
  test was added (the reminder actually sent; operations after the last
  end line).
- Every patch the probes saw refused for its format, replayed: 25 of 26
  now parse.
- The eleven probes against Claude, prompt alone and with everything: all
  completed and passed their hidden checks both times.
- Full suite as CI runs it: 5007 passed, 1 skipped (20 min 26 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
