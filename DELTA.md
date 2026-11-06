# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What a command printed, in view (D-068), item 29: under each command the
model runs, its exit code and the last lines it printed, as codex shows
them -- the person watching sees the test results the model is reading.

## Changes

- `core/tools/command.py`: `output_tail()`; `run_command` keeps the last 20
  lines (4,000 characters) of its output in `metadata["output_tail"]`.
- `core/events.py`: `ToolEnd.output`; `core/runtime.py` fills it.
- `cli/render.py`: a command's exit code on its `ok`/`failed` line always;
  the last 5 lines of its output under it (all 20 with `-v`).
- `tests/test_command_output.py` (new, 16 tests).
- Docs: D-068; design (the event protocol); threat model (the terminal
  injection row); roadmap item 29.

## Decisions and open points

- After the command, not while it runs: `ToolOutputDelta` stays reserved
  for live output, which needs `run_process` to hand over output as it
  arrives.
- The output is stripped of control sequences and printed as plain text; the
  event sink scrubs it like every other string, so `exec --json` carries it
  scrubbed too.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2414 passed, 1 skipped (2398 before, plus 16 in
  `tests/test_command_output.py`).
- Mutation check (scratch script): 15 mutants -- the trailing blank
  lines, head instead of tail (twice), the character cap, the tool and the
  runtime dropping it, the exit code only with -v, every summary shown, the
  output not shown, not sanitized, -v cut too, no ellipsis, lines not cut,
  and markup read. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/command-output`, from main at `b3edbaf`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/events.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/command.py` |
| A | `tests/test_command_output.py` |
<!-- auto:end -->
