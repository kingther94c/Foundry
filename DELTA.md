# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry-reply` sent the first task's `finish` to the model again with no
result, which every provider refuses (D-167, found in P9). Each task the
server runs now answers the calls it left as it ends, as the REPL does.

## Changes

- `cli/mcp_server.py`: `_run` answers the task's left calls.
- Tests: `tests/test_mcp_server.py` -- no call left unanswered in a reply's
  request, held open or resumed; what `finish` was answered, journaled.
- Docs: D-167; design (`foundry mcp-server`).

## Decisions and open points

- Answered as the task ends, not as a reply begins: the journal is then the
  same however the session goes on.

## Verification

- `tests/test_mcp_server.py`: 65 passed; the new test fails without the fix
  (`['f1']` left unanswered).
- Mutation check: 2 mutants -- the left calls not answered, and `finish`'s
  answer emptied -- both killed.
- Full suite: 4865 passed, 1 skipped, with D-168 stacked on it (20 min 0 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-reply-finish`, from main at `43a9fa5`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `src/foundry/cli/mcp_server.py` |
| M | `tests/test_mcp_server.py` |
<!-- auto:end -->
