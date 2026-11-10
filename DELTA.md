# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A `foundry mcp-server` client's `config` may only tighten (D-150), the
highest finding of P2's review: applied as the server's own `-c`, it could
send the user's credential to a host of its choosing, read any file as a
compaction prompt, and turn off the user's hooks.

## Changes

- `core/config.py`: `load_config(client_settings=...)`, applied after `-c`
  as a layer with a repository config's limits; the refusals name the layer
  ("the client's config may not set ...").
- `cli/app.py`: `build(client_settings=...)`, passed to both loads.
- `cli/mcp_server.py`: a call's settings go there, no longer merged into the
  server's `-c`.
- Docs: D-150, design (mcp-server), threat model (the client's row).

## Decisions and open points

- The client keeps the call's `model`, `profile` and `mode` arguments; only
  `config` is limited.
- The rest of P2's findings are listed for their own branches (D-151 on).

## Verification

- `tests/test_client_config.py` (20): each connection setting refused before
  any request; instructions, prompt file, compaction limit, fallback names
  and features refused; budgets only lowered past the server's; a mode in
  config only narrower; the server's own `-c` still chooses the backend; a
  trusted workspace the same.
- `test_mcp_server.py`, `test_mcp_elicitation.py`, `test_flag_setting_names.py`:
  123 passed.
- Mutation check: 8 mutants, all killed (one after the trusted-workspace test).
- Full suite: 4727 passed, 1 skipped.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-client-config`, from main at `e1cdeaa`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/mcp_server.py` |
| M | `src/foundry/core/config.py` |
| A | `tests/test_client_config.py` |
<!-- auto:end -->
