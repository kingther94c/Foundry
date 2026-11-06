# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Reasoning effort on every protocol (D-061), item 22: `backend.effort` now
reaches the Responses API (`reasoning.effort`) and Chat Completions
(`reasoning_effort`) as well as Claude, each protocol's levels checked when
the config loads; `/effort [LEVEL]` shows or changes it mid-session, as
codex's /model offers. Unset still sends nothing.

## Changes

- `core/config.py`: `OPENAI_EFFORT_LEVELS`, `effort_levels(protocol)`; the
  level is checked against the protocol after every layer.
- `core/backends/responses.py`, `core/backends/openai_compat.py`: an
  `effort` field, sent only when set.
- `cli/app.py`: every backend is built with the configured effort.
- `cli/repl.py`: `/effort`, and an `effort` row in `/status`.
- `core/session.py`: `effort_change`, synced.
- `tests/test_effort.py` (new, 39 tests).
- Docs: D-061; design (the command, the journal type); README (the config
  sample, the command list, the levels); roadmap item 22.

## Decisions and open points

- The model is not told of a change, and the system prompt stays as it was.
- A resume starts from the configured effort, as it does for `/model`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2293 passed, 1 skipped (2254 before, plus 39 in
  `tests/test_effort.py`), including a run through `main()` against the fake
  model server: `/effort high` reaches the next request body, `/new` keeps
  it, `/effort default` stops sending it.
- Mutation check (scratch script): 18 mutants -- the protocol check,
  the levels, each body and backend, the build, the command's case, default,
  same-level, journal, config, request and /new paths, the status row, the
  sync. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/effort`, from main at `b216d5c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_effort.py` |
<!-- auto:end -->
