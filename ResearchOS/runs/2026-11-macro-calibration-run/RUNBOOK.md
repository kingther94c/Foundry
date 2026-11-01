# Runbook: this run's forecasts, in an environment with an API key

For whoever makes the forecasts of `PLAN.md` where `ANTHROPIC_API_KEY` is
set (written 2026-11-01, before any forecast). Read `PLAN.md` first, its
two amendments above all: every step here is fixed by it. If a step cannot
be done as written, stop and report rather than work around it.

## Rules

- Never print, echo, log or write the API key, and never paste it anywhere.
  Check that it is set only with the command in step 1.
- Change no code under `researchos/` or `tools/`. Do not change
  `macro-config.json`, the cap, the rates, the cutoff or the endpoints.
  A fix that seems needed is reported, not made.
- Never edit or delete a `prediction.json`, and never forecast a snapshot
  twice. A failed snapshot is not retried by hand.
- Run `secondary.py` only after both arms are finished; nothing else here
  reads an outcome.
- Fetch only what `prepare.py` fetches. A source that refuses (403, a
  CAPTCHA, a rate limit) is not worked around.
- Commit nothing that carries series levels. `.gitignore` keeps out
  `series/`, `cases/`, the case files in the arms and `scores/`. Commit with
  `git add .` from this folder, and read `git status --short .` first.
- Push each commit as it is made. Work on `main` and `git push origin main`.
  If the environment must work on a branch of its own, push that branch
  instead and keep its `DELTA.md` current (`AGENT.md`, "Branch notes").

## 1. Set up and verify (no cost)

Python 3.12 is required.

```bash
git clone https://github.com/kingther94c/Foundry.git && cd Foundry   # or: git checkout main && git pull
python3.12 -m venv .venv && . .venv/bin/activate
pip install anthropic pytest
cd ResearchOS && python -m pytest -q          # all pass: 2263 on 2026-11-01
cd runs/2026-11-macro-calibration-run
python -c "import os; print('ANTHROPIC_API_KEY is', 'set' if os.environ.get('ANTHROPIC_API_KEY') else 'NOT set')"
```

## 2. Rebuild the snapshots and fix them in git (no API call)

```bash
python prepare.py fetch       # every series: FRED, ALFRED vintages, CFTC; a few minutes
python prepare.py build       # the eighteen snapshots in cases/, fetching the Fed's pages
python prepare.py check       # must name no snapshot MISSING anything
python prepare.py hashes      # inputs.sha256 and inputs-parts.sha256
python prepare.py compare inputs-2026-11-01-v31-parts.sha256
```

- If `fetch` or `build` fails, or `check` reports MISSING, stop and report
  the error. Do not hash or forecast.
- `compare` should find most snapshots the same. Differences are expected
  only where a series has posted since 2026-11-01: the dashboard and the
  targets of 2026-10-30, and perhaps 2026-10-23 (the broad dollar is
  published a week late). Report the whole `compare` output either way.

Keep a private copy of the inputs outside the repository, then commit the
hashes:

```bash
tar czf ~/macro-run-inputs.tgz series cases
git add . && git status --short .     # only inputs.sha256 and inputs-parts.sha256
git commit -m "Macro run: the snapshots rebuilt at v31, hashed before any forecast"
git push origin main
```

That push is the pre-registration of the inputs. It must happen before
step 4.

## 3. Dry run (no cost)

```bash
python dry_run.py /tmp/macro-dry-run
```

This checks `cases/` against the new `inputs.sha256` and runs every step
with a fake model. It must end with its `train` line.

## 4. Forecast (costs money: about $16 to $30, at most $15 an arm)

```bash
python forecast_arm.py setup
nohup python forecast_arm.py all > forecast.log 2>&1 &
tail -f forecast.log                # one line per snapshot
python forecast_arm.py status       # at any time
```

- Each snapshot is eleven calls in a row, so expect several minutes a
  snapshot and some hours in all.
- After the first snapshot, compare its cost in `forecast.log` with the
  Console's usage page if it can be seen. A large gap means the rates are
  wrong: stop the run and report.
- Every few snapshots, and before the environment might be recycled,
  commit what is sealed:

  ```bash
  git add . && git status --short . && git commit -m "Macro run: sealed forecasts so far" && git push origin main
  ```

- If the log says "The run stops here", read the error (the key, credit, a
  rate limit, the network). Fix the cause, never the code, and start the
  same command again (`>> forecast.log`). It resumes at that snapshot.
- If the environment was recycled: clone, set up as in step 1, unpack the
  private copy (`tar xzf ~/macro-run-inputs.tgz`), run
  `python forecast_arm.py restore`, then start `all` again. The committed
  spend logs keep the cap counting.
- An arm that stops at its cap stays stopped; the cap is not raised.
- It is finished when `status` shows no snapshot left in either arm, or an
  arm stopped at its cap.

## 5. Primary endpoints (no outcome)

```bash
python primary.py --out primary.json
python forecast_arm.py status
```

If `status` names snapshots served by another model, also run
`python primary.py --exclude <those ids> --out primary-without-fallbacks.json`.
Then commit and push:

```bash
git add . && git status --short . && git commit -m "Macro run: forecasts sealed; primary endpoints" && git push origin main
```

## 6. Secondary endpoints (on or after 2026-11-02)

```bash
python secondary.py
git add . && git status --short . && git commit -m "Macro run: secondary endpoints, first scoring" && git push origin main
```

Run the same again on or after 2026-12-07 and on or after 2027-02-08. In a
new environment: step 1, unpack the private copy, `python forecast_arm.py
restore`, then `python secondary.py`.

## Report

When a step ends the work for now, report:

- the commits pushed (hash and message);
- the output of `prepare.py check` and `compare`;
- `forecast_arm.py status`: sealed, failed with their reasons, spend per arm
  (and the Console's figure, if seen), and any snapshot served by another
  model;
- from `primary.json`: each arm's mean correlation, the difference with its
  interval, and how often the judge leaves [0.45, 0.55] by horizon;
- from `secondary-<date>.json`, if made: n, pending, and the Brier scores
  of the judge, the weighted and the uniform forecast against the coin,
  trend and mean reversion, by horizon;
- anything done differently from this runbook, and why.
