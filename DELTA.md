# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry mcp list | add | remove` (D-082), item 43, as `codex mcp`: the MCP
servers in the user's own config.toml from the command line. An edit is
text -- a table appended or one server's tables taken out -- written only if
it parses back to the old settings with exactly that server added or gone.
Two bugs found on the way: an `always` rule with a quote in its command
broke config.toml, and the empty `/mcp` line lost its table name.

## Changes

- `core/config_edit.py` (new): `add_server`, `remove_server`,
  `server_table`, `toml_string`; atomic writes.
- `cli/app.py`: `cmd_mcp` and its parser (the command after `--` split off
  before parsing); the `always` rule's values written with `toml_string`.
- `cli/repl.py`: `NO_MCP` escapes `[mcp_servers.NAME]` and names
  `foundry mcp add`.
- `core/winapi.py`: `looks_like_credential()`, shared with
  `child_environment`.
- `tests/test_mcp_cli.py` (new, 28 tests).
- Docs: D-082; README (command table, MCP section); design; roadmap item 43;
  threat model.

## Decisions and open points

- A credential-shaped `--env` is refused for `--env-var`; a server of the
  same name is refused on add, naming `remove`.
- A server written inline, with dotted keys or under a profile is left to
  the user to edit by hand.
- Open: `codex mcp get` and `mcp login` are not offered.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2686 passed, 1 skipped (2658 before, plus 28 in
  `tests/test_mcp_cli.py`).
- Mutation check (scratch script): 22 mutants -- settings not
  checked, a name twice, DEL written raw, surrogates escaped, keys never
  quoted, CRLF ignored, subtables left, its own comments kept, the next
  table's comments dropped, comments above kept, the cut not checked, not
  written in one step, a temporary file left, a secret in `--env`, `--env`
  without `=`, the command not split off, `--env` with `--url` dropped,
  `--read-only` ignored, passed variables unsaid, the JSON transport wrong,
  the `always` pattern unquoted, the empty `/mcp` line losing its table.
  All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-cli`, from main at `76221e1`.

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
| A | `src/foundry/core/config_edit.py` |
| M | `src/foundry/core/winapi.py` |
| A | `tests/test_mcp_cli.py` |
<!-- auto:end -->
