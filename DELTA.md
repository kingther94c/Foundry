# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

An MCP server cannot hang a session or end it (D-155, from P2): a stdio
server's input is written by a thread of its own from a bounded queue; an
HTTP session's DELETE has one deadline in all; lone surrogates from a server
or the model become U+FFFD; a TypeError in a tool's check is an invalid
call; and a server's name cannot run into its tools'.

## Changes

- `core/mcp.py`: `StdioClient` queues messages (`MAX_UNREAD_MESSAGES` = 256)
  for `_write_stdin`; a full queue refuses a request at once and drops an
  answer to the server; a failed write is noticed by waiting requests;
  `close()` queues the end of input; `_send` refuses at once once the server
  has stopped. `StreamableHttpClient.close()` runs `_end_session` on a
  thread, joined for `QUIET_TIMEOUT`. Every message parsed through
  `without_lone_surrogates`. `McpTool.validate` reads `required` only when
  it is a list.
- `core/conversation.py`: `without_lone_surrogates()`; `parse_arguments`
  applies it.
- `core/mcp_resources.py`: a `server` that is not a string is an invalid call.
- `core/runtime.py`: TypeError joins InvalidToolCall and ValueError, at the
  call and at a hook's rewrite.
- `core/config.py`: a server name may not hold `__` or end in `_`.
- `tests/mcp_fake_server.py`: modes `deaf`, `noinput`, `flood`, `surrogate`.
- Docs: D-155, design (MCP), threat model (the MCP row).

## Decisions and open points

- The queue's bound is in messages, not bytes: what a request carries is
  bounded already (`MAX_ARGUMENT_BYTES`).
- A server name like `a__b` that worked before is now refused at load; the
  error says why. Tool names a server gives with `__` are still taken: the
  server's prefix is unambiguous once its own name has none.
- TypeError is caught as a malformed call everywhere a tool's check runs;
  the model is told the exception's text.

## Verification

- `tests/test_mcp_robust.py` (21): a server that reads nothing times a call
  out and is closed; one flooding requests ends the calls with errors; the
  queue's bound refuses the 257th message; a closed input fails a call at
  once; a reading server still answers and ends at the end of its input;
  an HTTP DELETE trickled a byte at a time is left after 2 s; lone
  surrogates from the helper, a model's call, a stdio server, an HTTP body
  and event; a `[]` server and `"required": 5`; a TypeError in a check and in
  a hook's rewrite; server names refused and taken.
- MCP, config and conversation tests: 414 passed before the one fix below.
- Mutation check: 20 mutants, all killed; the flood test was flaky on
  whether the queue had filled, so it now asserts only an error in bounded
  time and the bound has a test of its own; with the stopped check added to
  `_send` (which restored `test_a_server_that_dies_mid_call_says_why`'s
  "not running"), both mutants it touches were killed again.
- Full suite: 4788 passed, 1 skipped (21 min 53 s), with D-154.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
