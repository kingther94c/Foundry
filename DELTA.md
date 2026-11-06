# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

An MCP client over stdio (D-043), the fifth step of the codex-parity order.
`[mcp_servers.<name>]` in the user's own `config.toml` (or a profile) starts a
server at session start; its tools reach the model as
`mcp__<server>__<tool>`, and every call goes through the policy as a mutator
-- refused in plan mode and `/review`, asked about otherwise, denied
unattended -- unless a rule allows it (rules may now name tools by pattern,
`mcp__docs__*`) or the entry says `read_only = true`. A server that fails is a
warning, never a failed session; the session's end stops it and what it
started.

## Changes

- `core/mcp.py` (new): `StdioClient` (process under a job object, scrubbed
  environment plus `env`/`env_vars`, JSON-RPC over lines, timeouts, cancel,
  the server's own requests answered), `McpTool`, `render_result`,
  `tool_name`, `start_servers`/`McpServers`.
- `core/config.py`: `McpServerConfig`, `[mcp_servers]` parsing; a repository
  config naming one is refused.
- `core/policy/engine.py`: a rule's tool may be a pattern (case-sensitive);
  dead-rule detection matches patterns.
- `cli/app.py`: servers started before rules are checked, closed on a failed
  build and with the session; `capability_probe` records and notices.
  `cli/repl.py`: an `mcp` row in `/status`.
- `tests/test_mcp.py` (new, 40 tests) and `tests/mcp_fake_server.py`, a
  standard-library MCP server with a mode per misbehaviour.
- Docs: D-043; design.md sections 1, 6 and 7; requirements.md; threat-model
  rows; README; roadmap.

## Decisions and open points

- The server's `readOnlyHint` is not trusted: a read-only fetch is how data
  leaves. Only the user's `read_only = true` makes tools read-only.
- Servers start at session start, all at once, each bounded by its startup
  timeout.
- Deferred: HTTP transports, resources and prompts, `tools/list_changed`,
  images.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1836 passed (1796 before, plus 40 in `tests/test_mcp.py`), each MCP test
  against a real server process.
- Mutation check (scratch script): 35 mutants -- the scrubbed environment,
  `env_vars` and `env`, the job owning the tree, ping answered and other
  server requests refused, log lines ignored, the tools capability, paging,
  the tool cap, a dead server failing fast, interruption, stderr in the
  reason, unknown content named, required arguments, the argument cap, error
  results, truncation, the tool error, name collisions, the empty schema,
  mutator by default, long names, disabled servers, a failed server closed,
  the job closed, a repository refused, unknown keys, a failed build and the
  session's end closing servers, the journal record, servers started at all,
  tool patterns, dead-rule matching, the status row -- all caught (one
  survived the first run, a failed server left running; a test was added).
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-client`, from main at `db0abb7`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| A | `src/foundry/core/mcp.py` |
| M | `src/foundry/core/policy/engine.py` |
| A | `tests/mcp_fake_server.py` |
| A | `tests/test_mcp.py` |
<!-- auto:end -->
