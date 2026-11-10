# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`core/config.py` split (D-161, P3): the layers and their precedence stay;
`[mcp_servers]` and the smaller sections move, as they were, to modules of
their own, with no change of behaviour.

## Changes

- `core/config.py` (1,412 lines -> 917): the layers, precedence and
  provenance, `Config`, `BackendConfig`, flags, profiles, the environment,
  `load_config`.
- New: `core/config_mcp.py` (`McpServerConfig` and its checks, the header
  patterns), `core/config_sections.py` (`ShellEnvironmentPolicy`, the
  terminal title, the docs' names, `MemorySettings`, skills, `notify`,
  permission rules).
- Imports elsewhere, the tests among them, point at the new modules.
- Docs: D-161; design (the package tree); roadmap (P3); the threat model's
  link to `_parse_shell_environment`.

## Decisions and open points

- The header patterns live with `[mcp_servers]`, their larger user;
  `config.py` imports them for `[backend] headers`.

## Verification

- The 44 test files that import the config or were changed: 1430 passed,
  1 skipped.
- Mutation check: 7 mutants of the moved code -- a repository allowing,
  the title alias, doc names bounded, the skills token budget, an unknown
  `inherit`, a secret over plain http, a server's name -- all killed (a
  repository allowing by `tests/test_architecture_config.py`).
- Full suite: 4808 passed, 1 skipped, the four splits stacked
  (22 min 38 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/p3-split-config`, from main at `3954406`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/slash_commands.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/config_edit.py` |
| A | `src/foundry/core/config_mcp.py` |
| A | `src/foundry/core/config_sections.py` |
| M | `src/foundry/core/mcp.py` |
| M | `src/foundry/core/memories.py` |
| M | `tests/test_child_environment.py` |
| M | `tests/test_mcp.py` |
| M | `tests/test_mcp_codex_keys.py` |
| M | `tests/test_mcp_command.py` |
| M | `tests/test_mcp_headers.py` |
| M | `tests/test_mcp_http.py` |
| M | `tests/test_mcp_robust.py` |
| M | `tests/test_mcp_server.py` |
| M | `tests/test_mcp_tool_filters.py` |
| M | `tests/test_memories.py` |
| M | `tests/test_memories_guarded.py` |
| M | `tests/test_program_search.py` |
| M | `tests/test_shell_env.py` |
| M | `tests/test_terminal_title.py` |
<!-- auto:end -->
