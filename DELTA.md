# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/review` of a branch, or of one commit (D-066), item 27: `/review --base REV`
reviews what this branch changed since it left REV, as a pull request shows
it (uncommitted work included), and `/review --commit REV` reviews one commit,
as codex's review presets do. `git_diff` gains `base` and `commit` to show
them; a revision is resolved to a full commit id before git sees it.

## Changes

- `core/tools/git.py`: `resolve_commit()`; `git_diff` takes `base` (merge
  base to the working tree) or `commit` (`git show`: message and patch), at
  most one of them and `staged`.
- `cli/repl.py`: `/review [--base REV | --commit REV] [focus]`; the scope
  line now comes before the review prompt (`REVIEW_UNCOMMITTED` for the old
  form); `prompts/review.md` no longer names the scope.
- `tests/test_review_base.py` (new, 27 tests);
  `tests/test_slash_commands.py`: the review text starts with its scope.
- Docs: D-066; design (git tools, read-only tasks); threat model (a revision
  read as an option); README; roadmap item 27.

## Decisions and open points

- A revision starting with `-`, or with a space or a control character, or
  over 200 characters, is refused before git runs (`--output=FILE` would
  write a file); only the resolved full id is passed on.
- The diff is from the merge base to the working tree, not `BASE...HEAD`:
  what is not yet committed is usually part of what the user wants reviewed.
- The task names the revision and its id, never the commit's message.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2370 passed, 1 skipped (2343 before, plus 27 in
  `tests/test_review_base.py`).
- Mutation check (scratch script): 19 mutants -- a leading dash, a
  space, the length cap, peeling to a commit, the id check, validation, one
  of three, the merge base, uncommitted work, no shared history, the message,
  the display, a flag without a revision, an unknown revision, the id in the
  task (twice), the focus, read-only, and the console line. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/review-base`, from main at `f633fce`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/tools/git.py` |
| M | `src/foundry/prompts/review.md` |
| A | `tests/test_review_base.py` |
| M | `tests/test_slash_commands.py` |
<!-- auto:end -->
