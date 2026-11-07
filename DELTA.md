# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Sub-agents (D-141), item 102: the model starts one with `spawn_agent`, as
codex's, when the user asks for one; it works in a conversation of its own
under the session's rules, read-only by default, and only its report comes
back.

## Changes

- `core/tools/agent.py` (new): the `spawn_agent` tool.
- `cli/agents.py` (new): `Spawner`, which runs a sub-agent on the session's
  backend, policy, approvals and hooks, journaled under the session's
  `agents/`.
- `core/runtime.py`: a cancel reaches the running sub-agent; its tokens are
  the session's (`spent_by_agent`); a tool result names the sub-agent.
- `core/policy/engine.py`: a built-in rule starts a read-only sub-agent
  unasked.
- `core/config.py`: `[runtime] subagents`; `cli/app.py` registers the tool;
  `cli/repl.py`: `/agents` says how to ask for one.
- `tests/test_subagents.py` (new, 24 tests).
- Docs: D-141; README; design; requirements; threat model; roadmap item 102.

## Decisions and open points

- One sub-agent at a time, run to its end while the session waits; codex's
  `send_input`, `resume_agent`, `wait_agent`, `close_agent` and agent roles
  are not here.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4528 passed, 1 skipped (4504 before, plus 24 in
  `tests/test_subagents.py`).
- Mutation check (scratch script): 30 mutants. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-102`, from main at `66dfa86`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| A | `src/foundry/cli/agents.py` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/policy/engine.py` |
| M | `src/foundry/core/runtime.py` |
| A | `src/foundry/core/tools/agent.py` |
| M | `tests/test_codex_commands_rest.py` |
| A | `tests/test_subagents.py` |
<!-- auto:end -->
