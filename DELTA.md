# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The notify program is told when an approval waits (D-079), item 40: before
a question blocks, the user's `notify` program gets `approval-requested`
with the tool, the call's display and the reason -- what D-050 deferred.

## Changes

- `core/notify.py`: `APPROVAL_TYPE`, `approval_payload()`.
- `core/runtime.py`: `_notify_approval()` from `_ask`, before the question;
  `_send_notification()` shared with the turn's end; each `notified` record
  names its `type`.
- `tests/test_notify_approval.py` (new, 7 tests); `tests/test_notify.py`
  -- its records carry `type`.
- Docs: D-079; README (the notify paragraph); design; roadmap item 40.

## Decisions and open points

- Only when someone can be asked: unattended runs and calls a rule allows
  tell nobody. Scrubbed and cut as a turn's texts are.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2606 passed, 1 skipped (2599 before, plus 7 in
  `tests/test_notify_approval.py`).
- Mutation check (scratch script): 8 mutants -- nobody told, told
  after the question, told when nobody can be asked, the display not
  scrubbed, the reason not cut, a record without its type, journaled as a
  turn, told as a turn's end. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/notify-approval`, from main at `00b7b39`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/notify.py` |
| M | `src/foundry/core/runtime.py` |
| M | `tests/test_notify.py` |
| A | `tests/test_notify_approval.py` |
<!-- auto:end -->
