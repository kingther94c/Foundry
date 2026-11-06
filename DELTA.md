# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Ctrl+C stops the turn, not the session (D-062), item 23: at the interactive
prompt a Ctrl+C while the model answers or a tool runs -- or (a)bort at an
approval, or a Ctrl+C during `/compact` -- ends that task and brings the
prompt back with the conversation kept, as codex does. Every call the task
left is answered, so the next request is valid, and the model is told it
was stopped.

## Changes

- `core/runtime.py`: `interrupted()`; `_answer_left()` on a cancelled
  ending (the running call as stopped, the rest as not run); `_running`;
  a cancellation is spent by the task it stops; `_reply` returns its ordinal.
- `core/context.py`: `unanswered_calls()`.
- `cli/repl.py`: `run_task` turns a Ctrl+C into `interrupted()`; a
  cancelled task keeps the session open and tells the model; `/compact`
  survives a Ctrl+C.
- `tests/test_interrupt_turn.py` (new, 16 tests).
- Docs: D-062; design (the cancellation chain); threat model (a row);
  README; roadmap item 23.

## Decisions and open points

- A stopped mutator other than `run_command` counts as a change; a stopped
  command does not, as a finished one does not.
- One-shot runs and `exec` still end on Ctrl+C; a Ctrl+C at the prompt
  still ends the session.
- What the model had streamed of an interrupted reply is not kept.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2309 passed, 1 skipped (2293 before, plus 16 in
  `tests/test_interrupt_turn.py`), including a resume of an interrupted
  session that rebuilds the live history with nothing to repair.
- Mutation check (scratch script): 19 mutants -- the catch, the
  session going on, the message and the note, the cancellation spent, the running
  call (set, cleared, matched by id), the answers (given, only when
  cancelled), the change rule, the tool end, the single ending, the
  unanswered-call scan, and `/compact`. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/interrupt-turn`, from main at `572bf90`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_interrupt_turn.py` |
<!-- auto:end -->
