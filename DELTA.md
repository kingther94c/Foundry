# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

How long the answers are, as codex's `model_verbosity` (D-090), item 51:
`[backend] verbosity = "low" | "medium" | "high"`, sent as `text.verbosity`
on the Responses API and `verbosity` on Chat Completions; unset sends
nothing, and `anthropic_messages` refuses it when the configuration loads.

## Changes

- `core/config.py`: `VERBOSITY_LEVELS`; `BackendConfig.verbosity`, checked in
  `_validate_backend_setting`; `load_config` refuses it for Claude.
- `core/backends/responses.py`, `core/backends/openai_compat.py`:
  `build_body(verbosity=)` and a `verbosity` field on each backend.
- `cli/app.py`: `build` gives it to the OpenAI backends; `cli/repl.py`:
  `/status` shows it when set.
- `tests/test_verbosity.py` (new, 32 tests).
- Docs: D-090; README; design; roadmap item 51.

## Decisions and open points

- Refused for Claude rather than dropped: a setting that does nothing reads
  as one that works. The message points at a profile.
- No slash command: codex has none either.
- Seen in passing, for later: a key directly under `[profiles.NAME]` (codex's
  flat spelling) or an unknown key under `[backend]` is skipped without a
  word.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2882 passed, 1 skipped (2850 before, plus 32 in
  `tests/test_verbosity.py`).
- Mutation check (scratch script): 15 mutants -- either protocol never
  sending it or spelling it as the other does, either backend dropping it,
  any level taken, medium refused, Claude taking it silently or refusing it
  unset, build never passing it, passing it to Claude or passing none,
  `/status` never or always showing it. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/model-verbosity`, from main at `6e13c02`.

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
| A | `tests/test_verbosity.py` |
<!-- auto:end -->
