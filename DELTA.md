# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Settings the protocol in use cannot send, refused rather than dropped (D-100),
item 61: `fallbacks = "off"` off Claude, and a `reasoning_summary` that asks
for a summary on Chat Completions.

## Changes

- `core/config.py`: `CHAT_PROTOCOL`; the two refusals at the end of
  `load_config`, beside verbosity's.
- `tests/test_exec_header.py`: its two runs on a Chat Completions server set
  `reasoning_summary = "none"`, which that protocol takes, instead of a
  summary it would have dropped.
- `tests/test_protocol_settings.py` (new, 12 tests).
- Docs: D-100; README; roadmap item 61.

## Decisions and open points

- The "ask for nothing" values (`default`, `none`) are taken everywhere.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3256 passed, 1 skipped (3244 before, plus 12 in
  `tests/test_protocol_settings.py`).
- Mutation check (scratch script): 7 mutants -- either dropped again,
  the default or `none` refused, Claude or the Responses API refused its own.
  All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/protocol-settings`, from main at `99a2fd5`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/config.py` |
| M | `tests/test_exec_header.py` |
| A | `tests/test_protocol_settings.py` |
<!-- auto:end -->
