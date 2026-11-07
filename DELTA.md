# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

UserPromptSubmit and Stop hooks, as codex's (D-116), item 77: a prompt hook
may stop a prompt before it is sent, or add a note to it; a Stop hook, run
when the model is done -- at an answer or at `finish` -- may send the task on
with its reason as the next prompt.

## Changes

- `core/hooks.py`: the two events (no matcher, as codex's),
  `read_user_prompt_submit`, `read_stop`, `READERS`, `events_named`; plain
  text kept from a run; feedback and prompts cut at codex's default
  (`_feedback`), PreToolUse and PostToolUse included.
- `core/runtime.py`: `_run_hooks` for any event (`_tool_hooks` at a call),
  `_prompt_hooks` before the prompt is recorded (a stop ends the task
  `blocked`, nothing recorded or sent), `_stop_hooks` at an answer and at
  `finish` (`stop_hook_active`; the reason a `user_input` with `hook`, or the
  refused finish's answer); `turn_id` the task's.
- `cli/repl.py`: `/hooks` shows no matcher for the two; the events named.
- `tests/test_prompt_stop_hooks.py` (new, 72 tests); `tests/test_hooks.py`:
  the refused events and `/hooks` wording.
- Docs: D-116; README; design; threat model; roadmap item 77.

## Decisions and open points

- Stop also runs at `finish`, which codex has no counterpart of: it is how an
  unattended run ends, so without it a Stop hook could never keep one going.
- A prompt hook's note goes in the prompt (codex adds a developer message).
- Not yet: SessionStart, SessionEnd, PreCompact, PostCompact, Interrupt,
  PermissionRequest; `mcp_tool` handlers; background `async`.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3970 passed, 1 skipped (3898 before, plus 72 in
  `tests/test_prompt_stop_hooks.py`).
- Mutation check (scratch script): 60 mutants over the events and the
  matcher, the cut, plain text, each answer of both readers, the prompt (told,
  stopped, its notes, `typed`), the stop (at an answer and at `finish`,
  `stop_hook_active`, precedence, the record), `turn_id` and `/hooks`. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-77`, from main at `cfb18a1`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_hooks.py` |
| A | `tests/test_prompt_stop_hooks.py` |
<!-- auto:end -->
