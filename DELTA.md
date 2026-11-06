# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Slash commands and custom prompts (D-039), the first step of the codex-parity
order now in the roadmap. The interactive prompt has a command table:
`/help /status /diff /plan /approve /undo /review [focus] /init /new /exit`.
An unknown `/name` is reported instead of being sent to the model; a path such
as `/usr/bin/python fails` is still a task. Custom prompts are Markdown files
in `~/.foundry/prompts/` and, in a trusted workspace, `.foundry/prompts/`,
with `$ARGUMENTS`, `$1`..`$9` and `$$`. `/review` runs read-only in every
mode. Building it found that plan mode refused changes only after the ASK
rules; it now refuses at step 2.

## Changes

- `core/commands.py` (new): `parse_command`, `builtin_prompt`,
  `CustomPrompt`, `load_custom_prompts` (user over repository; repository only
  when trusted; 24 KB cap; front matter), `expand`.
- `prompts/review.md`, `prompts/init.md` (new): the texts `/review` and
  `/init` send.
- `cli/repl.py` (new): the REPL moved out of `app.py`, with the `COMMANDS`
  table, `/status`, `/diff`, `/review`, `/init`, `/new`, custom prompts, and
  `run_task`, which sets and clears `policy.read_only` around a turn.
- `core/policy/engine.py`: `read_only` field; plan mode and read-only tasks
  refuse mutators at step 2, after the DENY rules and before any ASK.
- `cli/app.py`: wiring carries `home`, `trusted`, `prompts`; `cmd_session`
  starts a new session on `/new`; a repository with `.foundry/prompts/` gets
  the trust question.
- `tests/test_slash_commands.py` (new, 39 tests).
- Docs: D-039; design.md sections 1 and 6; requirements.md, the plan mode
  baseline; threat-model rows; README; roadmap "Toward codex parity".

## Decisions and open points

- Unknown commands are not sent to the model: a typo would start a paid turn.
- `/review` is read-only by policy, not by prompt; the reason is cleared in a
  `finally` so a failed review never leaves the session read-only.
- Built-ins win over custom prompts, the user's over the repository's.
- Deferred: `/compact` (next, with summarised compaction), `/model` and
  `/mode` mid-session, named prompt arguments.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1719 passed (1680 before, plus 39 in `tests/test_slash_commands.py`).
- Mutation check (scratch script): 20 mutants -- a path is not a command,
  repository prompts need trust, arguments never lost, `$$`, quotes, size
  cap, empty prompts, no-argument commands, unknown is not a task, built-ins
  win, read-only cleared on failure, review read-only, init once, whose
  change, diff sanitised, new session, read-only refuses, plan mode before
  ASK, prompts ask for trust, old session closed -- all caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/slash-commands`, from main at `aecaf5b`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/repl.py` |
| A | `src/foundry/core/commands.py` |
| M | `src/foundry/core/policy/engine.py` |
| A | `src/foundry/prompts/init.md` |
| A | `src/foundry/prompts/review.md` |
| A | `tests/test_slash_commands.py` |
<!-- auto:end -->
