# Macro calibration: the first macro forecasts

The plan ([PLAN.md](PLAN.md)) and its two amendments were committed before
either arm made a forecast. The forecasts were made on 2026-11-01 in another
environment, which had the API key, by [RUNBOOK.md](RUNBOOK.md). Files:
- the sealed forecasts, `arm-holistic/*/prediction.json` and
  `arm-isolated/*/prediction.json`;
- every API call's usage and serving model ([spend-holistic.jsonl](spend-holistic.jsonl),
  [spend-isolated.jsonl](spend-isolated.jsonl));
- the primary endpoints ([primary.json](primary.json), by
  [primary.py](primary.py) from the sealed forecasts alone);
- the first scoring of the secondary endpoints
  ([secondary-2026-11-02.json](secondary-2026-11-02.json), by
  [secondary.py](secondary.py)).

The snapshots themselves are not here, because some of their series may not
be redistributed. [inputs.sha256](inputs.sha256) fixes them, and
`prepare.py` rebuilds them.

**Result.** Asked how their own factor alone moves each target, the ten macro
perspectives disagree, as the seven earnings perspectives did. Over the 540
(snapshot, key) forecasts of each arm, their probabilities correlate at 0.045
on average, against 0.654 when each gives its overall view. The difference is
-0.61, with a 95% interval of -0.66 to -0.55 from resampling whole snapshots.
The plan's criterion, an interval below zero, is met.

Both arms stay close to 0.50: the market's own price is hard to beat, and the
model mostly says so. The isolated judge leaves [0.45, 0.55] three to seven
times as often as the holistic one. On the 330 keys resolved by 2026-11-02
neither arm is distinguishable from a coin, as the plan expected with
eighteen snapshots whose keys move together.

## How the run went

All of this is checked from the repository and GitHub's record of pushes,
not taken from the run's own report.

- **Inputs fixed first.** The snapshots were rebuilt at v31 and their hashes
  pushed to main at 12:45:55 UTC (`ea2defd`). The first API call came at
  12:48:08.
  - 17 of the 18 snapshots are byte for byte the v31 rebuild made on the
    planning machine, from the series fetched there on 2026-11-01
    (`inputs-2026-11-01-v31-parts.sha256`). They match again when rebuilt
    from the series fetched there on 2026-11-02.
  - The 18th, 2026-10-30, differs from both rebuilds in its dashboard and
    targets.
    - The amendment guessed that 10-30 levels would have posted in
      between, but that is not why. FRED had not posted the Treasury yields
      of 10-30 even by 13:00 UTC on 11-02.
    - Every level the forecasts of that snapshot quote agrees with the
      rebuilds: the 2-year at 4.61%, WTI at $103.37 (-5.8% on the week, +21%
      over three months), the 10-year's 31 bp one-month rally, and
      breakevens at 2.34%. So the difference lies in a figure they do not
      quote. Which one is not known here.
    - None of that snapshot's keys had resolved by 2026-11-02, so the first
      scoring does not depend on it.
    - Its exact case exists only in the private copy kept where the
      forecasts were made. If that copy is lost, the snapshot cannot be
      scored (PLAN.md).
  - **Reproduced.** The first scoring was redone on the planning machine:
    the sealed forecasts on the cases rebuilt there, against the target
    series fetched there on 2026-11-02. It gives the same 330 resolved keys,
    the same 210 pending, and the same Brier tables to the last digit, in
    both arms.
- **Every forecast sealed, none changed.** Each arm sealed all 18 snapshots
  in 198 calls (eleven a snapshot), with no failure and no retry. Each of the
  36 `prediction.json` files was added once and never modified. They were
  pushed in seventeen commits as the run went.
- **The model asked for, and no other.** All 396 calls asked for
  claude-opus-5-5 and were served by it; the server-side fallback never
  served one. So no snapshot is excluded, and `primary.py` ran once.
- **Order and cap as planned.** The snapshots went in date order, three of
  an arm at a time, H first:
  `H 07-03..07-17, I 07-03..07-17, H 07-24..08-07, ...`. Neither arm came
  near its $15 cap.
- **No sign of look-ahead in the text.** The forecasts' theses and key
  points (2,076 texts) name later months 269 times. All of them are events
  scheduled in advance:
  - the FOMC meetings and minutes on the calendar;
  - payroll and CPI release dates;
  - the Treasury's quarterly refundings;
  - the November 3 midterms.
  
  The eight mentions near a past-tense verb all describe what the market was
  pricing for a later meeting, or are conditional. None describes something
  that happened after its snapshot's date. A text scan cannot rule out
  knowledge that shapes a probability without being written down; the
  knowledge cutoff (2026-06-30, before every snapshot) is what guards that.

