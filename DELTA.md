# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The verification kit moves into the repository as `verify/` (D-199): the
relay that plays the three protocols, the runner and its scenarios, the
probes, the runners for a real endpoint, and the mutation check. It was in a
session's temporary folder until now. It comes in changed where it had to be:
no path of one machine, a key that is never copied or linked, a verdict and an
exit status for every scenario, a budget for every batch, and a mutation
check that restores files byte for byte.

## Changes

- `verify/`: `relay.py`, `run.py`, `autoreply.py`, `live.py`, `live_batch.py`,
  `show.py`, `mutate.py`, `mcp_client.py`, `tracker_mcp.py`, `notify_log.py`,
  `audit_agent.py`, `scenarios/` (31), `probes/` (two generators, the check of
  the second set's hidden tests, the runner), `README.md`, `.gitignore`.
  Nothing a run wrote is in it.
- `.github/workflows/ci.yml`: a `verify-smoke` job, one scripted scenario a
  protocol against the relay.
- `tests/test_verify_kit.py` (80) and `tests/test_verify_probes.py` (2).
- `tests/test_docs_truth.py`: the kit's README is among the documents whose
  links, suite size and folder name are checked.
- `docs/decision-log.md`: D-199. `docs/roadmap.md`: P12's kit landed, the
  suite's size. `docs/design.md`: the test strategy names the kit.
  `AGENT.md`, `README.md`: where it is, how to run it, and that a live run is
  started by a person.

## Decisions and open points

- A live run takes its key from `FOUNDRY_VERIFY_KEY`, else from Foundry's own
  vault read in the process the user started, else from a prompt; it reaches
  Foundry through `backend.env_key`. Nothing links or copies `auth.json`.
- The probes are generated, not stored; the generators write byte for byte
  what the earlier rounds ran.
- Open: neither live path has met a real endpoint since the key handling
  changed. The Chat Completions batch of P12, which the user starts, is the
  first that will.

## Verification

- Full suite as CI runs it (`python -X warn_default_encoding -m pytest tests -q`):
  5122 passed, 1 skipped, of the 5,123 the roadmap names; 20 min 44 s on
  Windows.
- The three smoke scenarios, from this checkout:
  `python verify/run.py --auto verify/scenarios/smoke-*.json` printed
  `VERDICT ... OK` three times and exited 0.
- Mutation check, `verify/mutate.py` against a copy of the tree: 72 breakages
  of the kit's scripts and scenarios, 6 of the probes and their check, 3 of
  the documents' guard; all 81 killed, none survived, and the copy still
  matched the working tree afterwards.
- `--live` and `--live-chat` against the kit's own relay with a made-up key,
  offline: every request carried a credential, the key was in no file of the
  run's folder, and the cost came from the journals. Neither has met a real
  endpoint (see the open point above).
- A wider search than the suite's of `verify/` and its two test files for
  anything shaped like a credential, a path of one machine, an address or a
  name: nothing but the sample strings the tests check their own patterns
  with, `t@example.com`, a made-up URL in a probe's data, and a 73-byte 4x4
  PNG of three chunks.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `b625e50`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `.github/workflows/ci.yml` |
| M | `AGENT.md` |
| M | `README.md` |
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `tests/test_docs_truth.py` |
| A | `tests/test_verify_kit.py` |
| A | `tests/test_verify_probes.py` |
| A | `verify/.gitignore` |
| A | `verify/README.md` |
| A | `verify/audit_agent.py` |
| A | `verify/autoreply.py` |
| A | `verify/live.py` |
| A | `verify/live_batch.py` |
| A | `verify/mcp_client.py` |
| A | `verify/mutate.py` |
| A | `verify/notify_log.py` |
| A | `verify/probes/check_b.py` |
| A | `verify/probes/make_projects.py` |
| A | `verify/probes/make_projects_b.py` |
| A | `verify/probes/run_auto.py` |
| A | `verify/relay.py` |
| A | `verify/run.py` |
| A | `verify/scenarios/s1-chat-fix.json` |
| A | `verify/scenarios/s10-chat-memories.json` |
| A | `verify/scenarios/s10b-chat-memories.json` |
| A | `verify/scenarios/s11-resp-mcp-server.json` |
| A | `verify/scenarios/s12-chat-retention.json` |
| A | `verify/scenarios/s13-anth-env-skills.json` |
| A | `verify/scenarios/s14-chat-repl-approvals.json` |
| A | `verify/scenarios/s15-anth-repl-goal.json` |
| A | `verify/scenarios/s16-resp-repl-plan-fork.json` |
| A | `verify/scenarios/s16b-resp-repl-plan-fork.json` |
| A | `verify/scenarios/s17-chat-exec-variants.json` |
| A | `verify/scenarios/s18-resp-mcp-http.json` |
| A | `verify/scenarios/s19-anth-repl-compact.json` |
| A | `verify/scenarios/s2-resp-feature.json` |
| A | `verify/scenarios/s20-chat-repl-init.json` |
| A | `verify/scenarios/s21-resp-repl-image.json` |
| A | `verify/scenarios/s22-anth-cli.json` |
| A | `verify/scenarios/s3-anth-resume.json` |
| A | `verify/scenarios/s4-chat-compact.json` |
| A | `verify/scenarios/s5-resp-subagent.json` |
| A | `verify/scenarios/s6-anth-review.json` |
| A | `verify/scenarios/s7-chat-mcp-hooks.json` |
| A | `verify/scenarios/s7b-chat-mcp-hooks.json` |
| A | `verify/scenarios/s8-resp-repl.json` |
| A | `verify/scenarios/s9-anth-refactor-faults.json` |
| A | `verify/scenarios/smoke-anthropic_messages.json` |
| A | `verify/scenarios/smoke-openai_compat.json` |
| A | `verify/scenarios/smoke-responses.json` |
| A | `verify/scenarios/smoke2-chat.json` |
| A | `verify/scenarios/smoke2-resp-error.json` |
| A | `verify/scenarios/smoke2-resp-failed.json` |
| A | `verify/show.py` |
| A | `verify/tracker_mcp.py` |
<!-- auto:end -->
