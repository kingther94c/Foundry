# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A compaction's summary is written as a handoff to the next step (D-216), at
the user's go-ahead after compaction and handoff were compared. In D-215's
first r-long run every one of eight compactions came in the middle of a
step, and after each the model read the whole working set again -- the
README, the format notes, the package, every earlier step's tests: 11,000 to
21,000 characters -- before writing anything. The summaries said what was
done, not what the next step needed, and copied the plan the runtime adds.
Amp's handoff carries a goal and the files it needs, opencode's summary its
relevant files, and Claude Code reads recent files back; none published
numbers.

## Changes

- `src/foundry/prompts/compact.md`: a section naming the files the next step
  opens (at most eight, what each is for, which part); facts later steps
  need from unchanged files are written down; the plan is not copied; an
  earlier summary is kept only where the remaining work needs it.
- `src/foundry/core/context.py`: the note before a summary says to open the
  files it names and read others again only where the work needs more;
  `SUMMARY_MARK` and `is_summary` know a summary by the note's first
  sentence, unchanged since D-040.
- `src/foundry/cli/slash_commands.py`, `src/foundry/cli/transcript.py`:
  `/rewind` and the resumed transcript use `is_summary`, so a summary
  written before this change is still one.
- Tests: `tests/test_compaction_handoff.py` (new, 7).
- Documents: decision log D-216, roadmap, design, README.

## Decisions and open points

- Not here: compacting where a step ends (the same code made no compactions
  in one run and five in another, more than a few runs could tell apart);
  the harness reading the named files back itself (it saves calls, not
  tokens); a `/handoff` command for the interactive session.
- The model still reads the README nearly every time after a compaction, and
  often an earlier step's tests as a pattern; the summaries were no shorter.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding),
  with the documents written: 5,288 passed, 4 skipped, and 102 failed or errored in
  21 files, every one because Windows could not start a child process (`git init`,
  `python -m foundry`: exit 0xC0000142) -- none in a file this change touches. Those
  21 files run again: 780 passed, 1 skipped. The suite collects 5,394 tests.
- Mutations of the new lines: 12, each killed (run on a copy of the tree while the
  gateway runs used this one).
- On the gateway (openclaw/foundry, r-long, three runs, 113 requests, plus 2 to see
  the gateway answered): all three completed and passed their hidden tests, in 33,
  40 and 33 rounds (35, 45 and 33 requests; two, five and no compactions). Each
  summary named one or two files for the next step; none copied the plan. Read
  after a compaction before the next write: median 10,491 characters over seven
  compactions, against 14,790 over D-215's eight.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `a093f54`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/slash_commands.py` |
| M | `src/foundry/cli/transcript.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/prompts/compact.md` |
| A | `tests/test_compaction_handoff.py` |
<!-- auto:end -->
