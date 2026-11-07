# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Which variables a command gets (D-083), item 44: `[shell_environment_policy]`
in the user's own config.toml, as codex's -- `inherit` (core, all, none),
`include`, `exclude`, `set`, `include_only` -- for `run_command` and
`!COMMAND`. A credential-shaped name passes only by its exact name in
`include`, and its value is then scrubbed; a repository cannot set it.

## Changes

- `core/config.py`: `ShellEnvironmentPolicy`, `_parse_shell_environment`,
  `Config.shell_environment` (user, managed, profile and `-c` layers only).
- `core/winapi.py`: `child_environment(policy=)`; the allowlist split into
  `_core_environment` and `_finish`.
- `core/tools/command.py`, `cli/repl.py`: commands get the policy;
  `cli/app.py` sets `ToolContext.env_policy`.
- `tests/test_shell_env.py` (new, 19 tests).
- Docs: D-083; README; design; roadmap item 44; threat model.

## Decisions and open points

- codex's `ignore_default_excludes` is refused with an explanation; a
  credential-shaped key in `set` is refused.
- Not changed: git's environment and an MCP server's `env`/`env_vars`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2705 passed, 1 skipped (2686 before, plus 19 in
  `tests/test_shell_env.py`).
- Mutation check (scratch script): 18 mutants -- a secret by a
  pattern, a passed secret not scrubbed, `inherit = "all"` carrying secrets,
  `inherit = "none"` inheriting, `exclude`, `set` or `include_only` ignored,
  names matched case-sensitively, Foundry's own variables dropped, a secret
  in `set`, codex's escape hatch unsaid, any `inherit`, a repository setting
  it, names unchecked, a profile replacing it whole, the session,
  `run_command` or `!COMMAND` not using it. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/shell-env`, from main at `d9e3c08`.

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
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/tools/command.py` |
| M | `src/foundry/core/winapi.py` |
| A | `tests/test_shell_env.py` |
<!-- auto:end -->
