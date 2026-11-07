# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry run TASK` goes on after the task (D-078), item 39: at a terminal
the task is the session's first message, as if typed, and the prompt comes
back, as `codex "task"` does. Not at a terminal, the run ends with the task
as before.

## Changes

- `cli/repl.py`: `repl(..., first=None)` takes the first message as typed.
- `cli/app.py`: `cmd_session` passes the task to the REPL at a terminal, once
  (not again after `/new`); the one-shot path only when nobody is there; the
  `task` argument's help.
- `tests/test_run_continues.py` (new, 7 tests); `tests/test_cli_flags.py`
  -- its `_repl` stand-in takes the first message.
- Docs: D-078; README (the command table); roadmap item 39.

## Decisions and open points

- A change of behaviour at a terminal: a script that ran `foundry run TASK`
  from an interactive console now gets the prompt after the task; `exec` is
  the unattended way, and a piped or closed stdin still ends with the task.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2599 passed, 1 skipped (2592 before, plus 7 in
  `tests/test_run_continues.py`).
- Mutation check (scratch script): 5 mutants -- the first message
  ignored, one task then the end always, never one-shot, sent again after
  `/new`, the task not passed on. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/run-continues`, from main at `21cb76f`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `tests/test_cli_flags.py` |
| A | `tests/test_run_continues.py` |
<!-- auto:end -->
