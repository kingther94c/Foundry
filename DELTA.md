# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`-c` with codex's names (D-098), item 59: a `-c` name no code reads is
refused with where it belongs, as a file's key is -- `-c
model_reasoning_effort=high` names `backend.effort`.

## Changes

- `core/config.py`: `_FLAG_KEYS`, what `-c` sets; `_no_flag_setting`, the
  refusal with its hint; `sandbox_workspace_write` among codex's tables told
  there is no sandbox.
- `tests/test_flag_setting_names.py` (new, 29 tests).
- Docs: D-098; README; roadmap item 59.

## Decisions and open points

- Told, not taken, as in a file: no `-c` name is accepted that was not before.
- A typo's guess comes from the table it was written under, when it was.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3222 passed, 1 skipped (3193 before, plus 29 in
  `tests/test_flag_setting_names.py`).
- Mutation check (scratch script): 11 mutants -- no hint, a key taken
  under the wrong table, a backend's name taken, codex's tables and names not
  told, profile not sent to `-p`, a file's table not sent to the file, the
  other table not named, a typo matched across tables or its guess without
  the table. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/flag-setting-names`, from main at `1fcca6f`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/config.py` |
| A | `tests/test_flag_setting_names.py` |
<!-- auto:end -->
