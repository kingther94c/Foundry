# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Skills found and switched as codex's (D-114), item 75: `.agents/skills` in
the workspace and each folder above it to the repository's top, the nearest
first; codex's `[[skills.config]]` (by path or name, last match deciding,
the machine policy's last) and `skills.max_context_tokens`.

## Changes

- `core/skills.py`: `repository_skill_folders`, `switched_on`,
  `Skills.disabled`; `load_skills(switches=)`.
- `core/config.py`: `[skills]` (`_parse_skills`), `skill_switches`,
  `managed_skill_switches`, `all_skill_switches()`,
  `skills_max_context_tokens`; a repository's config may not set it.
- `core/prompts.py`: `repository_top` (was private).
- `cli/app.py`: trust asks of any skills folder up to the top; the build
  passes the switches and the budget; `cli/repl.py`: `/skills` says which
  are turned off (the table's name escaped, which Rich ate as markup).
- `tests/test_skills_config.py` (new, 38 tests).
- Docs: D-114; README; design; threat model; roadmap item 75.

## Decisions and open points

- The nearest folder wins a name; the user's own still win over all.
- The machine policy's entries count after the user's, so what it turns off
  stays off.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3754 passed, 1 skipped (3716 before, plus 38 in
  `tests/test_skills_config.py`).
- Mutation check (scratch script): 28 mutants over the folders, their
  order and the top, names and paths, the last match, the table's checks and
  its layer, the budget, trust, the build and `/skills`. All are caught; one
  more (comparing paths as strings) is equivalent on Windows, where
  `resolve()` gives a file's own case.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-75`, from main at `d898050`.

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
| M | `src/foundry/core/prompts.py` |
| M | `src/foundry/core/skills.py` |
| A | `tests/test_skills_config.py` |
<!-- auto:end -->
