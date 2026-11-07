# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

PermissionRequest hooks, as codex's (D-119), item 80: an approval prompt
answered by the user's own program -- the call allowed once or refused --
only where someone would have been asked.

## Changes

- `core/hooks.py`: the event (exit code 2 a denial); `read_permission_request`
  and `_permission_answer`, codex's reserved fields failing closed;
  `HookVerdict.allow`.
- `core/runtime.py`: `_permission_request`, run at an ASK verdict when an
  approval callback exists, before the prompt; a denial refuses the call as
  a user's no, an allow runs it once (`approval` with `hook`).
- `tests/test_permission_hooks.py` (new, 44 tests); `tests/test_hooks.py`:
  the events named.
- Docs: D-119; README; design (pipeline step 6, journal); threat model (the
  first hook that lets a call through, and its bounds); roadmap item 80.

## Decisions and open points

- Not run in an unattended session, as codex's `never` asks none: a hook
  cannot turn an unattended refusal into a run.
- An allow is one call's; it grants nothing for the session.
- Not yet: `mcp_tool` handlers; background `async`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4093 passed, 1 skipped (4049 before, plus 44 in
  `tests/test_permission_hooks.py`).
- Mutation check (scratch script): 35 mutants over the event, exit
  code 2, the reader (shapes, reserved fields, async, the default denial),
  where it runs (ASK only, attended only), what it is told and matched on,
  a denial over an allow, and what an allow and a denial do at the call.
  All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-80`, from main at `8983620`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_hooks.py` |
| A | `tests/test_permission_hooks.py` |
<!-- auto:end -->
