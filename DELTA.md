# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What a child is given (D-153, from P2): no relative PATH entry, so cmd.exe
and PowerShell cannot find a program in the workspace through `bin` or `.`;
no variable the configuration reads a credential from, whatever its name,
unless `include` names it; and the credentials passed by name, an MCP
server's `env_vars` and fine-grained GitHub tokens are scrubbed from the start.

## Changes

- `core/winapi.py`: `withhold_variables()` and `_secret()`, used wherever a
  credential-shaped name was; `register_included_secrets()`;
  `absolute_path_entries()`, applied to every child's PATH in `_finish`;
  `set` replaces a variable whatever the case it was inherited in.
- `cli/app.py`: `_guard_credentials(config)` in `build`, before anything
  starts a child.
- `core/mcp.py`: a stdio server's `env_vars` values are registered.
- `core/redaction.py`: the `github_pat_` shape.
- Docs: D-153, design (run_command's environment), threat model (two rows).

## Decisions and open points

- The withheld set lasts for the process, as the redactor's values do: a
  `foundry mcp-server` that built one task with a header variable keeps it
  from the children of every later task. Erring that way is the safe one.
- A withheld variable still reaches a child by its exact name in the user's
  own `include`, as a credential-shaped one does (D-083), and is scrubbed.
- An MCP server's `env_vars` are passed as before; only their values are
  now scrubbed. Its plain `env` values are not: they sit in config.toml,
  which says secrets belong in `env_vars`.
- `\x` (rooted, no drive) is dropped as well: it is on the current drive,
  the workspace's, and a clone at a drive's root would supply it.

## Verification

- `tests/test_child_environment.py` (11): relative entries dropped, under
  every policy and through a hook (the repository's `bin\hostname.bat` was
  run before); withheld names under inherit all, a pattern, the core
  allowlist and an exact include; a session's configuration withholding its
  four variables and registering `include`'s value at once; `build` calling
  the guard; `github_pat_`; an MCP server's `env_vars`.
- Environment, hook and MCP tests (7 files): 159 passed, 1 skipped.
- Mutation check: 18 mutants, all killed.
- Full suite (on D-152): 4757 passed, 1 skipped.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
