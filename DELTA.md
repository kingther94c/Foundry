# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Which of an MCP server's tools are offered (D-080), item 41:
`enabled_tools` and `disabled_tools` per `[mcp_servers.<name>]`, as codex's.
A tool left out is not registered; a name the server does not offer is a
warning; what was left out is shown and recorded.

## Changes

- `core/config.py`: the two settings, `McpServerConfig.offers()`.
- `core/mcp.py`: `start_servers` leaves the others out (`McpServers.left_out`)
  and warns of a name the server does not list.
- `cli/app.py`: the start line and the `capability_probe` record say what was
  left out. `cli/repl.py`: `/mcp` names them.
- `tests/test_mcp_tool_filters.py` (new, 16 tests).
- Docs: D-080; README (the MCP example); design; roadmap item 41.

## Decisions and open points

- Exact names, the server's own; an empty `enabled_tools` offers nothing.
- Open: the hundred-tool cap applies to what is listed, before the filters,
  so a tool past the hundredth cannot be enabled.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2622 passed, 1 skipped (2606 before, plus 16 in
  `tests/test_mcp_tool_filters.py`).
- Mutation check (scratch script): 10 mutants -- an empty list
  offering all, disabled ignored, enabled not checked, disabled not read,
  nothing left out, left out unrecorded, typos unsaid (either setting), not
  journaled, `/mcp` silent. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-tool-filters`, from main at `7c88b58`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/mcp.py` |
| A | `tests/test_mcp_tool_filters.py` |
<!-- auto:end -->
