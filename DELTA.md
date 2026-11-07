# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

SessionStart, SessionEnd and Interrupt hooks, as codex's (D-117), item 78:
a note or a stop at a session's first prompt (`startup`, `resume`); told when
a running task is stopped and when the session closes.

## Changes

- `core/hooks.py`: the three events; `read_session_start`,
  `read_session_end`, `read_interrupt`; SessionEnd and Interrupt given a
  second, three at most (`SHORT_EVENTS`); exit code 2 a decision only where
  codex makes it one.
- `core/runtime.py`: `start_source`, run once in `_prompt_hooks` ahead of
  UserPromptSubmit (notes by event); `interrupt_hooks` at a cancelled
  `_terminate`; `end_hooks`; `_hook_payload` leaves out what codex does not
  tell an event; `_run_hooks(stoppable=False)` for hooks after a cancel.
- `cli/app.py`: `start_source` set (`resume` for a resumed session);
  `_close` runs the SessionEnd hooks before the journal closes; `exec`'s
  Ctrl+C runs the Interrupt hooks while its task ran.
- `cli/repl.py`: `/hooks`'s description.
- `tests/test_session_hooks.py` (new, 46 tests); `tests/test_hooks.py`:
  the refused events and `/hooks` wording.
- Docs: D-117; README; design; threat model; roadmap item 78.

## Decisions and open points

- A codex timeout over three seconds for SessionEnd or Interrupt is cut to
  three, as codex cuts it, rather than refused, so its files load.
- Not yet: PreCompact, PostCompact and SessionStart's `compact` source;
  PermissionRequest; `mcp_tool` handlers; background `async`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  4016 passed, 1 skipped (3970 before, plus 46 in
  `tests/test_session_hooks.py`).
- Mutation check (scratch script): 46 mutants over the events, their
  matchers and timeouts, exit code 2, each reader, what each event is told,
  the start (once, its source, its stop, its notes' place), the stop and
  the end (run to their end after a cancel, a Ctrl+C at the end) and the
  CLI's wiring. All are caught (one survivor, a SessionStart hook read as a prompt's, caught after a test of its exit code 2 was added).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-78`, from main at `be108a8`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_hooks.py` |
| A | `tests/test_session_hooks.py` |
<!-- auto:end -->
