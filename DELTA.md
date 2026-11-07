# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry mcp-server` asks its client (D-104), item 65: what the permission mode
would ask about is put to a client that offers elicitation, as codex's
mcp-server asks for approvals, and its user answers; a client that offers none
is never asked, and the task refuses as before.

## Changes

- `cli/mcp_server.py`: the client's elicitation capability read at
  initialize; `mode` may be `default` for such a client; a session's approval
  callback puts the question (`elicitation/create`) and waits for the answer,
  the task's cancel or the client's going; replies are handed to the question
  they answer.
- `tests/test_mcp_elicitation.py` (new, 30 tests).
- Docs: D-104; README; design; threat model; roadmap item 65.

## Decisions and open points

- Only a clear yes is a yes; "always" is not offered (it writes a rule into
  the user's config).
- The question is the terminal's display, stripped of control sequences,
  scrubbed of registered secrets, cut to 4000 characters.
- No deadline on an answer; a cancel or the client's going withdraws it.
- Not done: asking for a repository's trust; codex's other elicitations.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3379 passed, 1 skipped (3349 before, plus 30 in
  `tests/test_mcp_elicitation.py`).
- Mutation check (scratch script): 24 mutants -- any capability value
  taken or none, default not offered or not listed, dont_ask set up to ask, no
  one to ask, the policy still refusing, the prompt saying nobody can be asked,
  a question put after the client went, a stopped task still waiting, the
  question not withdrawn or read as a no, a malformed id, answers unheard or
  replies ignored, what is asked, the patch, control sequences, secrets,
  length, declines, content and "always" mishandled. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-elicitation`, from main at `0a9f0b8`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/mcp_server.py` |
| A | `tests/test_mcp_elicitation.py` |
<!-- auto:end -->
