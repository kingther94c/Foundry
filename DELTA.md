# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

MCP servers reached over streamable HTTP (D-081), item 42: `url` and
`bearer_token_env_var` in `[mcp_servers.<name>]`, as codex's. A POST per
message, JSON or an event stream back, the session id and protocol version
after initialize, DELETE at close; the token only in the Authorization
header, only over https or to this machine, and scrubbed.

## Changes

- `core/config.py`: `url` and `bearer_token_env_var` (refused beside
  `command`, `args`, `env`, `env_vars`; a token over plain http to another
  host, or a user name and password in the url, refused);
  `McpServerConfig.shown_as()`.
- `core/mcp.py`: `StreamableHttpClient`; the handshake, `list_tools` and
  `call_tool` moved to a shared `_Protocol`; `start_servers` picks the client
  by the entry.
- `core/httpc.py`: `HttpClient.connect()`, public, for the MCP client.
- `cli/repl.py`: `/mcp` shows a url server's scheme and host.
- `tests/test_mcp_http.py` (new, 36 tests, an HTTP server in process).
- Docs: D-081; README (the MCP example); design; requirements; roadmap item
  42; threat model.

## Decisions and open points

- Each request runs on a thread of its own so Ctrl+C and the timeout can cut
  it; the cut closes the socket's handle, since on Windows a shut-down socket
  does not wake a blocked read.
- A session the server ended (404) stops the server until a new Foundry
  session; it is not re-initialized.
- Open: `http_headers`/`env_http_headers`, OAuth, and the GET stream for
  messages a server starts on its own.

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2658 passed, 1 skipped (2622 before, plus 36 in
  `tests/test_mcp_http.py`).
- Mutation check (scratch script): 24 mutants -- url beside a command,
  a token over plain http, a user and password kept, `/mcp` showing the path,
  a token without a url, a token pasted as the name, no Authorization, the
  token not scrubbed, no session id, no protocol version, no DELETE, the
  server's requests unanswered, the server not told, the connection not cut,
  asking again after the session ended, the session not taken, any session
  id, any token, 202 as a reply, a JSON body or an event unbounded, the reply
  event missed, any reply taken, url servers started as programs. All are
  caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/mcp-http`, from main at `a612051`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/httpc.py` |
| M | `src/foundry/core/mcp.py` |
| A | `tests/test_mcp_http.py` |
<!-- auto:end -->
