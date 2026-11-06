# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/rewind [N]` (D-056), item 17, as codex's Esc Esc: go back to before the
user's last message, or the Nth last, since the conversation or its last
compaction began. The model no longer sees it or what followed; the message
is shown to be sent again, edited. Nothing in the workspace is reverted, and
the model is told so with the next message, naming the files it patched
after the point. Every file is read again before a patch. The kept history
is journaled whole as `rewound` (synced), and resume folds it as it folds a
compaction.

## Changes

- `core/context.py`: `ContextManager.truncate`, which cuts the last request
  sent and the unbound replies to the same prefix (D-048).
- `core/runtime.py`: `AgentRuntime.rewind`, `_patched_at` (cleared by a
  compaction), `_history_payload` (shared with `compact`).
- `core/resume.py`: the fold and `_last_sent` read `rewound`; a record that
  does not hold together is refused.
- `core/session.py`: `EventType.REWOUND`, synced.
- `cli/repl.py`: `/rewind [N]`, `rewind_points`, `REWIND_NOTE`.
- `tests/test_rewind.py` (new, 27 tests).
- Docs: D-056; design (journal types, synced records); threat model;
  README (the command list); roadmap item 17.

## Decisions and open points

- Not past a compaction: its summary is what the model knows of the work
  before it.
- The old message is printed, not put back on the prompt line.
- Files patched before this process started (a resume) are not named; the
  note still says commands may have changed files.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2205 passed, 1 skipped (2178 before, plus 27 in
  `tests/test_rewind.py`).
- Mutation check (scratch script): 33 mutants over truncation, the
  sent prefix and unbound set, forgotten reads, which patches are named and
  when again, compaction, journaling, every check of the resume fold,
  `_last_sent`, the command's arguments and limits, the shown cut, the note,
  and where it can go back to. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/rewind`, from main at `f986a30`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_rewind.py` |
<!-- auto:end -->
