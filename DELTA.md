# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Background hooks, as codex's (D-120), item 81: an `async` hook is started
and not waited for; what it says is heard at the next safe point -- before
the task's next model request, or with the next prompt -- and the session's
close ends one still running.

## Changes

- `core/hooks.py`: `HookSet.run` starts background hooks on threads of
  their own (`_start`) and returns only the runs it waited for;
  `finished`, `settle`, `close`; a SessionEnd hook runs in step whatever
  `async` says.
- `core/runtime.py`: `_background_notes` at the prompt (once it is let
  through) and at the top of each round before the request;
  `_heard` (what `_run_hooks` said, shared, marking `async` records);
  `_hook_message`, shared with SessionStart after a compaction;
  `end_hooks` closes the set.
- `tests/test_async_hooks.py` (new, 11 tests, with a hook that waits on
  a gate the test opens).
- Docs: D-120; README; design; threat model; roadmap item 81.

## Decisions and open points

- A stopped task does not end a background hook, as codex's goes on; only
  the session's close does, and what it would have said is dropped.
- A refused prompt takes none of what they said, so the next prompt has it.
- Not yet: `mcp_tool` handlers.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4104 passed, 1 skipped (4093 before, plus 11 in
  `tests/test_async_hooks.py`).
- Mutation check (scratch script): 19 mutants over starting and not
  waiting, handing each run over once, the close, SessionEnd in step, where
  and in what order the notes go, the records and the warnings. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-81`, from main at `1bf8a11`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_async_hooks.py` |
<!-- auto:end -->
