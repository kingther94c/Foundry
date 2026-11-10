# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`cli/app.py` split along its commands (D-158, P3): the parser and `main`
stay; building a session and each group of commands go to modules of their
own, moved as they were, with no change of behaviour.

## Changes

- `cli/app.py` (2,756 lines -> 480): the parser, `main`, `completion`.
- New: `cli/wiring.py` (building and closing a session),
  `cli/session_commands.py` (`run`, `resume`, `fork`, `undo`, `sessions`,
  `archive`, `delete`), `cli/unattended.py` (`exec`, `review`, `record`),
  `cli/login.py`, `cli/mcp_commands.py` (`mcp`, `mcp-server`),
  `cli/setup_commands.py` (`doctor`, `policy`, `features`, `memories`,
  `report`).
- `cli/mcp_server.py` and the `TYPE_CHECKING` imports of `Wiring` import
  from the new modules.
- Tests: imports from the new modules; each `monkeypatch.setattr(app, ...)`
  patches the module that looks the name up (`test_output_schema` patches
  `default_redactor` in both `wiring` and `unattended`, where `app` held
  both lookups).
- Docs: D-158; design (the package tree); roadmap (P3); the threat model's
  links to `cli/app.py` point at the module each function is in now.

## Decisions and open points

- No re-exports from `app`: a stale patch of `app.NAME` fails loudly instead
  of patching a name nothing reads.
- Moved mechanically (by the AST, imports computed from what each module's
  code reads); the one comment dropped is the `# --- foundry mcp ---`
  section header, now a module of its own.

## Verification

- Every patch the tests point at a new module is reached: each patched
  value swapped for one that raises makes its test fail (`reach` check):
  43 of 43.
- Mutation check: 10 mutants of the moved code -- the `mcp add` split,
  termination journaled, an ephemeral session discarded, the preview cut,
  the login and stdin limits, the scrubbed result, a credential's variable
  hidden, an unattended ask denied, an untrusted workspace's rules -- all
  killed.
- Full suite: 4808 passed, 1 skipped (21 min 0 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
