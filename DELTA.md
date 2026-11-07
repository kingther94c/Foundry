# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Reads that worked say nothing more; failures say why (D-076), item 37: a
read is one line, `-> read src/app.py`, not two; `failed` comes with its
reason; a patch refused in part names what it refused.

## Changes

- `cli/render.py`: `QUIET_WHEN_OK`; a read that worked adds nothing (`ok`
  with `-v`); a failure shows its summary.
- `core/tools/patch.py`: `metadata["rejections"]`.
- `core/runtime.py`: `_tool_end_summary` names a patch's first refusal and
  how many more.
- `tests/test_quiet_reads.py` (new, 21 tests); `tests/test_command_output.py`
  -- its "other tools" example is no longer a read.
- Docs: D-076; design (what goes under a tool's line); roadmap item 37.

## Decisions and open points

- Only Foundry's own read-only tools are quiet, checked against the default
  registry's kinds; an MCP tool says `ok` whatever it is called.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2586 passed, 1 skipped (2565 before, plus 21 in
  `tests/test_quiet_reads.py`).
- Mutation check (scratch script): 9 mutants -- reads saying ok,
  `-v` quiet too, failed reads quiet, failures unexplained, a read tool
  forgotten, a command made quiet, rejections not kept, how many more
  unsaid, the last refusal named instead of the first. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/quiet-reads`, from main at `ac98905`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/patch.py` |
| M | `tests/test_command_output.py` |
| A | `tests/test_quiet_reads.py` |
<!-- auto:end -->
