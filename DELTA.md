# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The MCP client's tests start the interpreter, not a virtual environment's
launcher (D-171, found with D-170). Tests only.

## Changes

- `tests/test_mcp_robust.py`: the fake server starts with
  `sys._base_executable`; a new test checks that the process the tests start
  is the interpreter.
- Docs: D-171.

## Decisions and open points

- The client is unchanged: through a launcher, a server that closes its
  input fails the call after the call's timeout, not at once.

## Verification

- Without the change, in a virtual environment with D-170's versions:
  `test_a_server_whose_input_is_closed_fails_a_call_at_once` failed 5 runs
  of 5 ("tools/call timed out after 1s"); with the system interpreter it
  passed.
- With it: `tests/test_mcp_robust.py` 22 passed, in the virtual environment
  and with the system interpreter.
- Mutation: `PYTHON = sys.executable` fails both the new test and the
  closed-input test in the virtual environment.
- Full suite in the virtual environment with D-170's versions: 4869 passed,
  1 skipped (19 min 50 s).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
