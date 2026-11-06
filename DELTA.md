# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`exec -` reads the task from stdin (D-053), item 14, as `codex exec -`
does: a long task, or one with quotes Windows would mangle on a command
line, is piped in whole. At most 1 MB; a byte-order mark decides the
encoding (PowerShell 5.1 files are UTF-16), else UTF-8 or the console's code
page; a terminal, no stdin or a blank one is refused with exit 2.

## Changes

- `cli/app.py`: `_task_from_stdin`, read when the task is `-`; the task
  argument's help says so.
- `tests/test_exec_stdin.py` (new, 13 tests).
- Docs: D-053 (with a note on PowerShell 5.1's `$OutputEncoding`); README
  (the exec row); roadmap item 14.

## Decisions and open points

- Only an explicit `-` reads stdin: a script that forgets its task should
  fail, not hang on an open pipe.
- A task on the command line never reads stdin.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2152 passed, 1 skipped (2139 before, plus 13 in
  `tests/test_exec_stdin.py`).
- Through `main()` against the fake model server: plain, quotes and
  non-ASCII with CRLF, a UTF-8 mark, UTF-16 little- and big-endian -- the
  model receives the task as written.
- Mutation check (scratch script): 11 mutants -- the terminal and
  missing-stdin refusals, the size cap and the byte past it, the marks read
  and dropped, line ends, trimming, the blank refusal, `-` itself, and a
  text-only stream. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/exec-stdin`, from main at `54e8873`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_exec_stdin.py` |
<!-- auto:end -->
