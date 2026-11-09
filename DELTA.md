# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Memories (D-145), item 106, as codex's: with `features.memories` on, a
model reads a trusted workspace's idle sessions and keeps what a later
session there should know, merged into notes and a summary each session in
that workspace starts with; `[memories]`, `foundry memories
show|update|reset`, and `/memories`.

## Changes

- `core/memories.py` (new): the workspace's folder, which sessions a pass
  reads, the pass (read, keep, prune, merge, one at a time by a lock),
  `forget`, and the read-only `read_memories` tool.
- `prompts/memory_extract.md`, `prompts/memory_consolidate.md` (new): what
  the model is asked; both say what they read is data, not instructions.
- `core/config.py`: `MemorySettings` and `[memories]` from the user's layers
  (a repository's refused), codex's names and ranges, its aliases, and its
  keys Foundry has no use for refused with what it does instead.
- `core/features.py`: `memories` is a switch, off by default.
- `core/context.py`: the summary goes in the instructions, after the skills.
- `core/session.py`: the `memory_mode` record.
- `core/policy/engine.py`: `read_memories` is read-only.
- `cli/app.py`: `make_backend` taken out of `_assemble_with`; a session in a
  trusted workspace gets the summary and `read_memories`; `generate_memories
  = false` journaled; an interactive session starts the pass in a detached
  process (`-P`, from the Foundry folder); `foundry memories`.
- `cli/memories.py` (new): `/memories`, `use on|off`, `generate on|off`.
- `cli/agents.py`: a sub-agent starts with the same summary.
- `cli/repl.py`: `/memories` is Foundry's own, no longer a codex-only hint.
- `tests/test_memories.py` (new, 47 tests); `tests/test_features.py`
  (memories is a switch now); `tests/test_codex_commands_rest.py`.
- Docs: D-145; README; requirements; threat model; roadmap item 106.

## Decisions and open points

- Unlike codex's, memories stay with the workspace they came from, and only
  where the user trusts it: what a model kept of one repository's files
  never reaches another's instructions.
- codex's `version`, `dual_write`, `max_unused_days`,
  `min_rate_limit_remaining_percent`, `consolidation_service_tier` and
  `dedicated_tools` are refused with what Foundry does instead.

## Verification

- `python -X warn_default_encoding -m pytest -q`: 4657 passed,
  1 skipped (4610 before, plus the new tests in
  `tests/test_memories.py`).
- Mutation check (scratch script): 51 mutants. All are caught; 9 survived the first run -- the tests that a pass reads nothing asserted only that it read none, which a pass with no answers to give also does -- and are caught by the tests made stricter. The full run counted 4655 before two tests were added to tests/test_memories.py, which then passed on their own (47).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-106`, from main at `7293673`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/agents.py` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/memories.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/features.py` |
| A | `src/foundry/core/memories.py` |
| M | `src/foundry/core/policy/engine.py` |
| M | `src/foundry/core/session.py` |
| A | `src/foundry/prompts/memory_consolidate.md` |
| A | `src/foundry/prompts/memory_extract.md` |
| M | `tests/test_codex_commands_rest.py` |
| M | `tests/test_features.py` |
| A | `tests/test_memories.py` |
<!-- auto:end -->
