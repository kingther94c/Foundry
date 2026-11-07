# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

When automatic compaction starts (D-094), item 55, as codex's
`model_auto_compact_token_limit`: `[runtime] auto_compact_token_limit = N`
compacts once the context passes N tokens or 80% of the window, whichever is
first; `/status` names the threshold in force.

## Changes

- `core/context.py`: `ContextManager.compact_above_tokens`,
  `compaction_threshold()`; `needs_compaction` uses it.
- `core/config.py`: `Config.auto_compact_token_limit`, parsed from
  `[runtime]` (at least 1,000; a repository may not set it); in
  `_RUNTIME_KEYS`; codex's name in `_CALLED_HERE`.
- `cli/app.py`: `build` hands it to the context; `cli/repl.py`: `/status`.
- `tests/test_compact_limit.py` (new, 24 tests).
- Docs: D-094; README; design; roadmap item 55.

## Decisions and open points

- Only ever sooner: a limit past the window would leave a task to run out.
- A repository may not set it: compacting early summarises away what the
  user was reading.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2982 passed, 1 skipped (2958 before, plus 24 in
  `tests/test_compact_limit.py`).
- Mutation check (scratch script): 14 mutants -- the limit making it
  later or ignored, at the limit counted as past it, a repository setting it,
  any value or a tiny one taken, never set, refused by `-c` and the file,
  codex's name not told, build dropping it, `/status` claiming it when off,
  never saying it, or showing the setting instead of the threshold (caught
  after a test was added). All are caught but one that is equivalent: `true`
  taken as the integer 1, which the floor of 1,000 refuses anyway.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/compact-limit`, from main at `f312d3c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/context.py` |
| A | `tests/test_compact_limit.py` |
<!-- auto:end -->
