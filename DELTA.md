# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A question is answered in the reply; `finish` is for a task (D-180, P11):
stand-in models in P9 and P11 were unsure whether a question at the
prompt needed `finish`.

## Changes

- `prompts/system.md`: Finishing says so, and that an unattended run ends
  with `finish` all the same.
- Tests: `tests/test_question_reply.py` (the sentence; the exception it
  names is what an unattended run's paragraph says, and an interactive
  one's does not).
- Docs: D-180; design (termination).

## Decisions and open points

- Found by the fifth round (s20), and before it in P9.

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (2, all killed): the paragraph left out; the unattended
  exception left out.
- Full suite as CI runs it: 4891 passed, 1 skipped (20 min 22 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
