# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`!COMMAND` at the prompt (D-055), item 16, as codex's prompt has it: the
user runs a command in the workspace without being asked -- it is theirs --
and the model is told what it printed, scrubbed and cut to a tool result's
size, with the next message. It runs as the model's commands do (workspace
root, minimal environment, time limit, Ctrl+C ends the tree), is journaled
as `user_command`, and counts as a change, so no command run before it can
be cited in finish; nor can it.

## Changes

- `cli/repl.py`: `run_shell`, `SHELL`, `SHELL_USAGE`, `SHELL_NOTE`; the REPL
  sends a line starting with `!` there; `/help` says so.
- `core/tools/command.py`: `combined_output`, shared with `run_command`.
- `core/session.py`: `EventType.USER_COMMAND`.
- `core/runtime.py`: `files_changed`'s docstring names the new caller.
- `tests/test_user_shell.py` (new, 21 tests).
- Docs: D-055; threat model (two rows); README (the prompt paragraph);
  roadmap item 16.

## Decisions and open points

- The model's command environment, not the user's full one: the output goes
  to the provider.
- Ctrl+C: recorded, and the model is not told.
- Which files it changed is not known, so the reads the model made stand;
  a patch still has to match the file as it is.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2178 passed, 1 skipped (2157 before, plus 21 in
  `tests/test_user_shell.py`).
- Mutation check (scratch script): 21 mutants -- the `!` itself, the
  trimmed command, the bare `!`, workspace root, time limit, environment,
  journaling (run, stopped, not started), the evidence reset, scrubbing of
  output and command, line ends, the size cut, no output, the note, the
  screen, the timeout, blank stderr, `run_command`'s stderr, and `/help`. All
  are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/user-shell`, from main at `fedb3a8`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| M | `src/foundry/core/tools/command.py` |
| A | `tests/test_user_shell.py` |
<!-- auto:end -->
