# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A resumed session shows its conversation (D-108), item 69: at a terminal,
`foundry resume` first shows what was typed, the model's answers and each
tool call's line, as codex's resume does, then its notices and the prompt.

## Changes

- `core/runtime.py`: `run_turn(..., typed=)`; `user_input.typed` journaled
  when the message says more than was typed.
- `cli/repl.py`: a task carries the line or command typed for it;
  `cli/app.py`: a command line's task too, and the conversation shown
  before the resume's notices, at a terminal.
- `core/resume.py`: `JournalState.typed`; the first task a listing and a
  resume name is the typed one.
- `cli/transcript.py` (new): `show_conversation`; `cli/render.py`:
  `show_answer`.
- `tests/test_resume_transcript.py` (new, 27 tests); the fakes of
  `run_turn` in `tests/test_markdown.py`, `tests/test_working.py` and
  `tests/test_images.py` take `typed`.
- Docs: D-108; README; design; threat model; roadmap item 69.

## Decisions and open points

- Typed text is matched to its message by the message's whole text; a
  journal from before shows the message as sent.
- The last 40 messages, the earlier counted; a user message cut to 12 lines.
- Not done: the calls' results; what came before a compaction.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3510 passed, 1 skipped (3483 before, plus 27 in
  `tests/test_resume_transcript.py`).
- Mutation check (scratch script): 36 mutants -- shown without a
  resume, what was typed ignored, tool results counted, nothing to continue
  said, no earlier said or every message shown, where it continues unsaid, a
  summary shown as typed, the message as sent, a long message whole or cut
  at exactly its length, images uncounted, no line before the user's, the
  user's words read as markup, an empty answer shown, the plan a line, no
  display, bad JSON, a TypeError, a refusal or an unknown tool raising, a
  display unsanitized, an answer's last line held, typed kept when the same,
  nothing or never kept, not passed on, an untyped task kept as typed, the
  line, the command or the command line's task not kept, shown off a
  terminal or never, typed not read back or not kept in the state, the first
  task as sent in the state or the listing. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-69`, from main at `23326f7`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/cli/repl.py` |
| A | `src/foundry/cli/transcript.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_images.py` |
| M | `tests/test_markdown.py` |
| A | `tests/test_resume_transcript.py` |
| M | `tests/test_working.py` |
<!-- auto:end -->
