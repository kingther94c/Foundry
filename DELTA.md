# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`cli/repl.py` split (D-160, P3): the loop stays; the slash commands, what a
message attaches, and the commands for the session's settings go to modules
of their own, moved as they were, with no change of behaviour.

## Changes

- `cli/repl.py` (1,709 lines -> 360): the prompt, the loop, running a task,
  `!` commands, side questions.
- New: `cli/slash_commands.py` (the table, the commands, `RunTask`,
  `Command`, the ends of a session), `cli/attachments.py` (images, the
  clipboard, `@` files, `$` skills), `cli/session_settings.py` (`/mode`,
  `/model`, `/effort`, `/skills`, `/hooks`, `/mcp`).
- Imports elsewhere (`cli/session_commands.py`, `cli/unattended.py`,
  `cli/mcp_server.py`, the tests) point at the new modules; each patch of
  `repl.NAME` patches the module that looks the name up.
- Docs: D-160; design (the package tree); roadmap (P3); the threat model's
  links to moved functions.

## Decisions and open points

- The values a command hands the loop live with the commands, so nothing
  imports the loop.

## Verification

- Every patch the tests point at a new module is reached (`reach` check):
  20 of 21. The one not reached, `tests/test_mention_names.py:123`, asserts
  the patched function is never called; the next test patches the same
  target and reaches it.
- Mutation check: 10 mutants of the moved code -- read-only after a task,
  an empty clipboard, the mention and skill limits, a side question and a
  review read-only, delete asking, an unknown command, a mode change
  journaled, the model list cut -- all killed.
- Full suite: 4808 passed, 1 skipped, on the branch before it was
  rebased and again on the four splits stacked (22 min 38 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/p3-split-repl`, from main at `272b63f`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| A | `src/foundry/cli/attachments.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/cli/session_commands.py` |
| A | `src/foundry/cli/session_settings.py` |
| A | `src/foundry/cli/slash_commands.py` |
| M | `src/foundry/cli/unattended.py` |
| M | `tests/test_cli_flags.py` |
| M | `tests/test_clipboard_image.py` |
| M | `tests/test_codex_commands.py` |
| M | `tests/test_codex_commands_rest.py` |
| M | `tests/test_compact_hooks.py` |
| M | `tests/test_effort.py` |
| M | `tests/test_features.py` |
| M | `tests/test_file_links.py` |
| M | `tests/test_fork.py` |
| M | `tests/test_hooks.py` |
| M | `tests/test_images.py` |
| M | `tests/test_live_output.py` |
| M | `tests/test_markdown.py` |
| M | `tests/test_mcp_cli.py` |
| M | `tests/test_mcp_command.py` |
| M | `tests/test_mention_names.py` |
| M | `tests/test_mentions.py` |
| M | `tests/test_model_list.py` |
| M | `tests/test_patch_diff.py` |
| M | `tests/test_prompts_namespace.py` |
| M | `tests/test_quiet_reads.py` |
| M | `tests/test_resume_transcript.py` |
| M | `tests/test_review_base.py` |
| M | `tests/test_review_command.py` |
| M | `tests/test_rewind.py` |
| M | `tests/test_side.py` |
| M | `tests/test_slash_commands.py` |
| M | `tests/test_small_commands.py` |
| M | `tests/test_user_shell.py` |
| M | `tests/test_worked_for.py` |
| M | `tests/test_working.py` |
<!-- auto:end -->
