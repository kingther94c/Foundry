# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A read-only run is told only of the tools it has (D-173, P10): a `foundry
review`'s system prompt said apply_patch and pytest were allowed without
asking, though it was offered neither.

## Changes

- `core/prompts.py`: `system_prompt`/`permissions_paragraph` take the run's
  registry; a read-only policy is said in the paragraph, and only allow
  rules for tools offered are listed (`read_only_line`, `_offered`).
- `cli/unattended.py`: read-only is set before the paragraph is written,
  with the registry.
- `cli/agents.py`: a read-only sub-agent's paragraph is written for it.
- Tests: `tests/test_review_command.py` (a review is told only of the tools
  it has, and an allowance it can use is still said); `tests/test_subagents.py`
  (the sub-agent's system prompt is the session's with the read-only line;
  only the allowances it can use; a paragraph that does not know the tools
  claims none); `tests/test_instruction_settings.py` (a sub-agent's system
  message is the session's with the read-only line, the developer
  instructions still in it).
- Docs: D-173; design (read-only tasks).

## Decisions and open points

- Found by the fourth round (s17): the review's model was told "Also allowed
  without asking: apply_patch (*), run_command (python -m pytest*)".
- `/review`, `/side` and `/recap` keep the conversation's system prompt, so
  the reasoning bound to it is not dropped; their message says read-only.

## Verification

- The new and changed tests pass; without the change they cannot import
  `read_only_line`, and the mutations show each part is needed.
- Mutations (8, all killed): read-only set after the prompt; the registry
  not passed (killed once the review test also allowed `read_file`); the
  sub-agent's prompt not rebuilt; every rule offered; every rule when the
  registry is unknown; mutators kept; no rule kept; the read-only line not
  said.
- Full suite as CI runs it: 4874 passed, 1 skipped, 1 failed (21 min 55 s):
  `test_a_sub_agent_is_told_them_too` asserted the sub-agent's system message
  was the session's exactly; it now allows the read-only line and nothing
  else, and its file passes (22). 4875 pass with it.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
