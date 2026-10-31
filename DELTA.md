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
from free sources), S4 in three parts (v23 snapshot tables, v24
building a snapshot, v25 resolving it) S5 in two parts (v26 the
many-target analysis, v27 scoring and reflecting on it) and the first part
of S6 (v28, forecast now and score later) were built by Foundry in
isolated workspaces and landed as written (v23 but for one word of a
docstring, v28 but for one of its tests, which needed `tools` installed).

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
- **v25, the last part of S4** (`ResearchOS/SPEC.md` "Resolving a
  snapshot (v25)"): `resolve_cases` and `resolve_case` in
  `researchos/snapshot.py` and the `resolve` command in `__main__.py`
  (a key resolves once its series has an observation on or after the
  horizon's end, from the level the case recorded; outcomes once written
  never change; `prediction.json` is never opened). Foundry's
  `tests/test_resolve_snapshot.py`, the hidden suite as
  `tests/test_resolve_snapshot_acceptance.py`, a scorecard row and a
  README paragraph.
- **v26, the first part of S5** (`ResearchOS/SPEC.md` "Many targets, one
  analysis (v26)"): the new module `researchos/targets.py` with ten macro
  perspectives and what each looks at, the prompts, strict schemas built
  from a case's targets, and `analyze_targets` (one call per perspective
  covering every target and horizon, then a judge; holistic or isolated,
  as v20). Foundry's `tests/test_targets.py`, the hidden suite as
  `tests/test_targets_acceptance.py`, a scorecard row and a README
  paragraph.
- **v27, the second part of S5** (`ResearchOS/SPEC.md` "Scoring and
  reflecting on many targets (v27)"), in `researchos/targets.py`:
  `score_targets` (one record per resolved key, with `case_id`
  `<case>/<key>` and the keys `compare`, `blend` and `agreement` read,
  plus the combined probabilities and what the target did) and
  `reflect_targets` (one reflector call over the resolved keys, the
  lessons read as `reflect` reads its own). Foundry's
  `tests/test_score_reflect_targets.py`, the hidden suite as
  `tests/test_target_scoring_acceptance.py`, a scorecard row and a README
  paragraph.
- **v28, the first part of S6** (`ResearchOS/SPEC.md` "Forecast now,
  score later (v28)"). `loop.forecast` seals
  forecasts and opens no outcome: a macro case needs a date after the
  knowledge cutoff, and a forecast is sealed once. `loop.score_sealed`
  scores what was sealed against what has resolved and calls no model.
  Also `loop.load_prediction`, `Playbook.initial(perspectives)`,
  `PREDICTION_FILE` moved to `cases` (so `loop` can import `targets`),
  `train` and `evaluate` refusing a macro case, and the `forecast` and
  `score` commands. Foundry's `tests/test_forecast_score.py` (its
  subprocess import test now passes the path of `tools` on, and
  `ResearchOS/FOUNDRY.md` says a child process does not inherit pytest's
  `pythonpath`), the hidden suite as
  `tests/test_forecast_score_acceptance.py`, a scorecard row and a README
  paragraph.
- **Draft of v29, the second part of S6** (`docs/drafts/researchos-v29/`):
  the SPEC section "Blend and agreement over the macro perspectives (v29)".
  `blend.replay` and `blend.agreement` take the perspectives to read, and
  `replay` learns from a record only once it has resolved: before a batch
  it learns what resolved earlier, after it what resolved by then. Without
  either, every result is what it was. `blend` and `agreement` take
  `--perspectives {earnings,macro}`. 36 hidden tests, a reference
  `blend.py` with a patch to `__main__.py`, and `check.py`.

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
  v24 building a snapshot (`snapshot`), v25 resolving it (`resolve`). S5 is
  two as well: v26 the analysis, v27 scoring and reflecting. The later
  stages keep their scope and take the next numbers. S6 is two as well: v28
  forecast now and score later, v29 blend and agreement over the macro
  perspectives. S7, walking forward by the date each outcome resolves, is
  v30. Views are 1 week, 1 month and 3 months ahead on UST 10y and 2y,
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
- v25: Foundry's build (7 rounds, 28 tool calls, finish "completed"
  citing its own run, 1679 passed) passes all 834 hidden ResearchOS tests
  and agrees with the reference on 3,000 fuzzed calls. The workspace
  holds no path from outside it; to read `tools`' sources it added a
  test that printed them and deleted it in its next patch. The relay
  agent's 22 calls were 8 relay commands, 10 writes to its replies, 3
  reads of the relay's saved output and its report. After landing:
  ResearchOS 1723 passed; v26's suite passes against its reference on
  top (1782).
- v26: Foundry's build (7 rounds, 26 tool calls, finish "completed"
  citing its own run, 1795 passed) passes all 893 hidden ResearchOS tests
  and agrees with the reference on 9,000 fuzzed calls. The workspace holds
  no path from outside it; it read `tools.stats` through a test it then
  deleted. The relay agent's 20 calls were 8 relay commands, 9 writes to
  its replies, 2 reads of the relay's saved output and its report. Its
  `targets.py` imports `snapshot.format_level` at the top, so v28 moves
  `PREDICTION_FILE` to `cases` before `loop` imports `targets`. After
  landing: ResearchOS 1854 passed; v27's suite passes against its
  reference appended to Foundry's `targets.py` (1881).
