# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A review is told a new file is not in `git_diff` (D-179, P11): told that
`git_status` and `git_diff` "show what changed", a review's model found a new
file only because `git_status` listed it.

## Changes

- `cli/slash_commands.py`: `REVIEW_UNCOMMITTED`, `REVIEW_AS_ASKED` and the
  `--base` scope say a new file is not in the diff and to read it.
- `core/tools/git.py`: `git_diff`'s description says so, and that
  `git_status` lists those files.
- Tests: `tests/test_review_command.py` (each scope says it);
  `tests/test_tools_command_git.py` (the description says it, and git does
  leave a new file out while `git_status` lists it).
- Docs: D-179; design (review).

## Decisions and open points

- Found by the fifth round (s20), in a `/review` after `/init` wrote
  `FOUNDRY.md`.
- `git_diff` itself is unchanged: every other use of it shows what it did.

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (4, all killed): each scope's old wording; the description's
  old wording.
- Full suite as CI runs it: 4889 passed, 1 skipped (20 min 55 s), before
  the docs; with them, the docs' own tests pass.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
