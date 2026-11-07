# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

An MCP server's table copied from codex loads as it is (D-110), item 71:
`startup_timeout_sec` and `tool_timeout_sec` are Foundry's `_s` timeouts
under codex's names, and `cwd` is the folder a server Foundry starts runs in.

## Changes

- `core/config.py`: the three keys; `_MCP_TIMEOUTS`; `cwd` among what only a
  started server has; `McpServerConfig.cwd`.
- `core/mcp.py`: a stdio server starts in its `cwd`, checked to be a folder.
- `cli/app.py`: `mcp get` shows `cwd`, text and JSON.
- `tests/test_mcp_codex_keys.py` (new, 28 tests); `tests/mcp_fake_server.py`
  gains a mode that answers with its folder; `tests/test_mcp_get.py`'s JSON
  expectation has `cwd`.
- Docs: D-110; README; design; threat model; roadmap item 71.

## Decisions and open points

- One setting under both names is refused; an error names the key as
  written; Foundry's defaults (20 s, 120 s) stay, not codex's (10 s, 60 s).
- `cwd` is absolute and only with `command`; a folder that is not there is
  the server's failure at start, a warning.
- Next: a url server's `http_headers` and `env_http_headers`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3552 passed, 1 skipped (3524 before, plus 28 in
  `tests/test_mcp_codex_keys.py`).
- Mutation check (scratch script): 22 mutants -- each key unknown,
  `url` with `cwd`, both names taken, either name ignored, a ceiling raised,
  codex's defaults, the error naming Foundry's key, a relative, empty or
  non-string `cwd`, `cwd` not kept or not used, a file or nothing as a
  folder, `cwd` unscrubbed, shown empty or not shown. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-71`, from main at `0bb0cfc`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/mcp.py` |
| M | `tests/mcp_fake_server.py` |
| A | `tests/test_mcp_codex_keys.py` |
| M | `tests/test_mcp_get.py` |
<!-- auto:end -->
