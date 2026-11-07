# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry mcp-server` (D-103), item 64: Foundry as an MCP server over stdio, as
`codex mcp-server`. Another agent or an editor gets `foundry`, which runs a
task as exec does -- nothing approved, fail-closed -- and answers with its last
word and `sessionId`, and `foundry-reply`, which gives that session another
task.

## Changes

- `cli/mcp_server.py` (new): the protocol (`Server`), a thread per
  `tools/call`, cancels, progress, one task per repository, sessions held open
  for replies (or resumed from the journal), and stdin/stdout kept for the
  protocol alone.
- `cli/app.py`: the `mcp-server` subcommand; exec's set-up for a session nobody
  watches (`unattended`, `announce_start`, `NO_FINISH`) shared with it; the
  runtime's cancel flag reaches long-running tools (`ToolContext.cancelled`).
- `core/runtime.py`: a cancel is checked between the pieces of a streamed
  answer and before each tool call, not only after one.
- `core/config.py`: `settings_layer`, the `-c` settings from typed values.
- `tests/test_mcp_server.py` (new, 64 tests).
- Docs: D-103; README; design; threat model; roadmap item 64.

## Decisions and open points

- A task runs as exec's: what the mode would ask about is refused; `mode`
  cannot be `default`, and a configured mode never decides.
- A cancelled request is not answered; the session goes on with a reply.
- One task at a time in a repository; at most 8 sessions held open.
- Not done: approvals asked of the client (codex's elicitation), codex's
  `codex/event` notifications, images, `--output-schema`, HTTP.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3349 passed, 1 skipped (3285 before, plus 64 in
  `tests/test_mcp_server.py`).
- Mutation check (scratch script): 57 mutants -- a configured mode
  deciding, the protocol version, batches, ids, params, cancels ignored or
  lost (before a task began, while its session was built, after it ended),
  arguments, prompts, modes and config taken unchecked, cwd, two tasks in one
  repository or a folder taken for another, which session is closed and when,
  the model, profile and settings asked for, someone left to ask, the start,
  the resume note, a prose ending unrecorded or unshown, errors, reasons, a
  cancelled request answered, sessions left open or holding their journals as
  the client goes, a broken session kept, progress, message size, blank lines,
  stdin and stdout shared with what Foundry starts, a bad profile found late,
  the runtime's two new cancel checks and the tools' cancel. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-server`, from main at `9d7509c`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/mcp_server.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/test_mcp_server.py` |
<!-- auto:end -->
