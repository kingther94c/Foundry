# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

PreCompact and PostCompact hooks, and SessionStart's `compact` source, as
codex's (D-118), item 79: a compaction kept from happening or the task ended
after one, and the start hooks run again before the next request.

## Changes

- `core/hooks.py`: the two events; `read_pre_compact` and
  `read_post_compact` (`_read_compact`); `_Reading.failed`, shared with
  SessionEnd's reader.
- `core/runtime.py`: `compact` runs PreCompact before the summary request
  and PostCompact after the `compacted` record, keeping a stop in
  `_compact_stop`, which the loop ends the task `blocked` on; the starts owed
  are now a queue, `start_sources`, with `compact` added after each
  compaction and run before the task's next request (`_start_hooks`);
  `_said` for a hook's reasons.
- `cli/app.py`: `start_sources`.
- `tests/test_compact_hooks.py` (new, 33 tests); `tests/test_hooks.py`:
  the events named.
- Docs: D-118; README; design; threat model; roadmap item 79.

## Decisions and open points

- A PostCompact stop after `/compact` has no task to end; it is shown as a
  warning.
- After `/compact`, the `compact` start runs at the next prompt, ahead of its
  UserPromptSubmit hooks, as a session's first start does.
- Not yet: PermissionRequest; `mcp_tool` handlers; background `async`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4049 passed, 1 skipped (4016 before, plus 33 in
  `tests/test_compact_hooks.py`).
- Mutation check (scratch script): 46 mutants over the events, the
  shared failure reading, the compact reader, what each hook is told and
  matched on, what a stop does before and after the summary, by hand and in
  a task, the start owed (queued, in order, its note and its stop) and the
  CLI's wiring. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-79`, from main at `1a76154`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_compact_hooks.py` |
| M | `tests/test_hooks.py` |
<!-- auto:end -->
