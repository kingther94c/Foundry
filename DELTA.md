# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry mcp-server` keeps one task to a repository with its submodules
counted in, and loses no cancel (D-156, from P2): a task's place is the
outermost superproject's working tree, and a reply to a session held open
looks for a cancel again once its request is registered.

## Changes

- `cli/mcp_server.py`: `_place` asks `--show-superproject-working-tree` from
  the Git top level until it answers nothing, at most `MAX_SUPERPROJECTS`
  (8) times; `_git_line` runs one git question. `_take`'s held-open branch
  reads `rid in self._cancelled` under the lock once the request is
  registered, and cancels the runtime outside it if so.
- `tests/test_mcp_server_tasks.py` (7 tests).
- Docs: D-156; design (`foundry mcp-server`); threat model (its client's row).

## Decisions and open points

- The key is the outermost superproject, so a task in one submodule waits
  for a task in a sibling submodule: over-locking a little, seen as a
  refusal the client can retry.
- Open: a repository nested in another's working tree without being its
  submodule is still a place of its own; walking up parent folders would
  cover it, but would make every repository under a home folder kept in Git
  one place.

## Verification

- `tests/test_mcp_server_tasks.py` (7): a submodule, a folder in it, and its
  superproject are one place, and its clone elsewhere another; a submodule
  of a submodule is the outermost's; the walk is bounded; a place outside
  Git is itself; a task in the submodule is refused while one works in the
  superproject, and taken after; a folder in a repository is its top's
  place; a cancel that lands before a reply takes its session stops the task
  unanswered, with the model never asked, and the session goes on at the
  next reply; a cancel of another request leaves a reply be.
- `tests/test_mcp_server.py` and the new file: 71 passed.
- Mutation check: 12 mutants. One survived as equivalent: reading the
  cancel before registering the request, under the same lock, is the same
  read. One survived until a folder of a plain repository was asserted to
  be its top's place; then killed.
- Full suite: 4795 passed, 1 skipped (20 min 42 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
