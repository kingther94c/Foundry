# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Lifecycle hooks, as codex's (D-115), item 76: `hooks.json` and `[hooks]`
from the user's own files and the machine's policy, in codex's format;
`PreToolUse` refuses, rewrites or adds a note before policy, `PostToolUse`
replaces a result or adds a note; `/hooks` lists them.

## Changes

- `core/hooks.py` (new): `Hook`, `parse_hooks`, `read_hooks_file`,
  `HookSet` (run together, through `cmd /D /S /C`, the command environment,
  a job object), `read_pre_tool_use`, `read_post_tool_use`, codex's names
  (`Bash`, `Edit`/`Write`), `scrubbed`.
- `core/tools/command.py`: `run_program`, run_process's body, taking a
  command line, an executable and stdin (written on a thread).
- `core/config.py`: `[hooks]` and the `hooks.json` beside `config.toml` and
  `policy.toml`; a repository's config may not set it.
- `core/runtime.py`: PreToolUse before policy (a refusal is step 1, rule
  `hook.PreToolUse`; a rewrite validated and checked from the start),
  PostToolUse before the reply, notes added in `_reply`, `hook` records;
  `core/session.py`: `EventType.HOOK`.
- `cli/app.py`: the hook set wired; `cli/repl.py`: `/hooks`.
- `tests/test_hooks.py` (new, 144 tests).
- Docs: D-115; README; design; threat model; roadmap item 76.

## Decisions and open points

- A hook never lets a call through; one that fails is a warning and the
  call goes on to policy, as codex's fail open.
- Two hooks rewriting a call differently refuse it.
- Not yet: codex's other events (refused by name), `mcp_tool` handlers,
  background `async`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3898 passed, 1 skipped (3754 before, plus 144 in
  `tests/test_hooks.py`).
- Mutation check (scratch script): 71 mutants over matching, codex's
  names, the table's checks and layers, running (the workspace search, the
  timeout, a stop, stdin), reading each answer, the notes and their cut,
  scrubbing, the runtime's refusal, rewrite and replacement, the journal,
  the wiring and `/hooks`. All but one are caught; that one (the notes' own scrub) is equivalent, since every reply is scrubbed again on its way into the context and the journal.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-76`, from main at `0c71139`.

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
| M | `src/foundry/core/config.py` |
| A | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| M | `src/foundry/core/tools/command.py` |
| A | `tests/test_hooks.py` |
<!-- auto:end -->
