# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

MCP resources (D-102), item 63: a server that offers resources gives the model
codex's `list_mcp_resources`, `list_mcp_resource_templates` and
`read_mcp_resource`. Listing runs unasked; a read is asked about unless a rule
allows it.

## Changes

- `core/mcp_resources.py` (new): `ResourceTool`, the three tools over the
  servers that offer resources, and `register`.
- `core/mcp.py`: `list_resources` and `read_resource` on both clients;
  `McpServers.resources`; `start_servers` registers the resource tools.
- `core/policy/engine.py`: the two list tools among `READ_ONLY_TOOLS`.
- `cli/app.py`: the start notice and `capability_probe` say a server offers
  resources; `cli/repl.py`: so does `/mcp`.
- `tests/mcp_fake_server.py`: a `resources` mode.
- `tests/test_mcp_resources.py` (new, 21 tests).
- Docs: D-102; README; design; threat model; roadmap item 63.

## Decisions and open points

- A list sends nothing the model wrote: only a cursor the server gave.
- A read is asked about (any URI, not held to the workspace), but in plan mode
  asked rather than refused.
- Not done: subscriptions and change notifications.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3285 passed, 1 skipped (3264 before, plus 21 in
  `tests/test_mcp_resources.py`).
- Mutation check (scratch script): 23 mutants -- offered without
  resources or never registered, listing asked about or a read let through, a
  read refused in plan mode, a rule unable to name the server, any server,
  cursor, uri length or argument taken, a read without a server, one server's
  list made every server's, pages, templates, images, contents, truncation,
  descriptions and refusals mishandled, the journal and `/mcp` not told, any
  cursor sent back or none ever recorded. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-resources`, from main at `23d2828`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/mcp.py` |
| A | `src/foundry/core/mcp_resources.py` |
| M | `src/foundry/core/policy/engine.py` |
| M | `tests/mcp_fake_server.py` |
| A | `tests/test_mcp_resources.py` |
<!-- auto:end -->
