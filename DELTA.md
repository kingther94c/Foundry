# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry mcp get NAME` (D-105), item 66: one MCP server's settings in full, as
`codex mcp get` -- its arguments, environment and address, which `list`
leaves out -- with a value a credential's name marks shown as `[hidden]`.

## Changes

- `cli/app.py`: `mcp get NAME [--json] [--profile P]`; the text and the
  listing's record with `command`, `args`, `env` and `url`; a name the
  configuration does not have refused with those it has. The listing's line
  under a server no longer escapes twice.
- `tests/test_mcp_get.py` (new, 29 tests); `tests/test_completion.py`
  offers `get` under `mcp`.
- Docs: D-105; README; design; threat model; roadmap item 66.

## Decisions and open points

- Hidden: an `env` value whose key looks like a credential, the value of an
  option so named (`--api-key X`, `--token=X`, `API_KEY=X`), a query value so
  named (its name read decoded); the rest goes through the redactor.
- An option so named that takes no value hides the argument after it: the
  cautious mistake.
- Not done: codex's `mcp login`/`logout` (OAuth).

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3408 passed, 1 skipped (3379 before, plus 29 in
  `tests/test_mcp_get.py`).
- Mutation check (scratch script): 45 mutants -- not dispatched, the
  profile ignored, the names it has unsaid, unsorted or said when empty, the
  JSON without the settings or followed by text, the header, the address,
  args, env, passed variables, the token's variable, tools, the remove hint
  unsaid or said empty, args unquoted, values read as markup, with control
  sequences or escaped twice, the listing escaped twice, the command, env,
  args or address unscrubbed, a credential's env, option or query value
  shown, plain words or every option hidden, hiding that never stops or
  starts, an encoded name read as written, a fragment dropped, no --json.
  All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-66`, from main at `4f9bfbc`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `tests/test_completion.py` |
| A | `tests/test_mcp_get.py` |
<!-- auto:end -->
