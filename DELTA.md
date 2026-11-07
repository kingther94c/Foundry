# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A url server's own headers (D-111), item 72: codex's `http_headers` and
`env_http_headers`, sent with every request to that server, a credential
only by its variable's name.

## Changes

- `core/config.py`: the two tables, `_mcp_headers`, `_secret_header`,
  `HEADER_VALUE`, `_URL_KEYS`, `_PROTOCOL_HEADERS`; a variable's header held
  to https or this machine as the token is.
- `core/mcp.py`: `StreamableHttpClient._user_headers`, read at start, sent
  under Foundry's own.
- `cli/app.py`: `mcp get` shows them, text and JSON.
- `tests/test_mcp_headers.py` (new, 63 tests); `tests/test_mcp_get.py`'s
  JSON expectation has both tables.
- Docs: D-111; README; design; threat model; roadmap item 72.

## Decisions and open points

- A credential-named header (`Authorization`, `Cookie`, a key's name) is
  refused in `http_headers`; an unset variable leaves its header out.
- A static value is not given to the redactor; a value is never echoed.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  3615 passed, 1 skipped (3552 before, plus 63 in
  `tests/test_mcp_headers.py`).
- Mutation check (scratch script): 40 mutants -- the value check
  loosened or tightened, each key unknown or allowed beside a command, an
  empty table refused, each protocol header allowed, case kept, a header
  twice, a credential in the file or refused from a variable, the https rule
  dropped or widened, the message, either table not kept, nothing sent, the
  user's headers winning, an unset variable sent, a bad value sent, a value
  not registered, `mcp get` unscrubbed or silent. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/item-72`, from main at `bb50f77`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/mcp.py` |
| M | `tests/test_mcp_get.py` |
| A | `tests/test_mcp_headers.py` |
<!-- auto:end -->
