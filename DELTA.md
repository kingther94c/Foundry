# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The re-plan of 2026-11-15 (D-198): what a fresh review of the project found,
the phases P12 to P17 that follow from it, and the documents brought back in
line with the code, with a test that keeps them so.

## Changes

- `docs/decision-log.md`: D-198.
- `docs/roadmap.md`: the status brought to 2026-11-15 (P5 paused, P12 to P17
  under way, what has run against a model and what has not); P9 to P11
  against Claude (D-186) and what was taken from other agents (D-192 to
  D-197), which the body did not have; M3's entry gate as it now stands; a
  new section, "After P11", with what the review found, the phases, and what
  waits on others.
- `docs/open-questions.md`: OQ-6, with what can and cannot come back from the
  corporate machine.
- `README.md`: a status that quotes no figure, custom prompts and skills in
  the folder the code reads, the open points as they are.
- `docs/requirements.md`, `docs/design.md`, `docs/threat-model.md`: headless
  mode is in V1, the Anthropic Messages adapter, the tools added since the
  table, `prompt_toolkit` never taken up, and what `~/.foundry/` stands for.
- `demo/README.md`, `demo/mini_foundry.py` (comments), `AGENT.md`: no size
  for the suite, three adapters.
- `docs/overview.html`: a notice that it is a snapshot of 2026-09.
- `core/tools/registry.py`: the docstring says where the other tools are
  registered.
- Tests: `test_docs_truth.py`, twenty.

## Decisions and open points

- The size of the suite is quoted in one place, the roadmap's status, and
  checked against what the run collected; no other document quotes it.
- The M3 entry gate changes: nothing can be carried off the corporate
  machine, so it is Foundry's own probe passing there (to be built in P15),
  not fixtures brought back.

## Verification

- Full suite as CI runs it (`python -X warn_default_encoding -m pytest tests
  -q`): 5040 passed, 1 skipped. Before the change, at 6a1a71d: 5020 passed,
  1 skipped.
- The size check runs only in a whole run. It passed there; and with the
  roadmap's figure set to 5,042 the whole run stopped at it ("says the suite
  collects 5,042 tests; this run collected 5,041"), after which the roadmap
  was put back byte for byte.
- Mutation check of `test_docs_truth.py`: 22 mutations, of the checks and of
  the documents they read, all killed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
<!-- auto:end -->
