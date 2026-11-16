# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

`foundry doctor` says what is so (D-203), the second item of P15. Its
credential line names where a session's credential would come from -- the
variable's name, `backend.env_key`, or the field `foundry login` stored --
never the value, and none there fails the check. The proxy is the one the
HTTP client would use for the endpoint, without its user name and
password. The machine's `policy.toml` is named with its rule count.
Outside a Git repository the check fails, and `-C`/`--workspace` names the
folder, read with its repository's settings.

## Changes

- `cli/setup_commands.py`: `_credential_line`, `_shown_proxy`; `cmd_doctor`
  reads the workspace's layer, reports the credential's source, the machine
  policy and the proxy for the endpoint, and fails outside a repository or
  without a credential. Paths and the proxy are escaped for rich's markup.
- `cli/app.py`: `doctor -C/--cd/--workspace`.
- `core/config.py`: `managed_policy_path()`, used by `load_config` and
  `doctor`.
- Tests: `tests/test_doctor_truth.py` (8, new).
- Docs: D-203; `README.md` and `requirements.md` on what `doctor` says;
  `roadmap.md`, status, P15 and the suite's size.

## Decisions and open points

- A missing credential fails the check: every session would stop at its
  first request.
- Nothing here goes on the network; the proxy, TLS, the credential and a
  stream are for the step of P15 that asks the endpoint.
- Open: the user's choice on the `foundry checkup` design, which would read
  these same lines.

## Verification

- Full suite as CI runs it, with `PYTHONDONTWRITEBYTECODE` unset so that
  bytecode is written as on CI, on the stack's tip (D-200 to D-203 on
  8a81793): 5210 passed, 1 skipped, of the 5,211 the roadmap names; 21 min
  36 s on Windows. This is the full-suite run for D-200 to D-202 too.
- Before the stack moved onto 8a81793, on d97460c: 5209 passed, 1 skipped, of
  5,210; 20 min 47 s on Windows.
- `tests/test_doctor_truth.py`: 8 passed; all 8 fail on the code before.
  The doctor tests already in the suite (`test_anthropic_wiring.py`,
  `test_retention.py`, `test_program_search.py`) pass.
- Mutation check (`verify/mutate.py`): 12 mutations of the new lines -- a
  missing credential passing, the variable not looked at, `env_key`'s
  backend read from the vault, one field for every backend, `env_key` not
  named, the proxy shown as it was, every proxy said to have a password, a
  folder outside a repository passing, the workspace's settings left out,
  the variable's proxy whatever the endpoint, every rule counted as the
  machine's, the machine's policy never found -- all killed. The one that
  counted every rule survived at first; the test now gives the user a rule
  of their own.
- Run by hand from the clone with a proxy whose address holds a user name
  and password: the line shows the host and port only.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `8a81793`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/requirements.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/app.py` |
| M | `src/foundry/cli/setup_commands.py` |
| M | `src/foundry/cli/wiring.py` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| M | `src/foundry/core/config.py` |
| M | `src/foundry/core/config_edit.py` |
| A | `src/foundry/core/config_text.py` |
| M | `src/foundry/core/hooks.py` |
| M | `src/foundry/core/runtime.py` |
| A | `tests/fixtures/live_gateway/foundry_chat_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_stream.raw` |
| A | `tests/test_config_encoding.py` |
| A | `tests/test_doctor_truth.py` |
| A | `tests/test_gateway_wire_shapes.py` |
| M | `tests/test_reasoning.py` |
| M | `tests/test_verify_kit.py` |
| M | `tests/test_verify_probes.py` |
| M | `verify/.gitignore` |
| M | `verify/README.md` |
| A | `verify/probes/check_c.py` |
| A | `verify/probes/make_projects_c.py` |
| M | `verify/probes/run_auto.py` |
<!-- auto:end -->
