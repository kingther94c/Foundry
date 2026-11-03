# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Session resume (D-035): `foundry resume` and `foundry exec --resume` rebuild a
session's conversation from its journal and continue it, without weakening the
evidence chain, the dirty-file guard, redaction or HEAD attribution. Also fixes
a gap in the evidence chain found on the way: a partly applied patch did not
count as a change.

## Changes

- runtime: a patch that landed some files and rejected others moves the
  mutation mark (`_last_mutation`), so earlier commands cannot vouch for it.
- journal (additive, versions unchanged): `user_input`, `tool_reply` and
  `resumed` records; `model_request.turn`; `model_response.opaque`;
  `tool_call.paths` for mutating calls. Every record is flushed on write.
- `core/session.py`: an OS lock per session (`sessions/<id>/lock`);
  `SessionStore(..., resume=True)` continues ordinals, re-indexes the
  artifacts and ends a half-written last line on its first append;
  `terminal_status` reads a run after `resumed` as interrupted.
- `core/resume.py` (new): rebuilds history from the requests plus the tail
  records, answers every call that has no result, refuses what cannot be
  continued safely (`ResumeError`), and writes the note for the model.
- `core/context.py`: `turn` and `restore()`. `core/tools/git.py`:
  `GitBaseline.from_payload`. Policy's dirty-file reason no longer says
  "when this session started".
- `cli/app.py`: `foundry resume [session] [task]`, `foundry exec --resume`,
  `build(resume=...)`; `sessions` shows which sessions are in use. A REPL that
  goes on after a blocked turn now records how it ended.
- docs: D-035; design.md section 9; requirements (resume landed, termination
  per run, the resume-ready test); README commands; threat model; roadmap;
  OQ-9.

## Decisions and open points

- Evidence always starts over on resume (v1). Carrying it over needs a
  workspace fingerprint at every termination: deferred, in D-035.
- A moved HEAD refuses the resume; re-baselining is deferred.
- Old journals resume by the same path, with their missing results answered.
- Not tested against a live provider: the local gateway's upstream rejects its
  model today (an OpenClaw account issue). Covered offline end to end.

## Verification

- `python -m pytest -q tests/test_subsystem_audit.py`: the new partial-patch
  test fails without the fix and passes with it.
- `tests/test_resume.py` (27 tests): exact rebuild with masking and provider
  blocks; the next request equals an uninterrupted session's; repairs for an
  interrupted call, a budget stop and finish; evidence starts over; outside
  changes guarded; HEAD moved, full context, other workspace, path-like ids,
  newer schema, inconsistent and damaged journals refused (nothing written);
  older journals; lock; ordinals; `exec --resume` through `main()`.
- Mutation check: 14 mechanisms broken one at a time; every one fails a test.
- Full suite `python -X warn_default_encoding -W error::EncodingWarning -m
  pytest -q tests`: 1617 passed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/session-resume`, from main at `beb771e`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/open-questions.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/errors.py` |
| M | `src/foundry/core/policy/engine.py` |
| A | `src/foundry/core/resume.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/session.py` |
| M | `src/foundry/core/tools/git.py` |
| A | `tests/test_resume.py` |
| M | `tests/test_subsystem_audit.py` |
<!-- auto:end -->
