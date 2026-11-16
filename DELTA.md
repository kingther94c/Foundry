# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

A third probe set (D-201), the first part of P13's second step: three
versions of ResearchOS that Foundry built -- v24, v30, v32 -- rebuilt from
this repository's history, each the tree before it with its SPEC section
appended, `tools` beside it, and the acceptance suite that judged it as the
hidden test. Their rounds and cost when they were built are on record, so a
run of them says how Foundry does now against how it did then.

## Changes

- `verify/probes/make_projects_c.py` (new): the generator, from git, bytes
  as committed; it refuses a clone without the history.
- `verify/probes/check_c.py` (new): each suite fails on the tree before the
  version and passes on the tree it landed in, or exit 1.
- `verify/probes/run_auto.py`: a project's `beside/` goes next to the
  workspace; a project whose `requires.txt` names a module that is not
  installed is skipped, with the reason.
- `verify/.gitignore`: `probes/projects-c/`. `verify/README.md`: the third
  set, and how to run it.
- Tests: `tests/test_verify_probes.py`, four; `tests/test_verify_kit.py`, two.
- `docs/decision-log.md`: D-201. `docs/roadmap.md`: the status, P13's probes,
  the suite's size.

## Decisions and open points

- The workspace is ResearchOS itself, so its `FOUNDRY.md` is what Foundry
  reads, and `tools` sits at `../tools`, outside it, as when it was installed.
- The three were chosen for difficulty (10 to 15 rounds then), for being
  offline, and for suites that run in seconds. v30 is judged by the suite it
  landed with: its draft had one wrong assertion.
- They need the Anthropic SDK installed, because one of ResearchOS's own
  tests imports it; without it the runner skips them and says so.
- Open: the rest of the step (a refactor across files, two compactions, the
  output ceiling, visible tests that pass where hidden ones do not) and the
  baselines, which the user starts.

## Verification

- Full suite as CI runs it (`python -X warn_default_encoding -m pytest tests -q`),
  with D-200 beneath, both moved onto d97460c: 5187 passed, 1 skipped, of
  5,188; 21 min 43 s on Windows. Moved again onto 8a81793, the tree collects
  5,189, the figure the roadmap names; the whole suite ran on the new
  stack's tip (D-203's notes).
- Before both moved onto d97460c (CI's annotations), on 6f74e7e with D-200
  beneath: 5181 passed, 1 skipped, of 5,182; 21 min 32 s on Windows.
- `check_c.py` on the three projects: v24 70 failed of 75 on the tree
  before, v30 33 of 53, v32 44 of 49; all passing on the tree each landed
  in. The same suites, run with the whole of each project's tests on the
  tree it landed in, pass (`tests/test_llm.py` left out where the Anthropic
  SDK is not installed, as here).
- Mutation check, `verify/mutate.py` against a copy of the tree that can
  read the repository's history, judged by `tests/test_verify_probes.py`
  and `tests/test_verify_kit.py`: 18 breakages of the generator, the check
  and the runner -- among them bytes as the clone turns them, v30 judged by
  its draft suite, and the scaffold taken from the tree the version landed
  in -- all killed, none survived.
- No real run yet: the baselines are the user's to start.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `8a81793`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/design.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/core/backends/base.py` |
| M | `src/foundry/core/backends/openai_compat.py` |
| M | `src/foundry/core/backends/responses.py` |
| A | `tests/fixtures/live_gateway/foundry_chat_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_chat_toolcall_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_followup_stream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_nonstream.raw` |
| A | `tests/fixtures/live_gateway/foundry_responses_toolcall_stream.raw` |
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
