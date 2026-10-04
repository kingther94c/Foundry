# Foundry

A local coding-agent runtime built from scratch: Python 3.12, Windows, offline
wheel install. It owns all of the code and interfaces for its agent loop,
tools, policy, providers and sessions; it does not fork, call into, or
impersonate any existing coding agent.

**Current status: M0-M4 implemented and running**, with 1436 tests passing on a
machine with no network and no credentials (including the security regressions
from six rounds of adversarial review, and 780 generated breaker-table
invariant combinations). After round 6 there was a separate clean-room check:
rebuild the wheelhouse, install into a fresh venv with `--no-index`, and run a
real task again (failing tests -> patch -> re-run green -> finish citing the
event id from its own tool output). All 14 checks passed.

## Want to understand how it works first?

`demo/` is this system's skeleton: exactly the same structure as the real
thing, with every layer shaved down to its thinnest form. No network, no API
key:

```bash
python demo/mini_foundry.py --yes
```

To run it a cell at a time, see the state in between, and change something and
re-run, use the notebook version (it imports from the `.py` rather than copying
code):

```bash
jupyter lab demo/mini_foundry.ipynb
```

To watch policy stop something that should not happen, and the finish gate see
through a false "all the tests pass":

```bash
python demo/mini_foundry.py --script destructive --yes
python demo/mini_foundry.py --script liar --yes
```

[demo/README.md](demo/README.md) maps every section onto the real module, and
explains how to point it at a real model (any OpenAI-compatible
`/v1/chat/completions` will do).

## Quick start

`wheelhouse/` is not in version control, so build it first:

```bash
python scripts/build_wheelhouse.py
```

Then install offline:

```bash
python -m pip install --no-index --find-links wheelhouse foundry
foundry doctor
foundry login
foundry
```

## Commands

| Command | What it does |
|---|---|
| `foundry` / `foundry run [task]` | interactive session (the default) |
| `foundry exec <task> [--json]` | unattended execution; every ASK becomes DENY (fail-closed) |
| `foundry record <task> --output f.json` | record a session as a replay fixture |
| `foundry sessions [id]` | list sessions, or show one |
| `foundry report [--json]` | patch first-try success rate, command failures, denials, token stats |
| `foundry login` / `logout` | credential management (DPAPI encrypted) |
| `foundry doctor` | environment self-check |

Exit codes: `completed=0 partial=10 blocked=11 failed=12 cancelled=13
interrupted=14`

## Design points

- **One loop**: `while the model returns tool calls: policy -> execute -> feed
  back -> resample`. A provider adapter only translates between protocols; it
  does not own the loop, touch policy, or call tools.
- **A six-step policy pipeline**: breaker table -> pre_tool callback -> DENY ->
  ASK -> mode baseline -> ALLOW -> interactive approval (no answer means DENY).
  A DENY at any layer cannot be flipped by any ALLOW.
- **The evidence chain**: every verification claim in `finish` must cite a real
  command event whose exit code matches, or `completed` is downgraded to
  `partial`. An empty claim is valid disclosure; an invented one is not.
- **No sandbox, honest disclosure**: approval reduces mistakes, not malice. The
  boundaries and non-goals are in [docs/threat-model.md](docs/threat-model.md).
- **A dependency budget**: the runtime has only `rich` (5 pure-Python wheels in
  total). HTTP/SSE, DPAPI and Job Objects all go through the stdlib and ctypes
  -- `ssl.create_default_context()` trusts the Windows system certificate
  store, so a corporate MITM proxy needs no configuration.

## Code map

```text
src/foundry/core/     runtime (the one loop), policy, tools, backends, session, workspace, winapi
src/foundry/cli/      the terminal UI: event subscriber + approval UI + report
src/foundry/prompts/  versioned system prompts
tests/                1436 tests: golden scenarios, attack tables, breaker-table invariants
```

`foundry.core` imports neither `foundry.cli` nor any third-party package --
both are enforced by tests
([tests/test_architecture_config.py](tests/test_architecture_config.py)).

## Documentation

| Document | Contents |
|---|---|
| [docs/requirements.md](docs/requirements.md) | requirements v0.2 |
| [docs/design.md](docs/design.md) | the design: package structure, core interfaces, key mechanisms |
| [docs/threat-model.md](docs/threat-model.md) | what is protected, what is not, and where enforcement happens |
| [docs/roadmap.md](docs/roadmap.md) | milestones and status |
| [docs/decision-log.md](docs/decision-log.md) | 30 numbered decisions (including the overturned ones) |
| [docs/open-questions.md](docs/open-questions.md) | open questions |
| [docs/research/](docs/research/) | research notes, including the [first live endpoint check](docs/research/live-endpoint.md) |
| [demo/](demo/) | the skeleton in one runnable file, plus a notebook |

## Open

The personal path is an OpenAI API key (ChatGPT login is confirmed blocked; the
evidence is in [docs/research/auth.md](docs/research/auth.md)). The corporate
Gateway's `responses` adapter is implemented and has contract tests, but **its
real protocol behaviour is unverified** -- the entry gate for M3 is obtaining a
tool-call streaming redaction fixture from the Gateway
([OQ-6](docs/open-questions.md)).
