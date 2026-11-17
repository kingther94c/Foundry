# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry checkup` (D-204), the first-run check the user asked for on
2026-11-16 and took the design of on 2026-11-17, with its first three levels:
this machine, the configuration, and the network to the endpoint. Each case
is a line with a short code where something is wrong and what to do; the run
ends with a summary, a feedback code to read out (no host, nothing secret)
and the path of a report kept in `checkups` under Foundry's folder. The
client a session uses now sends a proxy's user name and password decoded,
as a URL holds them.

## Changes

- `src/foundry/core/checkup.py` (new): a case, a level's verdict, the
  feedback code, the report.
- `src/foundry/core/netcheck.py` (new): the proxy and where it was found,
  the name, the connection, the proxy's tunnel and TLS, each on its own; who
  issued a certificate, read from its DER.
- `src/foundry/core/httptap.py` (new): an HttpClient that keeps each
  response's status, headers and body as they came, never a request's
  headers.
- `src/foundry/cli/checkup.py` (new): the command, the machine's and the
  configuration's cases, the model list through the adapter, the screen and
  the report. `src/foundry/cli/app.py`: `checkup [-C DIR]`.
- `src/foundry/cli/setup_commands.py`: `credential_found` gives the
  credential's kind too; `doctor` uses netcheck's `shown_proxy`.
- `src/foundry/core/httpc.py`: a proxy's user name and password
  percent-decoded.
- `tests/test_checkup.py` (new, 41 tests), `tests/selfsigned.py` (new: an RSA
  key and a self-signed certificate made at test time with the standard
  library).
- Documents: decision log D-204; README (the first run, the command table);
  requirements; design's code map; roadmap (status, M3's gate, P15, the
  suite's size).

## Decisions and open points

- One command where P15 planned three (`doctor --online`, `foundry probe`, a
  list of what to report), at the user's word. The protocol and task levels
  are the next entries; until then the code leaves them out.
- The report is in `%LOCALAPPDATA%\Foundry\checkups` (or the folder
  FOUNDRY_HOME names), not the `~/.foundry` the design's sketch said: that is
  Foundry's folder, and the documents are held to it.
- M3's entry gate becomes every level OK on the corporate machine, its code
  said, once all five levels are in.

## Verification

- Full suite as CI runs it, with `PYTHONDONTWRITEBYTECODE` unset so that
  bytecode is written as on CI: 5252 passed, 1 skipped, of the 5,253 the
  roadmap names; 20 min 40 s on Windows.
- The new tests cannot import the code before; the one about the session's
  client fails on it (the old client sent `alice:p%40ss%3Aw`).
- 55 mutations of the new lines, each killed; the one that took any Foundry
  under a folder named src for a clone survived at first, and the installed
  copy's test now sits in a virtual environment inside a repository.
- A run on this machine with an empty home: E warned of long paths, C failed
  for no credential and no config.toml, N reached api.openai.com through TLS
  and did not ask for the model list; the report was written. It sent no
  credential and no request.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `f4c38e1`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| A | `src/foundry/cli/checkup.py` |
| M | `src/foundry/cli/setup_commands.py` |
| A | `src/foundry/core/checkup.py` |
| M | `src/foundry/core/httpc.py` |
| A | `src/foundry/core/httptap.py` |
| A | `src/foundry/core/netcheck.py` |
| A | `src/foundry/core/protocheck.py` |
| A | `tests/selfsigned.py` |
| A | `tests/test_checkup.py` |
<!-- auto:end -->
