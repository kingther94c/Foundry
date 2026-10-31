# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The first part of a three-part refactoring planned with the user: Foundry runs
straight from a clone, every branch records its changes from main in this
file, and ResearchOS turns macro-first. This branch does the first two and
carries the stages of the third as they are accepted. S1 (tools v6, horizons
and level changes), S2 (ResearchOS v21, macro cases), S3 (v22, series
from free sources), v23 (snapshot tables) and v24 (building a snapshot),
the first two parts of S4, were built by Foundry in isolated workspaces and
landed as written (v23 but for one word of a docstring).

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
- **S2, ResearchOS v21** (`ResearchOS/SPEC.md` "Macro cases: as-of
  snapshots, per-target outcomes (v21)"): `Target`, `TargetOutcome`,
  `target_key`, `check_targets`, `target_keys`, `macro_case`, `save_outcome`,
  the resolution manifest (`resolution_manifest`, `save_resolution`,
  `load_resolution`, `unresolved_targets`) in `cases.py`; `Case.domain` and
  `Case.targets`; an optional `Outcome.abnormal_return_1d` and
  `Outcome.targets`; `masking.unmasked`; `loop.require_after_cutoff` and
  `loop.single_domain`; `build` prints the outcome as `outcome.json` holds
  it. Foundry's `tests/test_macro_cases.py`, the hidden suite as
  `tests/test_macro_cases_acceptance.py`, a scorecard row and a README
  paragraph.
- **S3, ResearchOS v22** (`ResearchOS/SPEC.md` "Series from free sources
  (v22)"): `researchos/series.py` (`fred_url`, `cftc_url`, `parse_fred_csv`,
  `parse_cftc`, `save_series`, `load_series`, `fetch_text`, `fetch_fred`,
  `fetch_cftc`) and the `fetch-series` command in `__main__.py`. Foundry's
  `tests/test_series.py`, the hidden suite as
  `tests/test_series_acceptance.py`, a scorecard row and a README paragraph.
- **v23, the first part of S4** (`ResearchOS/SPEC.md` "Snapshot tables
  (v23)"): `researchos/snapshot.py` (`trailing_change`, `format_level`,
  `format_change`, `target_table`, `context_table`, `positioning_table`,
  `calendar_list`). Foundry's `tests/test_snapshot.py`, the hidden suite as
  `tests/test_snapshot_tables_acceptance.py`, a scorecard row and a README
  paragraph.
- **v24, the second part of S4** (`ResearchOS/SPEC.md` "Building a
  snapshot (v24)"): `load_macro_config` and `build_snapshot` in
  `researchos/snapshot.py` and the `snapshot` command in `__main__.py`.
  Foundry's `tests/test_build_snapshot.py`, the hidden suite as
  `tests/test_build_snapshot_acceptance.py`, a scorecard row and a README
  paragraph. The SPEC gained one sentence: when every target is left out,
  the snapshot is refused before anything is fetched.
- **Draft of v25** (`docs/drafts/researchos-v25/`): the SPEC section
  "Resolving a snapshot (v25)" (`resolve_cases`, `resolve_case` and the
  `resolve` command: a key resolves once its series has an observation on
  or after the horizon's end, from the level the case recorded; outcomes
  once written never change; `prediction.json` is never opened), 44 hidden
  tests, a reference and `check.py`.
- **Draft of v26, the first part of S5** (`docs/drafts/researchos-v26/`):
  the SPEC section "Many targets, one analysis (v26)": a new module
  `researchos/targets.py` with ten macro perspectives and what each looks
  at, the prompts, strict schemas built from a case's targets, and
  `analyze_targets` (one call per perspective covering every target and
  horizon, then a judge; holistic or isolated, as v20). 59 hidden tests, a
  reference and `check.py`. S5 is split in two: v27 will score and reflect.

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
  first run. S4 was too big for one build (about 320 lines, near the
  25-round budget), so it is three SPEC sections: v23 the snapshot tables,
  v24 building a snapshot (`snapshot`), v25 resolving it (`resolve`). The
  later stages keep their scope and take the next numbers: S5 is v26, S6
  v27, S7 v28. Views are 1 week, 1 month and 3 months ahead on UST 10y and 2y,
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
- S3 reads every series without an account: FRED's `fredgraph.csv` for the
  current values, ALFRED's `alfredgraph.csv?vintage_date=` for a series as
  it was known on a date, and the CFTC's Socrata JSON for positioning. All
  three were checked against what they returned on 2026-10-31 (an empty
  value on a bond-market holiday, the `<ID>_<YYYYMMDD>` vintage column,
  counts as strings, dates as `2026-10-27T00:00:00.000`) and those samples
  are in the suite. So the plan's FRED API key is not needed: no key is
  stored, sent or tested. stooq now answers with a JavaScript proof-of-work
  check, which is bot detection and is not worked around; gold comes from
  FRED's NASDAQ gold index (`NASDAQQGLDI`) instead of stooq's `XAUUSD`.
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
- S2: the hidden suite passed against the reference (878 with the existing
  tests) and failed 9 against a copy with two deliberate bugs. Foundry's
  build (9 rounds, 39 tool calls, finish "completed" citing its own run, 755
  passed) passes all 448 hidden ResearchOS tests. On about 22,000 fuzzed
  calls it agrees with the reference except in `load_case` of a target
  whose `horizons` is not a list or whose `trailing` is not a dict, which
  `check_targets` refuses or does not check: the reference raises there and
  cannot read back what `save_case` wrote, Foundry's keeps the value. The
  workspace holds no path from outside it, and the relay agent's 39 tool
  calls named only workspace paths. After landing: ResearchOS 928 passed,
  and the S3 draft's suite passes against its reference on top (1116).
- S3: Foundry's build (6 rounds, 23 tool calls, finish "completed" citing
  its own run, 1115 passed) passes all 636 hidden ResearchOS tests and
  agrees with the reference on 17,266 fuzzed calls. The workspace holds no
  path from outside it, and the relay agent's 23 tool calls named only
  workspace paths. After landing: ResearchOS 1303 passed, tools 813.
- S4 drafts, on top of S3: v23's suite passes against its reference
  (1382) and fails 5 tests against a copy with two deliberate bugs (2,461
  fuzz mismatches); v24's passes against its reference (1457), fails 20
  against a copy with three deliberate bugs, and its fuzzer finds 344
  mismatches there and none between the reference and itself.
- v23: Foundry's build (6 rounds, 18 tool calls, finish "completed" citing
  its own run, 1382 passed) passes all 715 hidden ResearchOS tests and
  agrees with the reference on 21,000 fuzzed calls; the workspace holds no
  path from outside it. The relay agent reported, and its transcript
  shows, one command outside the relay: a five-line Python script that
  moved a file's deletion from one of its patch files to another. It
  touched only that reply file; every other call was a relay command, a
  write to its replies or a read of the relay's saved output. Review
  changed one word of a docstring (`_observed` returns `[]`, not None).
  After landing: ResearchOS 1461 passed, and v24's suite passes against
  its reference appended to Foundry's `snapshot.py` (1536).
