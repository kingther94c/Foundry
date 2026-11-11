# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry login` asks for the OpenAI and the Anthropic key, Enter skipping
one (D-182): it asked only for the key the configuration of the moment
reads, and a user setting up Claude was asked for an OpenAI key.

## Changes

- `cli/login.py`: both keys asked, the configured one first; stored once
  every answer is in; nothing entered or a cancel stores nothing; the same
  key twice refused; a warning when the configured key is still missing;
  `login status` names the other key too. A gateway profile is asked for
  its token only.
- Tests: `tests/test_login_both_keys.py`; the status lines in
  `test_login_stdin.py` and the skipped prompt in `test_anthropic_wiring.py`
  updated.
- Docs: D-182; README (Claude section, commands table); requirements.

## Decisions and open points

- The user's request, while setting up the real endpoint.
- `--with-api-key` is unchanged: one key per pipe, the configured one.

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (9, all killed): only the configured key asked; the other one
  first; no skip hint; each stored as answered; the same key twice taken;
  no warning; the warning always; the variable not counted; status naming
  one key.
- Full suite as CI runs it: 4900 passed, 1 skipped (21 min 27 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
