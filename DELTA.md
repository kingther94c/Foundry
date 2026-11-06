# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Instructions in layers (D-041), the third step of the codex-parity order. The
system prompt now carries the user's own `FOUNDRY.md` / `AGENTS.md` from the
Foundry folder, then the repository's from its top (the nearest `.git`) down to
the working directory, one per directory, sharing the 24 KB budget. One file
reads exactly as before. A file above the working directory raises the trust
question like one at its root. `/status` names the files loaded.

## Changes

- `core/prompts.py`: `Instructions`, `repository_docs`, `load_instructions`;
  `load_project_doc` is now the repository part of it.
- `cli/app.py`: `build` loads both layers and keeps their sources on the
  wiring; the trust question looks at every layer.
- `cli/repl.py`: a `notes` row in `/status`.
- `tests/test_instructions.py` (new, 13 tests).
- Docs: D-041; design.md section 5; requirements.md; threat-model rows; README;
  roadmap.

## Decisions and open points

- Outside a repository only the workspace is read; below the working
  directory nothing is.
- The user's own file needs no trust; it is capped like the rest.
- Deferred: per-user overrides inside a repository, configurable names.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1760 passed (1747 before, plus 13 in `tests/test_instructions.py`).
- Mutation check (scratch script): 14 mutants -- repository needs trust, own
  file read, empty own file, cap, shared budget, left out past the budget,
  outside a repository, worktree top, top down, files only, FOUNDRY.md first,
  parents ask for trust, status sources wired, status row -- all caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/instruction-layers`, from main at `7c2469e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/prompts.py` |
| A | `tests/test_instructions.py` |
<!-- auto:end -->
