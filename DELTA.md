# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Resuming as codex spells it (D-088), item 49: `foundry resume --last
[TASK]`, `foundry resume --all` (every workspace's sessions, each continued
where it was recorded) and `foundry exec resume [--last | SESSION] [TASK]`.

## Changes

- `core/resume.py`: `recent_sessions` and `latest_session` take None for
  every workspace; `recorded_workspace`; a summary carries its workspace.
- `cli/app.py`: `_choose_resume` (`--last`, `--all`); the picker shows where
  each session was when it lists every workspace's; `exec_resume` rewrites
  the exec form, reading which options take a value from the parser.
- `tests/test_resume_spellings.py` (new, 29 tests).
- Docs: D-088; README; requirements; roadmap item 49.

## Decisions and open points

- With `--last`, a word is the task; an id and a task is refused.
- `--all` continues a session only where it was recorded; one naming no
  workspace is refused.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2831 passed, 1 skipped (2802 before, plus 29 in
  `tests/test_resume_spellings.py`).
- Mutation check (scratch script): 20 mutants -- None listing
  nothing, a session naming no workspace listed, a summary without its
  workspace, `--last` taking the word for an id or ignored or taking an id
  and a task, the picker offering this workspace's or hiding where, `--all`
  ignored or its newest this workspace's, a missing session crashing, no
  workspace taken, continued in this workspace, a task after `--` read as
  the form, option values read as words, a value `--last` taken for the
  flag, `resume` inside a task, `exec resume` alone taken as the newest, a
  dashed task read as an option, `main` never rewriting. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/resume-spellings`, from main at `b3b688a`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/resume.py` |
| A | `tests/test_resume_spellings.py` |
<!-- auto:end -->
