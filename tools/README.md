# tools

Reusable, pure utility functions -- financial arithmetic, returns, statistics
and forecast scoring, text masking, fiscal dates. Standard library only; every
uncomputable result is `None`, never an exception. [SPEC.md](SPEC.md) is the
contract.

| Module | What it covers |
|---|---|
| `tools.finance` | growth, CAGR, margins, basis-point changes, free cash flow, TTM, earnings surprise and beat/miss |
| `tools.returns` | simple/log/compound returns, abnormal and event-window returns, drawdown, volatility |
| `tools.stats` | mean, stdev, quantiles, winsorizing, Brier score, log loss, calibration, multiplicative-weights updates, probability pooling |
| `tools.textmask` | whole-word redaction, money and amount rescaling in any layout, year shifting and dropping years long past -- for disguising a document's identity |
| `tools.dates` | fiscal quarters for any year end, business-day arithmetic |

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
```

## How it was built

Foundry wrote the package from SPEC.md and FOUNDRY.md alone, in a fresh
workspace with a fresh `FOUNDRY_HOME`: no repository history, no earlier
attempts, no tests. `tests/test_acceptance.py` was written separately from the
spec and kept outside the workspace; it decided acceptance. A reference
implementation was written by hand, also from the spec, and the two were
compared by differential fuzzing over 144,000 generated calls.

| | Foundry attempt 1 | attempt 2 | attempt 3 | Hand-written reference |
|---|---|---|---|---|
| Acceptance tests (74) | all pass | 73 | 73 | 73 at first, 74 after the fix |
| Agent rounds / cost | 9 / ~$0.71 | 9 / ~$0.65 | 10 / ~$0.79 | -- |

Attempt 1 is what is checked in, with its own unit tests (`test_dates.py` and
the rest). Across 144,000 fuzzed calls it disagreed with the others only in
`cagr` on a float overflow (`cagr(1e-9, 2.5, 1e-9)`), where SPEC.md's "never
an exception" rule calls for `None`: attempt 1 catches the `OverflowError`;
attempts 2 and 3, and the first version of the reference, let it escape. The
acceptance suite now pins the case. Attempt 3 ran with an extra line in
Foundry's system prompt asking the agent to test a spec's general rules; it
tested bad and missing input more, never overflow, so the line was dropped.

v2 added `dates.parse_iso_date` and `stats.is_number`, the two checks
ResearchOS had written privately in five places. Foundry made them in 10
rounds, with the API out of credit and a subagent standing in for the model
behind a local relay; they pass all 37 new acceptance cases (`test_v2_acceptance.py`), including
`is_number(10 ** 400)`, where `math.isfinite` would overflow -- Foundry
handled it, with a comment saying why.

v3 added `textmask.scale_table_numbers` and `textmask.replace_years_before`.
On the first small-cap ResearchOS cases, masked releases still carried their
real table figures (a `$` in a cell of its own, most rows with no `$`, so
`scale_money` never saw them), and guessers undid the year shift from years
everyone knows (a founding year, the 1995 securities reform act). Foundry
made both in 6 rounds through the relay; they pass all 24 new acceptance
cases (`test_v3_acceptance.py`) and agree with a hand-written reference on
20,000 fuzzed inputs.

v4 added `textmask.scale_numbers`: with v3 in place, 404 of the sixteen
cases' 6,194 comma-grouped real figures still came through, all in layouts
v3 does not read as tables (a cell per line, a negative split as `(22` and
`)` across cells, slide text). `scale_numbers` scales every amount other
than money whatever the layout, leaving 29. Foundry made it in 7 rounds;
it passes all 42 new acceptance cases (`test_v4_acceptance.py`) and agrees
with a hand-written reference on 30,000 fuzzed inputs.
