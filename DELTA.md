# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What P1's second round found (D-149): an unattended run is offered only the
tools some call to could be allowed, its prompt says files with uncommitted
changes are refused, an empty path is the root for `list_files` and
`search_text`, `search_text` keeps indentation, command output reaches the
model with plain line ends, and the base prompt says calls in one reply run
in order.

## Changes

- `core/policy/engine.py`: `may_allow(tool, kind)` -- false for a read-only
  task's mutators and, where nobody can approve, for a tool no allow rule
  names, no grant covers and the mode does not allow.
- `core/runtime.py`: `_offered_tools()` filters through it (D-148's filter
  folded in).
- `core/prompts.py`: the dirty-file line when nobody can approve.
- `core/tools/files.py`: empty path is the root; search keeps indentation,
  200 characters after it.
- `core/tools/command.py`: `combined_output` makes Windows line ends plain
  ones (`cli/repl.py` no longer does it itself).
- `prompts/system.md`: calls in one reply run one at a time, in order.
- Docs: D-149, design (where nobody can approve), roadmap (P1's second round).

## Decisions and open points

- `may_allow` says yes whenever the outcome turns on a call's arguments: an
  allow rule naming the tool keeps it offered.
- Not changed, though the stand-in model raised them: the compaction request
  keeps its tools (D-040), a Stop hook's edit still makes earlier runs stale
  evidence, an untrusted workspace still sends no FOUNDRY.md (D-041), and a
  tool-call budget is not told to the model.

## Verification

- `tests/test_p1_round_two.py` (15): `may_allow` with and without someone
  to ask, by rule, pattern, grant and mode; the request an unattended run
  sends; a hidden tool called anyway; the dirty-file line; the order
  sentence; empty paths; indentation; line ends.
- The existing tests of the touched behaviour (70 files): 1938 passed.
- Mutation check: 18 mutants, all killed.
- Full suite: 4707 passed, 1 skipped.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/p1-round2`, from main at `2f82e93`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/policy/engine.py` |
| M | `src/foundry/core/prompts.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/command.py` |
| M | `src/foundry/core/tools/files.py` |
| M | `src/foundry/prompts/system.md` |
| A | `tests/test_p1_round_two.py` |
<!-- auto:end -->
