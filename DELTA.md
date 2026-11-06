# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The newest line of a running command (D-074), item 35: while a command runs,
the working line shows the newest complete line it printed --
`running (2m 10s, Ctrl+C to stop): collected 340 items` -- so a long build
is visibly alive. `ToolOutputDelta` finally has a producer.

## Changes

- `core/tools/command.py`: `_drain` reads with `read1` (a pipe's `read`
  waited for 64 KB) and keeps each pipe's last 4 KB in a `PipeEnd`, past the
  capture budget too; `newest_line()`; `run_process(on_output=...)`, told at
  most every `OUTPUT_EVERY_S` and only when the line changed.
- `core/tools/base.py`: `ToolContext.on_output` in place of the unused `emit`.
- `core/runtime.py`: `_execute` sets the call's teller, emitting
  `ToolOutputDelta`, and clears it however the call ends.
- `cli/render.py`: `Working.latest`, cut to the screen's width; `handle`
  passes a `ToolOutputDelta` to the working line without taking it down.
- `tests/test_live_output.py` (new, 25 tests).
- Docs: D-074; design (the event list); roadmap item 35; threat model.

## Decisions and open points

- Only complete lines leave, so the redactor sees each credential whole; a
  window that has filled drops its first line; NULs are removed.
- Open: a program that buffers its output into a pipe (Python without `-u`)
  shows lines only as it flushes them; the child's environment is unchanged.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2547 passed, 1 skipped (2522 before, plus 25 in
  `tests/test_live_output.py`).
- Mutation check (scratch script): 17 mutants -- `read` for `read1`,
  the end not kept past the budget, any chunk ending a line, a line not yet
  ended passed on, a full window keeping its first line, NULs kept, a
  progress bar's redraws taken as one line, the same line told again, no
  half second between, stdout always, the tool or the runtime not passing it
  on, the teller kept after the call, the line taken down for it, control
  sequences kept, a long line wrapping, the line not shown.
- By hand: a real PowerShell loop printing a line every 0.4 s is told
  `line 1`, `line 3`, `line 4`, `line 6`, then its stderr line.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/live-output`, from main at `480123e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/base.py` |
| M | `src/foundry/core/tools/command.py` |
| A | `tests/test_live_output.py` |
<!-- auto:end -->
