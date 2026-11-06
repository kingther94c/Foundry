# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`/model` lists the endpoint's models (D-067), item 28: typed alone, it asks
the session's endpoint for its models (`GET /models`) and shows them sorted,
the session's own marked, as codex's `/model` offers a list -- so the
spelling a gateway wants is one command away.

## Changes

- `core/httpc.py`: `get_json` -- a GET with its own timeout for the
  connection and every read, and a cap on the body.
- `core/backends/`: `list_models()` on the Chat Completions, Responses and
  Anthropic backends (Anthropic's with `?limit=1000`, without the betas);
  `model_ids()` in `base.py`; a recording backend asks the one it records.
- `cli/repl.py`: `show_models`, called by `/model` with no name.
- `tests/test_model_list.py` (new, 28 tests).
- Docs: D-067; design (`/model`, the HTTP client); threat model (a row for
  the model list); README; roadmap item 28.

## Decisions and open points

- Asked each time `/model` is typed alone, never cached, never retried; 15 s
  and 1 MB at most. A switch by name does not ask, and a name the endpoint
  does not list is still accepted (a gateway may serve more than it lists).
- What comes back is shown only: ids shaped as model names, escaped; never
  sent to the model or journaled. An error shows its status, never the body.
- Not checked against a real gateway: the one available returns 404 for
  everything (real-model validation is still blocked).

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2398 passed, 1 skipped (2370 before, plus 28 in
  `tests/test_model_list.py`).
- Mutation check (scratch script): 19 mutants -- the timeout, the size
  cap, the status check, the order, the id check, the list check, Anthropic's
  betas and page size, the URL, the credential, the recording backend,
  `/model` showing them, the name check, the mark, the cut, Ctrl+C, the
  cleaned error, the count left out, and a backend that cannot list. All are
  caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/model-list`, from main at `a636bf9`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/repl.py` |
| M | `src/foundry/core/backends/anthropic.py` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/recording.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/httpc.py` |
| A | `tests/test_model_list.py` |
<!-- auto:end -->
