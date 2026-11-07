# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

An unattended run says what it runs with (D-092), item 53, as codex's exec
opens with its settings: `session_started` carries protocol, profile, mode,
effort, reasoning summary and verbosity, and without `--json` they follow the
session's line on stderr, dim and never wrapped.

## Changes

- `core/events.py`: `SessionStarted` gains `protocol`, `profile`, `mode`,
  `effort`, `reasoning_summary`, `verbosity`.
- `cli/app.py`: `cmd_exec` fills them, the mode from the run's policy.
- `cli/render.py`: `run_settings`; the renderer prints them under the
  session's line with `soft_wrap=True`.
- `tests/test_exec_header.py` (new, 11 tests).
- Docs: D-092; README; design; roadmap item 53.

## Decisions and open points

- No base URL or header shown: no setting's source text reaches a log.
- Not wrapped: a path cut at the console's width cannot be copied.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2929 passed, 1 skipped (2918 before, plus 11 in
  `tests/test_exec_header.py`).
- Mutation check (scratch script): 12 mutants -- protocol and profile,
  effort, summary or verbosity not carried, an event that does not say still
  listing, an unset effort shown blank, unset summary and verbosity shown, the
  terminal never told, a long path wrapped, a value read as markup, escapes
  not stripped. All are caught but one that is equivalent: the configured
  mode in place of the run's, which agree when the run starts, since exec
  passes its mode to `build` as an override (the test for it stays as a
  guard).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/exec-header`, from main at `f165300`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/events.py` |
| A | `tests/test_exec_header.py` |
<!-- auto:end -->
