# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A configuration file is read as Windows editors and shells save it
(D-202), the first item of P15, taken ahead for the first run on the
company machine. Notepad and PowerShell 5.1's `Set-Content -Encoding utf8`
begin a UTF-8 file with a byte-order mark, which tomllib called a syntax
error on line 1; PowerShell 5.1's `>` writes UTF-16, which ended in a
traceback, as did a file in the system's code page. Now a mark is taken
off, and the other two are refused by name, with how to save the file
again.

## Changes

- `core/config_text.py` (new): `read_config_text`, the one reader of a
  configuration file's text.
- Its callers: `core/config.py` (`_load_toml`: the user's, the repository's
  and the machine's file), `core/hooks.py` (`read_hooks_file`),
  `core/config_edit.py` (`_read`, for `foundry mcp add/remove` and
  `foundry features`), `cli/wiring.py` (the "always" writer, which reads line
  ends as text mode does).
- `core/runtime.py`: an "always" whose file cannot be read warns and grants
  for the session, as one whose file cannot be written did.
- Tests: `tests/test_config_encoding.py` (14, new).
- Docs: D-202; `design.md` code map; `requirements.md`, the configuration's
  encoding; `roadmap.md`, status, P15 and the suite's size.

## Decisions and open points

- UTF-16 is refused, not read: Foundry writes to the user's file, and one
  read as UTF-16 and written back as UTF-8 would change under the user
  without a word. The message costs one save.
- An edit writes UTF-8 without the mark it read.
- Open: nothing of this has met the company machine yet.

## Verification

- Full suite as CI runs it: run on the stack's tip, with D-203 above this;
  D-203's notes give the result. Collected here: 5,203, the figure the
  roadmap names (5,202 before the stack moved onto 8a81793).
- `tests/test_config_encoding.py`: 14 passed. On main's readers (a copy of
  d97460c with only the new reader added), 13 of the 14 fail; the one that
  passes tests the reader alone.
- The neighbouring suites (features, hooks, approvals, MCP commands, docs
  truth, the Anthropic wiring, retention): passed. One of them,
  `tests/test_approval_shows.py`, caught the CR the "always" writer gained
  before its line ends were read as text mode reads them.
- Mutation check (`verify/mutate.py`): 12 mutations of the reader, its four
  callers and the runtime's warning -- the mark kept, unmarked UTF-16 or a
  mark alone not seen, the line counted from zero, the byte before named,
  the PowerShell hint reading and writing at once, each caller reading as
  before, a CRLF kept, the warning not caught -- all killed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `8a81793`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/wiring.py` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/config_edit.py` |
| A | `src/foundry/core/config_text.py` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/fixtures/live_gateway/foundry_chat_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_stream.raw` |
| A | `tests/test_config_encoding.py` |
| A | `tests/test_gateway_wire_shapes.py` |
| M | `tests/test_reasoning.py` |
| M | `tests/test_verify_kit.py` |
| M | `tests/test_verify_probes.py` |
| M | `verify/.gitignore` |
| M | `verify/README.md` |
| A | `verify/probes/check_c.py` |
| A | `verify/probes/make_projects_c.py` |
| M | `verify/probes/run_auto.py` |
<!-- auto:end -->
