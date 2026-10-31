# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The first part of a three-part refactoring planned with the user: Foundry runs
straight from a clone, every branch records its changes from main in this
file, and ResearchOS turns macro-first. This branch does the first two and
carries the stages of the third as they are accepted: S1 (tools v6, horizons
and level changes) was built by Foundry in an isolated workspace and landed
as written.

## Changes

- **Run from a clone** ([D-033](docs/decision-log.md)): pytest puts `src/`
  first on the path (`pyproject.toml`), and ResearchOS's puts `../tools`;
  `run-foundry.py` and `foundry.cmd` run the CLI with no install, and
  `ResearchOS/run-researchos.py` runs ResearchOS's; `foundry doctor` prints
  the directory the code was imported from; the subprocess test in
  `tests/test_subsystem_audit.py` uses an absolute `PYTHONPATH`; CI gains a
  `run-from-clone` job that installs only the dependencies; the cloud
  SessionStart hook installs only the dependencies; README Quick start and
  AGENT.md "Running things" describe both paths; OQ-20 records the hash-pinning
  drift; threat-model non-goal 8 covers Foundry working on its own source.
- **Branch notes** ([D-034](docs/decision-log.md)): this file;
  `scripts/delta.py` (`refresh`, `check`, `check --stop-hook`,
  `reset --commit`) and `tests/test_delta_notes.py`; SessionStart and Stop
  hooks in `.claude/settings.json`; a `Branch notes` step in CI's `test` job,
  which now checks out with `fetch-depth: 0`; AGENT.md "Branch notes"; the
  README documentation table.
- **S1, tools v6** (`tools/SPEC.md` "v6: horizons and level changes"):
  `dates.add_months`, `parse_horizon`, `shift_date`, `horizon_end`,
  `last_on_or_before`, `first_on_or_after`; `finance.level_change` and
  `direction`; Foundry's unit tests `tests/test_v6_horizons.py` and
  `tests/test_v6_levels.py`; the hidden suite, now shipped as
  `tests/test_v6_acceptance.py`; a scorecard row in
  `docs/foundry-acceptance.md` and a paragraph in `tools/README.md`.

## Decisions and open points

- The wheel stays the corporate distribution channel (D-002 is unchanged), and
  the offline-install job still proves it. The launcher is not called
  `foundry.py`: at the root it would shadow the wheel when that job runs
  `python -m foundry`.
- The development machine has an editable install of Foundry whose `.pth`
  points at the `blueprint` worktree. Every worktree's own `src/` now comes
  first, but the user should still remove it: `python -m pip uninstall foundry`.
- OQ-20 (open): D-002 promises a hash-pinned wheelhouse, and nothing pins
  hashes.
- The Stop hook rewrites the file list at the end of every turn. CI only warns
  when the committed list is out of date, so a small fixup commit does not
  fail on it.
- Macro pivot, stages S1 to S8: S1 tools v6 (horizons and
  level changes), S2 macro cases, S3 free series (FRED/ALFRED, stooq, CFTC),
  S4 snapshots and their resolution, S5 many-target analysis, S6 forecast now
  and score later, S7 walk-forward by resolution date, S8 a pre-registered
  first run. Views are 1 week, 1 month and 3 months ahead on UST 10y and 2y,
  2s10s, the broad dollar, the S&P 500, WTI, gold, the HY spread, the 10y
  breakeven and VIX.
- The stages land as commits on this branch, not one branch each as
  planned: the user asked for development to continue until done, and each
  stage builds on the one before. Each stage is still one commit (SPEC
  section, Foundry's code and tests, the hidden suite, the scorecard row),
  and its draft lives in `docs/drafts/` only until it lands: the hidden
  suite would fail CI before the code exists, and a build workspace is a copy
  of `tools/` or `ResearchOS/`, which must not hold the suite or the
  reference. Neither pytest configuration collects `docs/`.
- Builds run with a subagent playing the model behind a local relay, as
  v7-v17 did: this machine has no API key, and the OpenClaw gateway's only
  provider refuses the model it is configured with. The relay agent may run
  only the relay's two commands; every tool call it made was checked.

## Verification

- With only the dependencies installed (rich, pytest 8.4.2, anthropic) and
  Foundry itself not installed: Foundry 1575 passed, examples 104, tools 410,
  ResearchOS 705.
- `python run-foundry.py --version` prints `foundry 0.1.0.dev0`;
  `foundry.cmd doctor`, run from another repository, prints this worktree's
  `src/foundry` as its `source`.
- `tests/test_delta_notes.py`: 14 passed. The two hook commands were run by
  hand as the hooks run them: `refresh` exits 0, and `check --stop-hook` exits
  2 while the Summary is unwritten, and 0 with `stop_hook_active` set.
- The full Foundry suite with the new tests, dependencies only: 1589 passed.
- S1: before the build, the hidden suite passed against the reference
  (227) and failed 4 tests against a copy with two deliberate bugs, where
  the fuzzer found 386 mismatches in 20,000 calls. Foundry's build (5
  rounds, 15 tool calls, finish "completed" citing its own `python -m pytest
  -q`, 586 passed) passes all 478 hidden tools tests, agrees with the
  reference on 20,000 fuzzed calls, and its workspace holds no path from
  outside it; the relay agent's 15 tool calls all named workspace paths.
  After landing: tools 813 passed, ResearchOS 705 passed (in a `.venv` with
  rich, pytest 8.4.2 and anthropic 1.17.0).

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/refactoring-plan-foundry-macro-690951`, from main at `603db4f`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `.claude/hooks/session-start.sh` |
| M | `.claude/settings.json` |
| M | `.gitattributes` |
| M | `.github/workflows/ci.yml` |
| M | `AGENT.md` |
| M | `README.md` |
| M | `ResearchOS/pyproject.toml` |
| A | `ResearchOS/run-researchos.py` |
| M | `docs/decision-log.md` |
| M | `docs/foundry-acceptance.md` |
| M | `docs/open-questions.md` |
| M | `docs/threat-model.md` |
| A | `foundry.cmd` |
| M | `pyproject.toml` |
| A | `run-foundry.py` |
| A | `scripts/delta.py` |
| M | `src/foundry/cli/app.py` |
| A | `tests/test_delta_notes.py` |
| M | `tests/test_subsystem_audit.py` |
| M | `tools/README.md` |
| M | `tools/SPEC.md` |
| A | `tools/tests/test_v6_acceptance.py` |
| A | `tools/tests/test_v6_horizons.py` |
| A | `tools/tests/test_v6_levels.py` |
| M | `tools/tools/dates.py` |
| M | `tools/tools/finance.py` |
<!-- auto:end -->
