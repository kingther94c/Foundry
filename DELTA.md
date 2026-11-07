# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry policy check COMMAND` (D-135), item 96, as `codex execpolicy
check`: what the policy would decide for a command here, at a terminal and
unattended, without running it.

## Changes

- `cli/app.py`: `cmd_policy` and the `policy check` parser.
- `tests/test_policy_check.py` (new, 11 tests).
- Docs: D-135; README; roadmap item 96.

## Decisions and open points

- Nothing is asked: an untrusted repository's rules are left out, and it
  says so. Hooks are not run.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4444 passed, 1 skipped (4433 before, plus 11 in
  `tests/test_policy_check.py`).
- Mutation check (scratch script): 12 mutants over the evaluation,
  trust and what is shown. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-96`, from main at `99d889f`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_policy_check.py` |
<!-- auto:end -->
