# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Tab completion for the shell (D-093), item 54, as `codex completion`:
`foundry completion [powershell|bash]` prints a script built from the
argparse parser -- subcommands, flags, `--mode`'s values, an argument's fixed
values -- that leaves paths, names and anything after `--` to the shell.

## Changes

- `cli/completion.py` (new): `command_tree`, `script`, the PowerShell and
  bash templates.
- `cli/app.py`: the `completion` subcommand; `cmd_completion` writes bytes,
  so the bash script keeps LF on Windows.
- `tests/test_completion.py` (new, 29 tests): the tree, the command,
  and each shell driven for real (`TabExpansion2` in PowerShell, `COMP_WORDS`
  in Git Bash; skipped where the shell is missing).
- Docs: D-093; README command table; roadmap item 54.

## Decisions and open points

- PowerShell is the default (Windows); bash for Git Bash. zsh and fish
  are not offered.
- Before a subcommand, run's flags are offered too (`foundry -m x` is
  `run`).

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2958 passed, 1 skipped (2929 before, plus 29 in
  `tests/test_completion.py`).
- Mutation check (scratch script): 22 mutants on the tree, each
  shell's walk (a flag's value read as a subcommand, past `--` completed, no
  values after a flag, a value completed as a subcommand, no flags, no
  argument values, never descending) and the command (printed as text with
  CRLF, bash the default). All are caught (the one past `--` in PowerShell after its test was changed: PowerShell never calls a completer for a bare `-`).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/shell-completion`, from main at `2d3d2e9`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/completion.py` |
| A | `tests/test_completion.py` |
<!-- auto:end -->
