# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`git_diff` shows a CRLF file's lines as `read_file` does (D-174, P10): in
the fourth round a review's model read a blank line after every line of a
diff whose lines ended in CRLF.

## Changes

- `core/tools/git.py`: `GitDiff.execute` turns CRLF into LF in what it
  shows and adds `CRLF_NOTE` under it; an LF diff is unchanged.
- Tests: `tests/test_tools_command_git.py` (a CRLF file's uncommitted change
  and a commit's, shown without carriage returns and with the note; an LF
  diff without it).
- Docs: D-174; design (`git_diff`).

## Decisions and open points

- Found by the fourth round (s17): the model saw a blank line after every
  line of the diff.
- Only CRLF is turned into LF; a lone carriage return is shown as git gives it.

## Verification

- The new tests pass; without the change the CRLF ones fail.
- Mutations (3, all killed): the carriage returns kept; no note; the note
  under every diff.
- Full suite as CI runs it, with D-175 on top: 4878 passed, 1 skipped,
  1 failed (21 min 14 s) -- D-173's `test_a_sub_agent_is_told_them_too`,
  fixed as D-173 landed; 4879 pass with it.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
