# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`[features]` (D-144), item 105, as codex's: its feature flags read as
Foundry's own settings, `--enable`/`--disable`, and `foundry features
list|enable|disable`.

## Changes

- `core/features.py` (new): codex's documented features, each with what it
  is here -- a switch, or none -- and every other name in codex's registry;
  `refusal`, `apply_features`, `state`.
- `core/config.py`: `[features]` in the user's layers (a repository's
  refused); `run_hooks`, `shell_tool`, `view_image`, `goals`, with
  `multi_agent` as `subagents`; `active_hooks()` keeps the machine policy's;
  `shell_tool = false` is a deny rule for `run_command`; `-c features.X`.
- `core/config_edit.py`: `set_feature`, checked as `add_server` is.
- `cli/app.py`: `--enable`/`--disable`; `foundry features`; view_image and
  hooks by the switches; a resumed goal kept in the journal with goals off;
  `main` prints an error's words as they are, not as markup.
- `cli/agents.py`: a sub-agent gets view_image only where the session has it.
- `cli/repl.py`: `/goal` with goals off; `/hooks` says which are not run;
  codex's `/experimental` points at `foundry features list`.
- `tests/test_features.py` (new, 45 tests); one hint's words in
  `tests/test_flag_setting_names.py`.
- Docs: D-144; README; requirements; threat model; roadmap item 105.

## Decisions and open points

- A feature Foundry has no counterpart for is off: false is taken, true
  refused with what Foundry has instead. `memories` is one until item 106.
- `enable`/`disable` write the user's own file, never a profile's, as codex's.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4610 passed, 1 skipped (4565 before, plus 45 in
  `tests/test_features.py`). The full run gave 4608 passed and one failure,
  a hint's words, updated in `tests/test_flag_setting_names.py`; that file
  and `tests/test_features.py`, one test added to it after the run, were run
  again: 74 passed.
- Mutation check (scratch script): 26 mutants. All 26 are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-105`, from main at `cf76436`.

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
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/config_edit.py` |
| A | `src/foundry/core/features.py` |
| A | `tests/test_features.py` |
| M | `tests/test_flag_setting_names.py` |
<!-- auto:end -->
