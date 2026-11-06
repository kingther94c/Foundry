# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`exec` names its session first (D-060), item 21: a `session_started` event
(id, workspace, model, resumed) -- the first JSON line with `--json`, a dim
line on stderr without -- so a script knows what `exec --resume` takes, as
codex's exec reports its session id.

## Changes

- `core/events.py`: `SessionStarted`, in the `Event` union.
- `cli/render.py`: one dim line for it.
- `cli/app.py`: `cmd_exec` emits it before anything else.
- `tests/test_exec_session_id.py` (new, 3 tests).
- Docs: D-060; design (the event list); README (the exec row); roadmap
  item 21.

## Decisions and open points

- Not in interactive sessions: D-059's closing line and `/status` give it.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2254 passed, 1 skipped (2251 before, plus 3 in
  `tests/test_exec_session_id.py`), including a run through `main()` against
  the fake model server that resumes by the id the first run reported.
- Mutation check (scratch script): 7 mutants -- emitted, first,
  resumed, workspace, model, and the renderer's line. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/exec-session-id`, from main at `ad6e5f2`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/render.py` |
| M | `src/foundry/core/events.py` |
| A | `tests/test_exec_session_id.py` |
<!-- auto:end -->
