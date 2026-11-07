# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

MCP tool hooks, as codex's (D-121), item 82: `type = "mcp_tool"` calls a
tool of an MCP server the session already has, its arguments filled in from
the event, and reads its text as a command hook's answer.

## Changes

- `core/hooks.py`: the handler read (`_MCP_KEYS`; `server`, `tool`,
  `input`; SessionEnd refuses one; always in step); `Hook.mcp_server`,
  `mcp_tool`, `mcp_input`; `fill_input` for `${field.nested}`;
  `HookSet.mcp` and `_call_tool`.
- `cli/app.py`: the hook set is given the session's MCP clients.
- `tests/test_mcp_hooks.py` (new, 31 tests, against the test MCP
  server); `tests/test_hooks.py`: the handler types named.
- Docs: D-121; README; design; threat model; roadmap item 82.

## Decisions and open points

- The call is not asked about and does not pass the model's policy, as
  codex's does not: the user named it, and it reaches only a server the
  session already started.
- `async` on an `mcp_tool` hook is read and run in step, as codex runs it.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4134 passed, 1 skipped (4104 before, less the case of
  `tests/test_hooks.py` that refused an `mcp_tool` hook, plus 31 in
  `tests/test_mcp_hooks.py`).
- Mutation check (scratch script): 25 mutants over reading the
  handler, filling in the arguments, the call (its client, timeouts,
  cancel, error results, text content) and the wiring. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-82`, from main at `49d5bcf`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/hooks.py` |
| M | `tests/test_hooks.py` |
| A | `tests/test_mcp_hooks.py` |
<!-- auto:end -->
