# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

codex's command-line flags (D-097), item 58: `--full-auto`, `-a`, `-s`,
`--yolo`, `--skip-git-repo-check`, `--search`, `--oss`, `--add-dir` and
`--color` are refused with what Foundry takes instead, and the usage shown is
the subcommand's; `-p` (`--profile`) and `-V` (`--version`) are taken.

## Changes

- `cli/codex_flags.py` (new): the flags, their spellings, and the advice for
  each, read with its value where the value decides it.
- `cli/app.py`: `_Parser.parse_args` adds the hints to the refusal and shows
  the subcommand's usage; `-p` on `--profile`; `-V` on `--version`, which
  `main` no longer sends to `run`.
- `tests/test_codex_flags.py` (new, 176 tests).
- Docs: D-097; README; roadmap item 58.

## Decisions and open points

- Hints, not aliases: with no sandbox none of them means here what it means in
  codex.
- Only a line argparse already refuses is looked at; words after `--` are the
  task's.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3193 passed, 1 skipped (3017 before, plus 176 in
  `tests/test_codex_flags.py`).
- Mutation check (scratch script): 15 mutants -- no hint, the task's
  words read as flags, a flag told twice, the next word never its value or a
  flag taken as one, exec told as if someone answers, `-a never` and
  `-s read-only` told generically, `-sread-only` and `--flag=value` not read,
  the top-level usage, the parser never asking, `-p`/`-V` not taken, `-V`
  sent to run. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/codex-flag-hints`, from main at `2d1adac`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/codex_flags.py` |
| A | `tests/test_codex_flags.py` |
<!-- auto:end -->
