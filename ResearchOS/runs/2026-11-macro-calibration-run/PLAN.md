# Macro calibration: plan, fixed before any forecast is made

Written and committed before either arm below is run. No macro forecast has
been made when this is written. Amendments, if any, are added at the end
with their dates, before the forecast they affect.

## Why

ResearchOS now forecasts macro snapshots: as of a Friday's close, will each
of ten targets be higher in a week, a month and three months? This first
run asks whether the pipeline works on real data end to end, and how the
forecasts behave before any outcome is used. It cannot show skill. The
model's knowledge cutoff is 2026-06-30, so the snapshots are the eighteen
Fridays from 2026-07-03 to 2026-10-30. When the plan is written
(2026-11-01) about 16 of them have resolved at one week, 13 at one month and
4 at three months, and a snapshot's thirty keys move together (the 10-year
and 2-year yields, the curve, breakevens). The effective sample is far
smaller than the number of keys. So the primary endpoints use no outcome,
as in `2026-10-perspectives-run`.

## Inputs

- `macro-config.json`, the snapshot configuration:
  - The targets: UST 10-year and 2-year yields and the 2s10s curve, the
    broad dollar, the S&P 500, WTI, gold (the NASDAQ gold price index;
    optional), the high-yield spread, the 10-year breakeven and VIX. Each
    has the horizons 1w, 1m and 3m.
  - Nine economic series, each shown as it was published on the
    snapshot's date (ALFRED vintages): payrolls, unemployment, jobless
    claims, CPI, core PCE, GDPNow, Michigan sentiment, the Chicago Fed's
    financial conditions index and the Fed's balance sheet.
  - Speculative positioning in seven futures (CFTC Commitments of Traders,
    each report counted as public three days after its date): 10-year and
    2-year notes, S&P 500, the dollar index, WTI, gold and VIX.
  - The latest FOMC statement and minutes published by the date (from
    federalreserve.gov, dated as released), and the Fed's calendar after it.
- BLS release texts and release dates are not included: bls.gov refuses
  automated requests (403), and that is not worked around. The figures they
  carry reach the snapshot through the economic series.
- `prepare.py fetch` stores the series and `prepare.py build` builds the
  eighteen snapshots. `inputs.sha256` holds the SHA-256 of each `case.json`
  as built when this plan was fixed. The series and the cases are not
  committed, because some of the series may not be redistributed. Every
  source needs no account and no key.
- The snapshots were built on 2026-11-01 from the series fetched that day.
  Each holds about 58,000 to 68,000 characters (about 15k tokens): four
  tables, the FOMC statement and the minutes. The run uses these cases as
  hashed, not a later rebuild.
- Known limits:
  - 2026-07-03 was a market holiday, so most of its levels are those of
    07-02.
  - Eight targets of the 2026-10-30 snapshot had not posted 10-30 when the
    series were fetched. Their levels are the latest published then (10-27
    to 10-29), with that date shown beside each, and `resolve` measures
    their changes from those levels.
  - The text of a fetched page is all of its visible text, so the Fed's
    statement and minutes come with the site's navigation around them
    (about 8,500 characters a page). The model reads past it; it costs
    about 2k cached tokens a page.
  - The broad dollar index is published the following week. Its Friday
    level is shown on the Friday, a few days before it was public. That is
    a value of the date, not any later move.
  - The series files hold today's values, including those after each
    snapshot's date. A snapshot cuts every series at its date (v24).
    Nothing in this plan was chosen by looking at how a target moved after
    a snapshot's date: the targets, horizons and endpoints were fixed in
    the refactoring plan before any series was fetched.

## Arms

- Arm H: `python -m researchos forecast --perspective-mode holistic`. Arm
  I: the same with `--perspective-mode isolated`. Both:
  - the eighteen snapshot ids;
  - the initial macro playbook (equal weights over the ten macro
    perspectives, no lessons);
  - `--knowledge-cutoff 2026-06-30`;
  - the code at the commit that lands v30.
- Each arm runs on its own copy of `cases/` (`arm-holistic/`,
  `arm-isolated/`), checked against `inputs.sha256` first.
- `forecast` seals every forecast and opens no outcome. No playbook is
  trained in this run, so all eighteen snapshots are forecast with the same
  playbook.
- A walk-forward `train` (v30) learns from a key only once it has resolved.
  It is a later run, once every horizon of a block of snapshots has
  resolved.

## Primary endpoints (use no outcome)

All three are computed by `primary.py` from the sealed `prediction.json`
files alone, over the snapshots sealed in both arms.

1. **Completeness.** Every key of every snapshot is sealed in both arms.
   A snapshot that fails (an `LLMError`, a refusal) is reported by name,
   with the reason.
