# Delta from main

This file records how this branch differs from main: what changed, why, and
how it was checked. On main it is this empty template, and
`python scripts/delta.py reset --commit` restores it as the last commit
before a branch lands. See AGENT.md, "Branch notes".

## Summary

The first macro run's forecasts move to an environment that has an
Anthropic API key, since this machine has none. The run folder gains what
that environment needs: a tool that makes the forecasts and keeps the cap
by metering every call, a scorer for the secondary endpoints that commits
no series level, a runbook, and a dated PLAN.md amendment made before any
forecast. The snapshots are rebuilt there at v31 and hashed before the
first forecast, because the cases may not go into the public repository.

## Changes

- `ResearchOS/runs/2026-11-macro-calibration-run/`:
  - `forecast_arm.py` (new): `setup`, `next`, `all`, `status`, `restore`.
    One `forecast` per snapshot, arms alternating three at a time. Every
    call's usage is metered at claude-opus-5-5's prices into
    `spend-<arm>.jsonl`, with the model asked for and the model that
    served it. The $15 cap is checked before each snapshot. A model
    failure goes to `failed-<arm>.jsonl` and is not retried; a failed API
    request stops the run, which then resumes. Snapshots served by
    another model are named for `primary.py --exclude`.
  - `secondary.py` (new): one scoring a day, not before 2026-11-02, once
    both arms are finished. It fetches the target series, resolves copies
    of the arms in `scores/<date>/`, runs score, compare, every blend
    rule, agreement and the coin, trend and mean-reversion baselines, and
    writes the level-free `secondary-<date>.json`.
  - `prepare.py`: `check` (six documents and the required targets in
    every snapshot), `hashes` also writes `inputs-parts.sha256`,
    `compare FILE`, warnings no longer swallowed, `fetch_fred(folder=)`.
  - `primary.py`: `--exclude`.
  - `inputs.sha256` is renamed `inputs-2026-11-01.sha256` (the v30
    build). `inputs-2026-11-01-v31-parts.sha256` holds the part hashes of
    a v31 rebuild made here.
  - `.gitignore`: only the arms' `prediction.json` files are committable,
    and `scores/` and logs are ignored.
  - `PLAN.md`: a second dated amendment. `RUNBOOK.md` (new): the steps
    for the environment with the key.
- `ResearchOS/README.md`: the run's entry points at the runbook.

## Decisions and open points

- The cases are rebuilt, not carried over: the repository is public, and
  some series may not be redistributed. The v31 rebuild made here matches
  the v30 build in every table and target and differs only in the two Fed
  pages.
- The server-side fallback stays on, as `llm.py` has it. Its effect is
  measured instead: the serving model is recorded per call, and the
  primary endpoints are reported with and without the snapshots it served.
- Open: the forecasts themselves, which need the API key (`RUNBOOK.md`).

## Verification

- Offline, with a fake client that reports usage (scratch tests, not
  committed):
  - `forecast_arm.py`: setup, then a dropped connection that stops the
    run and records nothing; resuming seals 17 snapshots per arm. One
    refusal is recorded as failed. One fallback snapshot is named, the cap
    stops both arms after three snapshots at $3, and the cost per call
    matches the rates by hand.
  - `restore`: from a fresh-clone state it brings back 72 files, leaves
    every `prediction.json` byte for byte, resumes the run, and refuses a
    tampered `case.json`.
  - `secondary.py`: it refuses before 2026-11-02 and while an arm is
    unfinished. It scores 330 keys per arm, its tables agree with
    `score`, and its summary holds no level key.
  - `primary.py --exclude` drops the named snapshots from both arms.
- `prepare.py check` passes on all eighteen snapshots. `git check-ignore`
  confirms that only `prediction.json`, the spend logs and the summaries
  are committable.

<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->
`claude/refactoring-plan-foundry-macro-690951`, from main at `2bbae48`.

Files that differ from main (the working tree; this file left out):

| Status | Path |
|---|---|
| M | `ResearchOS/README.md` |
| M | `ResearchOS/runs/2026-11-macro-calibration-run/.gitignore` |
| M | `ResearchOS/runs/2026-11-macro-calibration-run/PLAN.md` |
| A | `ResearchOS/runs/2026-11-macro-calibration-run/RUNBOOK.md` |
| A | `ResearchOS/runs/2026-11-macro-calibration-run/forecast_arm.py` |
| A | `ResearchOS/runs/2026-11-macro-calibration-run/inputs-2026-11-01-v31-parts.sha256` |
| A | `ResearchOS/runs/2026-11-macro-calibration-run/inputs-2026-11-01.sha256` |
| D | `ResearchOS/runs/2026-11-macro-calibration-run/inputs.sha256` |
| M | `ResearchOS/runs/2026-11-macro-calibration-run/prepare.py` |
| M | `ResearchOS/runs/2026-11-macro-calibration-run/primary.py` |
| A | `ResearchOS/runs/2026-11-macro-calibration-run/secondary.py` |
<!-- auto:end -->
