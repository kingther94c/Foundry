# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

Foundry run against the user's local OpenClaw gateway (D-208), at their
word, and what that found. The checkup and ten of eleven probe projects
passed; the five-step build compacted after every round, as the gateway
counts each request past its 12,000-token limit, and is fixed by compaction
that waits where it would free too little (D-209). The tool calls a GPT
model sent and Foundry refused are taken or explained (D-210).

## Changes

- D-208: `verify/probes/run_auto.py --gateway URL --protocol P`, with a
  token that is never the stored Anthropic key (`verify/live.py`
  `obtain(stored=False)`), runs counted in requests; `verify/README.md`.
  `src/foundry/cli/checkup.py`: another protocol's warnings are said.
- D-209: `src/foundry/core/context.py` `compaction_due()` and the window's
  floor; `src/foundry/core/runtime.py` says once why compaction waits;
  `src/foundry/cli/slash_commands.py` `/status` names the point in force.
- D-210: `list_files` `depth` (`tools/files.py`); `repo_map` takes "" as the
  root (`tools/repomap.py`); `apply_patch` refuses U+0000 (`tools/patch.py`).
- Tests: `tests/test_compaction_floor.py` and
  `tests/test_gateway_tool_shapes.py` (new); `test_verify_kit.py`,
  `test_checkup.py`, `test_measured_context.py`, `test_repo_map.py`.
- Documents: decision log D-208 to D-210; roadmap (status, P13, the suite's
  size).

## Decisions and open points

- Half the threshold is the least a compaction must be able to free; codex's
  `body_after_prefix` and aider's history-only budget were read first.
- The NUL characters came from the model's output: asked directly, the
  gateway passes backticks through.
- Not yet: the third probe set on the gateway (it needs `anthropic`
  installed here), and more than one run per project.

## Verification

- Full suite as CI runs it (PYTHONDONTWRITEBYTECODE unset, -X warn_default_encoding),
  before the documents: 5,339 passed, 1 skipped, 1 failed in 21 min 31 s -- the
  roadmap still quoted the old suite size; with the documents written,
  test_docs_truth.py, test_compaction_floor.py (one test more since) and
  test_verify_kit.py pass. The suite collects 5,342 tests.
- On the code before, the session test compacts four times where it now
  compacts once.
- Mutations of the new lines: D-208 7, D-209 13, D-210 9, each killed.
- On the gateway: f-long completed and passed (33 requests, 3 compactions;
  before: partial, 75, 34); the four runs that had refusals all passed.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/project-review-planning-a401a7`, from main at `3890481`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `docs/decision-log.md` |
| M | `docs/roadmap.md` |
| M | `src/foundry/cli/checkup.py` |
| M | `src/foundry/cli/slash_commands.py` |
| M | `src/foundry/core/context.py` |
| M | `src/foundry/core/runtime.py` |
| M | `src/foundry/core/tools/files.py` |
| M | `src/foundry/core/tools/patch.py` |
| M | `src/foundry/core/tools/repomap.py` |
| M | `tests/test_checkup.py` |
| A | `tests/test_compaction_floor.py` |
| A | `tests/test_gateway_tool_shapes.py` |
| M | `tests/test_measured_context.py` |
| M | `tests/test_repo_map.py` |
| M | `tests/test_verify_kit.py` |
| M | `verify/README.md` |
| M | `verify/live.py` |
| M | `verify/probes/run_auto.py` |
<!-- auto:end -->
