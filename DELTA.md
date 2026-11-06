# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/mcp` (D-064), item 25: the MCP servers the user's configuration names --
running, not available and why, stopped since, or disabled -- and the tools
each offers, read-only or not, with what the server says each does, as
codex's `/mcp` lists them. A server's arguments are not shown; its words are
cleaned before they are.

## Changes

- `cli/repl.py`: `show_mcp`, registered as `/mcp`.
- `tests/test_mcp_command.py` (new, 9 tests), against the fake MCP
  server.
- Docs: D-064; design (the MCP bullet); README (the command list); roadmap
  item 25.

## Decisions and open points

- It asks no server anything: it shows what the session already knows.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2327 passed, 1 skipped (2318 before, plus 9 in
  `tests/test_mcp_command.py`).
- Mutation check (scratch script): 14 mutants -- each state, the
  program without its arguments, the kind, the suffix, cleaning, escaping,
  the cut and its length, tools not offered, no servers, and the listing.
  All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-command`, from main at `b564f16`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| A | `tests/test_mcp_command.py` |
<!-- auto:end -->
