# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Session retention (D-163, P6; OQ-19 answered[provisional]):
`session_retention_days` becomes the user's setting, 0 -- every session
kept -- by default. Set, a session starting removes those last written more
than N days ago; `foundry prune` does it on demand.

## Changes

- `core/config.py`: the default is 0; a repository's or a client's config
  that sets it is refused; 0 or more, a whole number.
- New `core/retention.py`: which sessions have expired (by their journal's
  last write; never through a link or junction; only folders named as a
  session is, with a journal), and removing them (never one in use).
- `core/session.py`: `sessions_pruned`.
- `cli/wiring.py`: the sweep as a session starts, journaled and said.
- `cli/session_commands.py`, `cli/app.py`: `foundry prune [--days N]
  [--dry-run] [--force]`.
- `cli/setup_commands.py`: `foundry doctor` says what the setting does.
- Tests: `tests/test_retention.py`.
- Docs: D-163; OQ-19; requirements §3.5; design (the tree, the event types);
  threat model (a row); roadmap (P6); overview.

## Decisions and open points

- The default is 0, not the 30 OQ-19 leaned to: D-026's reason.
- No size ceiling; no promise of encryption at rest.

## Verification

- `tests/test_retention.py`: 32 passed.
- Mutation check: 15 mutants -- zero days, a folder not named as a session
  is, a junction, the folder's time for the journal's, oldest first, one
  failure stopping the rest, a repository setting it, negative days, a
  default of 30, no sweep at start, journaled when nothing went or not at
  all, a dry run deleting, `prune` not asking, zero days taken -- all
  killed.
- Full suite: 4860 passed, 1 skipped, with D-164 and D-165 stacked on it
  (21 min 50 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/session-retention`, from main at `f9cc432`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/open-questions.md` |
| M | `docs/overview.html` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/session_commands.py` |
| M | `src/foundry/cli/setup_commands.py` |
| M | `src/foundry/cli/wiring.py` |
| M | `src/foundry/core/config.py` |
| A | `src/foundry/core/retention.py` |
| M | `src/foundry/core/session.py` |
| A | `tests/test_retention.py` |
<!-- auto:end -->
