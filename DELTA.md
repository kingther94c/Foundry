# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Compaction by summary (D-040), the second step of the codex-parity order. When
masking leaves the projected context past 80% of the window, the runtime asks
the model for a handoff summary and the history becomes the user's own
messages plus that summary; `/compact [focus]` does it on request, and
`runtime.auto_compact = false` turns the automatic one off. A long task goes on
instead of ending `partial(context window exhausted)`, and a resume follows the
history past a compaction.

## Changes

- `prompts/compact.md` (new): what the summary must cover.
- `core/context.py`: `needs_compaction`, `compactable`, `compacted_history`
  (user messages newest first within 10% of the window, earlier summaries
  dropped, summary scrubbed and capped at 15%), `replace_history`.
- `core/runtime.py`: `compact(auto, focus)`; run at the top of a round before
  the exhaustion check; one failure stops it for the task; `_sample` takes the
  messages and a `purpose`, and a summary is not streamed to the user.
- `core/events.py`: `Compacted`. `core/session.py`: `EventType.COMPACTED`,
  synced. `core/tools/base.py`: `ReadTracker.clear`.
- `core/resume.py`: the fold skips `purpose` records and resets the history
  at a `compacted` record; the tail skips them too.
- `core/config.py`: `runtime.auto_compact` (boolean). `cli/app.py` wires it.
- `cli/repl.py`: `/compact [focus]`. `cli/render.py`: the compacted line.
  `cli/report.py`: `compactions`, and summary requests are not rounds.
- `tests/test_compaction.py` (new, 28 tests).
- Docs: D-040; design.md sections 5 and 9; requirements.md; threat-model row
  (and the plan-approval row now points at `cli/repl.py`); README; roadmap.

## Decisions and open points

- Masking stays first; the summary is the step after it, not a replacement.
- The summary request carries the normal tools (a history with tool calls is
  refused without them) and any tool call in its reply is ignored.
- Files read before a compaction are read again before a patch; command
  evidence is unchanged.
- Deferred: compacting mid-round, showing the summary, a configurable
  threshold.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  1747 passed (1719 before, plus 28 in `tests/test_compaction.py`).
- Mutation check (scratch script): 27 mutants -- the threshold, only model
  output compacted, earlier summaries dropped, order, newest first, summary
  scrubbed and capped, the round stamp, reads forgotten, no retry every round,
  the next task retrying, the summary not streamed, empty summary refused,
  focus, plan kept, Ctrl+C, the setting off, the fold and tail skipping the
  summary call, the fold following the record, report rounds and count,
  `/compact` cancelled, the setting type-checked and wired, the rendered line,
  the record synced -- all caught.
- Not run against a live model: the local gateway answers 404.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/compaction`, from main at `e9cf041`.

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
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/cli/report.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/events.py` |
| M | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| M | `src/foundry/core/tools/base.py` |
| A | `src/foundry/prompts/compact.md` |
| A | `tests/test_compaction.py` |
<!-- auto:end -->
