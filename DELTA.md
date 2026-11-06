# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A resume picker (D-057), item 18, as `codex resume` has: at a terminal,
`foundry resume` with no id lists this workspace's ten newest sessions --
id, how each ended, in use or not, the first line of its first message --
and asks which; Enter is the newest, Ctrl+C resumes none. Not at a
terminal it is `last`, as before. `foundry sessions` shows each session's
first message too.

## Changes

- `core/resume.py`: `SessionSummary`, `summarize`, `recent_sessions`;
  `latest_session` now shares `_workspace_sessions` with them.
- `cli/app.py`: `pick_session`, `_interactive`, `_session_line`,
  `_preview`; `resume`'s id defaults to none, and `cmd_session` asks;
  `cmd_sessions` prints the shared line.
- `tests/test_resume_picker.py` (new, 17 tests).
- Docs: D-057; README (the resume and sessions rows); roadmap item 18.

## Decisions and open points

- Only this workspace's sessions: resume refuses another's anyway.
- `exec --resume` never asks.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2222 passed, 1 skipped (2205 before, plus 17 in
  `tests/test_resume_picker.py`).
- Mutation check (scratch script): 19 mutants -- this workspace,
  newest first, the first message, in use, the limit, one session is no
  choice, Enter, the number range and pick, spaces, end of input, a script
  not asked, none chosen, the preview's cut, first line and placeholder, the
  listing, and the argument's default. All are caught; one more ("run never
  asks", without the command check) is equivalent, since `run` has no
  session argument.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/resume-picker`, from main at `3f2af7e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/resume.py` |
| A | `tests/test_resume_picker.py` |
<!-- auto:end -->
