# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A task whose rounds or calls run out gets one last request, offering only
`finish`, to say where the work stands (D-197), as opencode's step limit
does. It ended at once, with a summary that named only the limit reached.

## Changes

- `core/runtime.py`: `Budget.over_tokens`; `_wind_up`, the last turn's
  message and refusals; `_offered_tools` offers only `finish` in it.
- Tests: `test_last_turn.py`. Tests that relied on a budget ending a task
  at once now give the last turn a reply, or use a token ceiling where what
  they check is the ending itself (`test_client_config.py`,
  `test_mcp_server.py`, `test_prompt_stop_hooks.py`, `test_resume.py`,
  `test_skills_agents_contained.py`, `test_task_end.py`,
  `test_security_round5.py`).
- Docs: D-197; design, roadmap.

## Decisions and open points

- The token ceiling still ends a task at once: a last turn would spend
  past it.
- The model's own status stands in the last turn; it is told "completed
  only if the work is done and checked".

## Verification

- Mutation check: 17 mutations of D-197, all killed.
- Full suite as CI runs it: 5020 passed, 1 skipped (20 min 33 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
