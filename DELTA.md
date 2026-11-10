# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Memories keep no secret, give no instructions, and forget when told (D-154,
from P2): the configuration's credentials are scrubbed before any journal is
read, the summary is given fenced and escaped and scrubbed again, a
sub-agent's MCP calls count, a reset cannot be undone by a running pass or a
resumed session, a session turned off leaves the notes, and `memories show`
strips escape sequences.

## Changes

- `core/memories.py`: `instructions()` fences the summary in `<memories>`
  tags, escaped and scrubbed; `read_memories` scrubs; `_journal_facts`
  returns `_Facts` with when the journal began, and reads the sub-agents'
  journals for external calls; `candidates` skips a session begun before the
  last reset and collects the ones turned off (`withdrawn`); the pass removes
  their memories, records them, and merges again; `forget` takes the
  folder's lock and returns None when a pass holds it.
- `cli/app.py`: `_guard_credentials` also registers the values of the
  configuration's credential variables and of MCP servers' `env_vars`, and
  `foundry memories update` calls it; `memories reset` says when a pass is
  running and fails; `memories show` sanitizes.
- Docs: D-154, threat model (the memories row).

## Decisions and open points

- A session turned off while active keeps its memories in the notes until
  it has been idle long enough to be looked at again: the pass does not read
  active journals.
- A session started before a reset is not read again at all, including what
  it did after: the reset said "the sessions before now".
- `forget` returning None (not False) when busy keeps `if forget(...)` true
  only for a reset that happened.
- The 20,000-character bound on the summary (D-145) stays.

## Verification

- `tests/test_memories_guarded.py` (10): the fence and its escaping; the
  summary and `read_memories` scrubbed of a secret registered after they
  were written; the update process scrubbing a server's `env_vars` and a
  header variable named only after the session; a sub-agent's MCP call;
  a resumed pre-reset session not read; a session turned off removed from
  the notes, recorded, and the notes gone with the last; a reset under a
  running pass forgetting nothing, and the CLI saying so; `show` without
  escape sequences.
- `tests/test_memories.py`: the system-prompt test expects the fence.
- Memory and environment tests: 58 passed before the new file; 10 more.
- Mutation check: 17 mutants, all killed.
- Full suite: 4767 passed, 1 skipped (20 min 55 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
