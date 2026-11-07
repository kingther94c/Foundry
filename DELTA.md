# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A key piped to login, and where the key comes from (D-087), item 48, as
codex: `printenv OPENAI_API_KEY | foundry login --with-api-key` stores the
key (or gateway token) the configured source reads, and `foundry login
status` says where it comes from. The key is never shown: the flag refuses a
terminal, a refusal never quotes what was piped, and the status names a
source, not any part of the key.

## Changes

- `cli/app.py`: `_secret_from_stdin` (bytes decoded as UTF-8, a byte order
  mark and the line end dropped, one line with no spaces or control
  characters, at most 16 KiB); `_login_status`; `login` takes `status` and
  `--with-api-key`; without the flag a pipe is told about it.
- `tests/test_login_stdin.py` (new, 25 tests).
- Docs: D-087; README; requirements; roadmap item 48; threat model.

## Decisions and open points

- codex's status shows a masked key; Foundry shows none of it.
- A backend with `env_key` still takes nothing; its status says whether the
  variable is set.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2802 passed, 1 skipped (2777 before, plus 25 in
  `tests/test_login_stdin.py`).
- Mutation check (scratch script): 16 mutants -- a terminal read, the
  byte order mark kept, a long pipe taken on either path, the limit off by
  one, spaces or control characters taken, nothing taken, the status
  ignoring the variable or `env_key`, reading one field, failing a stored
  key, taking a key, missing; the flag ignored; the pipe not pointed at it.
  All are caught. Two checks the first run showed to be redundant (the mark
  dropped as bytes and again as text; `UnicodeDecodeError` beside
  `ValueError`) were removed rather than tested.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/login-stdin`, from main at `aa2be93`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_login_stdin.py` |
<!-- auto:end -->