## Do the views disagree? (primary, no outcome)

| | holistic | isolated |
|---|---|---|
| mean pairwise correlation, 540 keys | 0.654 | 0.045 |
| difference, isolated minus holistic (95% interval, snapshots resampled) | | -0.609 (-0.664, -0.547) |
| standard deviation of a perspective's probability | 0.027-0.037 | 0.020-0.032 |
| keys on which a perspective left 0.50 | 74%-85% | 69%-88% |
| judge outside [0.45, 0.55], 1 week | 2.2% | 16.1% |
| judge outside [0.45, 0.55], 1 month | 7.2% | 33.9% |
| judge outside [0.45, 0.55], 3 months | 17.2% | 47.8% |

- The probabilities are small departures from 0.50. Each perspective ranges
  over 0.40 to 0.63. The holistic judge ranges over 0.43 to 0.59 and the
  isolated judge over 0.36 to 0.67.
- No perspective is uninformative: each leaves 0.50 on at least 69% of the
  keys.
- The longer the horizon, the more often the judge claims an edge, in both
  arms.

## Do the scores change? (secondary, exploratory)

Scored on 2026-11-02 against the target series fetched that day: 330 of the
540 keys of each arm had resolved (162 at one week, 129 at one month, 39 at
three months), and 210 were pending.
- The three-month keys come from the first four snapshots only.
- A snapshot's keys move together, so the effective sample is far smaller
  than 330.

| Brier score | n | judge H | judge I | pool I | uniform | coin | trend | mean reversion |
|---|---|---|---|---|---|---|---|---|
| 1 week | 162 | 0.250 | 0.247 | 0.245 | 0.249 | 0.250 | 0.256 | 0.264 |
| 1 month | 129 | 0.247 | 0.239 | 0.233 | 0.246-0.247 | 0.250 | 0.275 | 0.244 |
| 3 months | 39 | 0.256 | 0.264 | 0.279 | 0.252-0.256 | 0.250 | 0.237 | 0.283 |
| all | 330 | 0.249 | 0.246 | 0.244 | 0.249 | 0.250 | 0.261 | 0.258 |

"pool I" is the isolated arm's combined forecast (log-odds pooled). The
holistic arm's combined forecast is its weighted mean, which with the initial
playbook's equal weights is the uniform one.

- `compare --metric brier_judge`, isolated against holistic: -0.0037, with an
  interval of -0.0076 to +0.0003. That interval resamples keys, not
  snapshots, so it is narrower than it should be. Even so it crosses zero.
  From the 2026-12-07 scoring on, the interval that resamples whole
  snapshots (`--resample cases`, v32) is given beside it.
- The combination rules replayed (`blend --perspectives macro`):

  | | uniform | median | hedge | leader | pool |
  |---|---|---|---|---|---|
  | holistic | 0.249 | 0.249 | 0.249 | 0.250 | 0.278 |
  | isolated | 0.249 | 0.248 | 0.249 | 0.249 | 0.244 |

  As in the perspectives run, pooling separate factors helps a little and
  pooling one view ten times hurts: ten correlated holistic views pooled as
  if independent are overconfident.
- By target, no judge is clearly better than the simple baselines. Trend did
  best at three months (0.237) and worst at one month (0.275). Mean reversion
  mirrors it. These are four and thirteen snapshots of one summer.

## What this means

The method carries over from earnings to macro: isolated views can be built
to disagree, and when they do, how they are combined matters. Whether any of
it forecasts better than the market's price cannot be told from these
eighteen snapshots. The rest of the plan, and what would answer that:

- **The two scorings fixed in the plan:** 2026-12-07, when every one-month
  key has resolved, and 2027-02-08, for every three-month key.
- **Forward snapshots, one each Friday.** Unlike these eighteen, their
  outcomes are unknown to everyone, so no knowledge cutoff is in question.
  That is a new plan.
- **A walk-forward `train` (v30) in isolated mode**, once whole blocks of
  snapshots have resolved, to ask whether learned weights help. That is also
  a new plan.

## Cost

API spend, metered from each call's usage:
- holistic: $13.81 ($0.73-0.82 a snapshot);
- isolated: $14.13 ($0.76-0.84 a snapshot);
- $27.94 in all, inside the plan's estimate of $0.45-1.00 a snapshot and
  under each arm's $15 cap.

A call read a median of about 16,600 cached prefix tokens and wrote a median
of about 1,400 output tokens. The run took 1 hour 35 minutes.
