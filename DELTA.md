# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A setting nobody reads is an error in the files too (D-091), item 52: a key
at the top of a file, under `[backend]` or `[runtime]`, in a flat
`[profiles.NAME]` table or in a permission rule is refused by layer and name,
with where it lives here -- codex's names told Foundry's. A misspelt rule key
no longer leaves the rule matching everything.

## Changes

- `core/config.py`: `_SECTIONS`, `_PROFILE_KEYS`, `_RULE_KEYS`,
  `_CALLED_HERE`, `_NOT_A_SETTING`; `_no_setting`, `_check_names`; `_apply`
  checks names first (`profiles_allowed`, `tables`), names every backend key
  a repository may not set, and no longer skips unknown backend keys;
  `_parse_rules` refuses a rule's unknown key; `_CONNECTION_KEYS` (now
  unused) removed.
- `tests/test_unknown_settings.py` (new, 36 tests).
- Docs: D-091; requirements 4.3; README; threat-model; roadmap item 52.

## Decisions and open points

- codex's flat spellings are refused with a hint, not taken as a second
  spelling: one place per setting.
- Only the chosen profile is read, as before.
- Every TOML example in README.md and docs/ was checked against the new
  names (all pass).

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2918 passed, 1 skipped (2882 before, plus 36 in
  `tests/test_unknown_settings.py`); the suite also passed with only the
  check in place, before the new tests, so no existing test relied on a
  skipped key.
- Mutation check (scratch script): 19 mutants -- unknown sections or
  table keys taken, profiles taken anywhere or refused in the user's file, a
  section that is not a table, runtime keys not checked, names never
  checked, a profile's table not named, a rule's unknown key taken or its
  reason refused, each hint dropped, no guess, an empty hint shown, a
  repository told one key. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/unknown-settings`, from main at `46846db`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/core/config.py` |
| A | `tests/test_unknown_settings.py` |
<!-- auto:end -->