2. **Disagreement.** The mean pairwise correlation of the ten
   perspectives' probabilities, over every (snapshot, key) forecast, in arm
   I against arm H.
   - The difference (I minus H) gets a 95% percentile interval from 2,000
     resamples of the snapshots (`random.Random(0)`, the same snapshots for
     both arms).
   - This is a cluster bootstrap: a snapshot's keys are not independent,
     so whole snapshots are resampled, never single keys.
   - Isolated views count as disagreeing more if the interval lies below
     zero.
   - Alongside it: each perspective's standard deviation, and how often it
     leaves 0.50. A perspective that never leaves 0.50 has no correlation
     and is named as uninformative.
3. **The edge claimed.** How often the judge's probability falls outside
   [0.45, 0.55], by horizon and arm. 0.50 means no edge over the market's
   own price, so this is how often the model claims to know better.

## Secondary (outcomes; exploratory)

Run after both arms are sealed: `prepare.py resolve arm-holistic` and
`prepare.py resolve arm-isolated`, then `python -m researchos score` on
each arm.

1. Brier scores of the judge, the weighted and the uniform forecast,
   against three baselines:
   - a coin (0.25);
   - trend: 0.6 when the key's trailing change over the same horizon is
     above 0, 0.4 when below, 0.5 when it is 0 or unknown;
   - mean reversion: the mirror of trend.

   Each is given by horizon and by target.
2. `compare --metric brier_judge`, arm I against arm H.
3. Every `blend` rule replayed with `--perspectives macro`.
4. `agreement --perspectives macro` on the scored records.

They are scored at three dates, fixed now:
- **2026-11-02**: what has resolved by then;
- **2026-12-07**: every 1-month key has resolved;
- **2027-02-08**: every 3-month key has resolved.

With eighteen snapshots whose keys move together, none of these can show a
difference of plausible size. They are reported as underpowered whatever
they show.

## Cost

Each snapshot costs eleven calls per arm: ten perspectives and the judge.
The documents (the four tables, the statement and the minutes, about 15k
tokens) are a prefix the calls share and the cache serves. That comes to
about $0.60 a snapshot per arm, and about $22 for both arms. Each arm is
capped at $15. An arm that reaches its cap stops, and only the snapshots it
finished are reported.

## Not run here

The forecasts need the Anthropic API, and the machine this plan was written
on has no API key. Everything before the forecasts (`prepare.py fetch`,
`build`, `hashes`) was done on 2026-11-01. The run starts at `forecast`.

## Amendment, 2026-11-01, before any forecast

Nothing above changes what is measured. This adds what a dry run found and
how the cap is kept.

- `dry_run.py` ran this plan end to end with a fake model, on copies of the
  eighteen hashed snapshots. Both arms sealed all eighteen in 198 calls
  each. `primary.py`, `resolve`, `score`, `compare`, every `blend` rule and
  `agreement` ran. On 2026-11-01, 330 of the 540 keys had resolved (162 at
  one week, 129 at one month, 39 at three months), and `train` learned
  from the three snapshots whose keys had all resolved. What a fake model
  forecasts says nothing about a model.
- The shared prefix is 58,191 to 68,442 characters (about 15k to 17k
  tokens). The judge's prompt is up to 7,869 characters and a
  perspective's up to 1,884.
  - The input side costs about $0.22 a snapshot per arm: the prefix is
    written to the cache once and read ten times.
  - The output, thinking included, is the rest: about $0.28 at 1,000
    output tokens a call, and $0.83 at 3,000.
  - The estimate of $0.60 stands, within about $0.50 to $1.05. At the top
    of that range an arm reaches its $15 cap after fourteen snapshots.
- The CLI does not report spend, so the cap is kept by hand:
  - Each arm is forecast in date order, three snapshots per `forecast`
    call, the arms alternating (H 1-3, I 1-3, H 4-6, ...).
  - The spend is read from the Anthropic Console after each call. An arm
    stops before a call that could take it past its cap.
  - Alternating keeps the two arms' snapshots alike if one must stop.
  - How the snapshots are grouped changes no forecast: the playbook is
    frozen, and each snapshot is analyzed on its own.
- The code is that of `50c7a37`, which lands v30. The commits after it
  change only documentation and this folder.
- Later the same day, still before any forecast: v31 makes `snapshot`
  keep only a fetched page's main content, which takes about 8,300
  characters of site navigation off each Fed page. These snapshots were
  built before v31 and are used as hashed, navigation included, so the
  known limit above stands for this run. `forecast`, `resolve` and `score`
  are the same at v31 as at `50c7a37`.