- v27 draft: its suite passes against its reference (1809, with v26's
  reference and suite) and fails 4 against a copy with three deliberate
  bugs; its fuzzer finds 2,815 mismatches in 9,000 calls there and none
  between the reference and itself.
- v27: Foundry's build (8 rounds, 31 tool calls, finish "completed"
  citing its own run, 1882 passed) passes all 920 hidden ResearchOS tests
  and agrees with the reference on 9,000 fuzzed calls. The workspace holds
  no path from outside it. The relay agent's 25 calls were 9 relay
  commands, 10 writes and 3 edits of its replies (one patch file changed
  with the Edit tool, where the prompt asked it to write the file again),
  2 reads of the relay's saved output and its report. Its workspace's
  README lacks v26's paragraph only because the scaffold was copied
  before that paragraph was written; the README was not taken from it.
  After landing: ResearchOS 1909 passed; v28's suite passes against its
  reference on top (1967).
- v28: Foundry's build (7 rounds, 34 tool calls, finish "completed"
  after its own run, 1948 passed) passes all 978 hidden ResearchOS tests
  and agrees with the reference on 1,200 fuzzed calls. The workspace holds
  no path from outside it. The relay agent's 25 calls were 8 relay
  commands, 8 writes of its replies, 7 reads of the relay's saved output
  or its own patch file, and its report; no Edit. Landing found 4 of
  Foundry's tests failing in a clone: a subprocess that imports each
  module found `tools` in the workspace (on PYTHONPATH) but not here
  (pytest's `pythonpath` is not inherited). The test now passes the path
  on. After landing: ResearchOS 2006 passed; v29's suite passes against
  its reference on top (2042).
- v29 draft: its suite passes against its reference (2003, with v28's
  reference and suite) and fails 6 against a copy with three deliberate
  bugs; its fuzzer finds 2,752 mismatches in 8,000 calls there and none
  between the reference and itself.

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
| M | `ResearchOS/FOUNDRY.md` |
| M | `ResearchOS/README.md` |
| M | `ResearchOS/SPEC.md` |
| M | `ResearchOS/pyproject.toml` |
| M | `ResearchOS/researchos/__main__.py` |
| M | `ResearchOS/researchos/cases.py` |
| M | `ResearchOS/researchos/loop.py` |
| M | `ResearchOS/researchos/masking.py` |
| M | `ResearchOS/researchos/playbook.py` |
| A | `ResearchOS/researchos/series.py` |
| A | `ResearchOS/researchos/snapshot.py` |
| A | `ResearchOS/researchos/targets.py` |
| A | `ResearchOS/run-researchos.py` |
| A | `ResearchOS/tests/test_build_snapshot.py` |
| A | `ResearchOS/tests/test_build_snapshot_acceptance.py` |
| A | `ResearchOS/tests/test_forecast_score.py` |
| A | `ResearchOS/tests/test_forecast_score_acceptance.py` |
| A | `ResearchOS/tests/test_macro_cases.py` |
| A | `ResearchOS/tests/test_macro_cases_acceptance.py` |
| A | `ResearchOS/tests/test_resolve_snapshot.py` |
| A | `ResearchOS/tests/test_resolve_snapshot_acceptance.py` |
| A | `ResearchOS/tests/test_score_reflect_targets.py` |
| A | `ResearchOS/tests/test_series.py` |
| A | `ResearchOS/tests/test_series_acceptance.py` |
| A | `ResearchOS/tests/test_snapshot.py` |
| A | `ResearchOS/tests/test_snapshot_tables_acceptance.py` |
| A | `ResearchOS/tests/test_target_scoring_acceptance.py` |
| A | `ResearchOS/tests/test_targets.py` |
| A | `ResearchOS/tests/test_targets_acceptance.py` |
| M | `docs/decision-log.md` |
| A | `docs/drafts/researchos-v29/SPEC-section.md` |
| A | `docs/drafts/researchos-v29/check.py` |
| A | `docs/drafts/researchos-v29/reference/blend.py` |
| A | `docs/drafts/researchos-v29/reference/main_patch.py` |
| A | `docs/drafts/researchos-v29/test_blend_macro_acceptance.py` |
| A | `docs/drafts/researchos-v30/SPEC-section.md` |
| A | `docs/drafts/researchos-v30/reference/loop_append.py` |
| A | `docs/drafts/researchos-v30/reference/playbook_append.py` |
| A | `docs/drafts/researchos-v30/test_walk_forward_acceptance.py` |
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
