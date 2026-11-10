# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

What the user approves is what runs, and "always" means exactly it
(D-151, from P2): the client's question no longer cuts what would run, an
MCP call cut in its display is asked with its arguments whole, padding and
carriage returns are shown counted, and an "always" rule matches only the
call approved.

## Changes

- `cli/render.py`: `visible()`; the approval panel shows the display, the
  reason and, for a non-command non-patch call, the arguments through it.
- `core/runtime.py`: `_ask` puts the arguments in `detail` when the display
  does not hold them.
- `cli/mcp_server.py`: `_question` never cuts what would run, cuts only a
  patch's body, and returns None past `MAX_SHOWN_CHARS`; `_ask` then refuses
  with a notice instead of asking.
- `cli/app.py`: `_literal()` for the "always" rule's pattern.
- Docs: D-151, design (approvals), threat model (a row).

## Decisions and open points

- A refused-unasked call is replied to the model as one the user declined
  (the approval callback returns a choice, not a reason); the notice says why.
- Rule matching on Windows stays case-insensitive (fnmatch), as for every
  rule; "always" makes only the wildcards literal.

## Verification

- `tests/test_approval_shows.py` (15): `visible`; the question with padding,
  longer than a question's length and still whole, over the limit, with a carriage return, with arguments; the server refusing
  unasked; the terminal panel; the runtime's `detail` for a cut MCP display
  and not for a command; "always" with `*`, `?` and `[`.
- `tests/test_mcp_elicitation.py`: the patch-cut test now expects the head
  whole and the patch shortened with a note.
- Approval-related tests (10 files): 241 passed.
- Mutation check: 15 mutants, all killed. One (cutting the whole question
  at 4,000 characters) first survived -- the padded test collapses to a
  short head -- and a test with a head past 4,000 characters was added; one
  (asking although the question was too long) hangs the client test, and
  is counted killed on its timeout.
- Full suite (rebased on D-150): 4740 passed, 1 skipped.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
