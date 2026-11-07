# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Which files are instructions, and how much of them is read (D-086), item 47,
as codex: `AGENTS.override.md` over `FOUNDRY.md` over `AGENTS.md` in each
directory (and the user's own), then the user's
`project_doc_fallback_filenames`; `project_doc_max_bytes` is the budget the
repository's files share, 0 for none. Both are the user's: a repository may
only lower the budget.

## Changes

- `core/prompts.py`: `AGENTS.override.md` first; `doc_names`; fallbacks and
  the budget through `repository_docs` and `load_instructions`; a file is
  read only up to the budget.
- `core/config.py`: the two `[runtime]` settings, validated; `-c` takes
  them; a repository may lower the budget and may not name fallbacks.
- `cli/app.py`: the user's settings are read before the trust prompt, which
  asks about fallback-named files and not about files the user reads none
  of; the repository's config is read after it, as before.
- `cli/repl.py`: `/init` counts a fallback-named file.
- `tests/test_project_doc_settings.py` (new, 29 tests).
- Docs: D-086; README; design; requirements; roadmap item 47; threat model.

## Decisions and open points

- The budget is counted in characters, as Foundry's always was; codex's in
  bytes.
- The user's own file keeps its fixed cap and takes no fallback, as codex's
  home file.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2777 passed, 1 skipped (2748 before, plus 29 in
  `tests/test_project_doc_settings.py`).
- Mutation check (scratch script): 25 mutants -- the override last or
  missing, fallbacks ignored at each layer or not deduplicated, 0 still
  reading, the fixed budget used in each place, a cut never marked, a
  negative budget, a repository raising the budget or naming fallbacks, any
  name, any number of names, duplicates kept, `-c` unable to set them, the
  trust prompt ignoring fallbacks or asking about unread files, the
  repository's settings dropped, the session ignoring either setting, `/init`
  ignoring fallbacks. All are caught (the trust one after a test was added).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-doc-settings`, from main at `0214fbb`.

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
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/prompts.py` |
| A | `tests/test_project_doc_settings.py` |
<!-- auto:end -->
