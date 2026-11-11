# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What changed outside a resumed session is not claimed as its own (D-177,
P11): a file the user changed while the session was stopped was listed as
changed by the session when it finished, and the prompt said it had changed
"before this session".

## Changes

- `core/tools/git.py`: `GitBaseline.outside`; `GitEvidence.session_changed`
  leaves those files out, and `outside_changed` names them.
- `cli/wiring.py`: a resume's baseline carries the files changed outside it.
- `core/runtime_ending.py`: the finish summary and the final evidence record
  name them apart.
- `cli/slash_commands.py`: `/diff` and `/status` say so.
- `core/prompts.py`: the dirty-file line and accept-edits mode's say "this
  session did not make".
- Tests: `tests/test_resume.py` (the prompt after a resume; the finish
  summary and record; `/diff` and `/status`; the evidence's three sets;
  accept-edits mode's line).
- Docs: D-177; design (resume).

## Decisions and open points

- Found by the fifth round (s20): `/export notes.md`, `/new`, `/resume`, and
  the model told notes.md had changed "before this session" beside a clean
  start. Checking it showed the finish summary claimed such a file too.

## Verification

- The new tests pass; the mutations show each part is needed.
- Mutations (10, all killed): the resume's baseline without them; the
  session's set keeping them; the outside set empty or taking the files
  dirty before; the summary list, the record field, `/diff`'s label and
  `/status`'s count left out; the old prompt and accept-edits wording.
- Full suite as CI runs it: 4883 passed, 1 skipped (21 min 15 s), before
  the docs; with them, the docs' own tests pass.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
