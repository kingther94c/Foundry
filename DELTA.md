# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Settings on the command line (D-042), the fourth step of the codex-parity
order. `run`, `resume`, `exec` and `record` take `-m`, `--mode`,
`-c key=value` and `--profile`; profiles are `[profiles.<name>]` tables in the
user's own `config.toml`, applied between the user's settings and the
repository's. `exec --mode accept_edits` lets an unattended run patch the
workspace, and `exec -o FILE` writes its last word to a file. `-c` refuses
secrets and unknown keys.

## Changes

- `core/config.py`: `parse_settings` (the `-c` layer), `_apply_profile`,
  `load_config(profile=, settings=)`, `FOUNDRY_PROFILE`.
- `core/prompts.py`: `UNATTENDED_LINE` in the permissions paragraph for an
  unattended run outside `dont_ask`.
- `cli/app.py`: `_config_flags`, `_config_args`; `build(profile=, settings=)`;
  `/new` keeps the flags; `exec --mode` (dont_ask, plan, accept_edits) with the
  prompt rebuilt once the run is unattended; `exec -o`; a flag first means
  `run`.
- `tests/test_cli_flags.py` (new, 36 tests).
- Docs: D-042; requirements.md section 4.3; threat-model row; README; roadmap.

## Decisions and open points

- `exec` takes its mode only from `--mode`, never from configuration.
- A bare first word stays a subcommand: a typo must not start a paid run.
- Deferred: `--add-dir`, `-c` for permission rules, `/model` mid-session.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1796 passed (1760 before, plus 36 in `tests/test_cli_flags.py`).
- Mutation check (scratch script): 22 mutants -- no secrets, typos refused,
  `name` not a setting, plain-text values, empty key, bare backend key,
  settings applied, environment over file, profile name recorded, unknown
  profile, profiles listed, flag first means run, the model flag, the exec
  prompt rebuilt, exec defaulting to dont_ask, the output written and
  scrubbed, a write failure reported, `/new` keeping the flags, exec's modes,
  the unattended line (once, and at all) -- all caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/cli-flags`, from main at `11c45ae`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/prompts.py` |
| A | `tests/test_cli_flags.py` |
<!-- auto:end -->
