# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Where a backend's key, headers and query come from (D-084), item 45, as
codex's `model_providers`: `env_key` (the key from that variable only, never
the stored one), `env_http_headers` (values from variables, scrubbed, left
out when unset) and `query_params` (on every request URL, the model list
included), in `[backend]` or a profile's.

## Changes

- `core/config.py`: the three settings, validated, and connection settings
  (no repository may set them).
- `core/auth.py`: `EnvKeySource`.
- `core/backends/base.py`: `with_query`; the three backends take
  `query_params` on their endpoint and model list.
- `cli/app.py`: `request_headers`; `_credential_source` picks
  `EnvKeySource`; `foundry login` says where such a key goes.
- `tests/test_provider_settings.py` (new, 21 tests).
- Docs: D-084; README; design; roadmap item 45; threat model.

## Decisions and open points

- With `env_key` the store is never read: its key belongs to the protocol's
  default provider.
- Not changed: how the key is sent (Bearer, or `x-api-key` for Anthropic).

## Verification

- `python -X warn_default_encoding -W error::EncodingWarning -m pytest -q`:
  2726 passed, 1 skipped (2705 before, plus 21 in
  `tests/test_provider_settings.py`).
- Mutation check (scratch script): 17 mutants -- a repository setting
  them, any `env_key`, any header name, any query value, the named key or a
  header value not scrubbed, `env_key` ignored, `login` storing it anyway, an
  empty header sent, a line break sent on, the session ignoring the headers
  or the query, a second `?`, each protocol's endpoint and the model list
  without the query. All are caught.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/provider-settings`, from main at `fd1dbd4`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `docs/threat-model.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/core/auth.py` |
| M | `src/foundry/core/backends/anthropic.py` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/config.py` |
| A | `tests/test_provider_settings.py` |
<!-- auto:end -->