- v25 draft: its suite passes against its reference (1580, with v24's
  reference and suite) and fails 17 against a copy with two deliberate
  bugs; its fuzzer finds 1,443 mismatches in 3,000 calls there and none
  between the reference and itself.
- v24: Foundry's build (10 rounds, 42 tool calls, finish "completed"
  citing its own run, 1590 passed) passes all 790 hidden ResearchOS tests.
  On 2,536 fuzzed calls it agrees with the reference but in 2, both a
  config whose every target is optional and has no data: both refuse and
  write nothing, but the reference fetched a page first. The workspace
  holds no path from outside it; the relay agent's 29 calls were 11 relay
  commands, 14 writes to its replies, 3 reads of the relay's saved output
  and its report. Its `snapshot.py` imports `PREDICTION_FILE` from
  `loop`; if `loop` ever imports `snapshot`, that constant moves to
  `cases`. After landing: ResearchOS 1665 passed, and v25's suite passes
  against its reference appended to Foundry's `snapshot.py` (1709).
- v26 draft: its suite passes against its reference (1724) and fails 3
  against a copy with two deliberate bugs; its fuzzer finds 2,619
  mismatches in 9,000 calls there and none between the reference and
  itself.

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
| M | `ResearchOS/README.md` |
| M | `ResearchOS/SPEC.md` |
| M | `ResearchOS/pyproject.toml` |
| M | `ResearchOS/researchos/__main__.py` |
| M | `ResearchOS/researchos/cases.py` |
| M | `ResearchOS/researchos/loop.py` |
| M | `ResearchOS/researchos/masking.py` |
| A | `ResearchOS/researchos/series.py` |
| A | `ResearchOS/researchos/snapshot.py` |
| A | `ResearchOS/run-researchos.py` |
| A | `ResearchOS/tests/test_build_snapshot.py` |
| A | `ResearchOS/tests/test_build_snapshot_acceptance.py` |
| A | `ResearchOS/tests/test_macro_cases.py` |
| A | `ResearchOS/tests/test_macro_cases_acceptance.py` |
| A | `ResearchOS/tests/test_series.py` |
| A | `ResearchOS/tests/test_series_acceptance.py` |
| A | `ResearchOS/tests/test_snapshot.py` |
| A | `ResearchOS/tests/test_snapshot_tables_acceptance.py` |
| M | `docs/decision-log.md` |
| A | `docs/drafts/researchos-v25/SPEC-section.md` |
| A | `docs/drafts/researchos-v25/check.py` |
| A | `docs/drafts/researchos-v25/reference/main_patch.py` |
| A | `docs/drafts/researchos-v25/reference/snapshot_append.py` |
| A | `docs/drafts/researchos-v25/test_resolve_snapshot_acceptance.py` |
| A | `docs/drafts/researchos-v26/SPEC-section.md` |
| A | `docs/drafts/researchos-v26/check.py` |
| A | `docs/drafts/researchos-v26/reference/targets.py` |
| A | `docs/drafts/researchos-v26/test_targets_acceptance.py` |
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
