## v6: horizons and level changes

A macro view forecasts where a market level will be some time after a date:
the 10-year yield one month after a Friday's close, the dollar index a week
after it. Scoring one needs the date its horizon ends, the observations a
series has around that date (a horizon can end on a weekend or a holiday),
and the change in the level, measured the way that market is quoted. In
`tools.dates`:

- `add_months(d, months) -> datetime.date`: the same day of the month,
  `months` calendar months later (earlier when negative), clipped to the
  last day of the target month when that month is shorter. `months` must be
  an `int` that is not a `bool`; anything else raises ValueError, and so does
  a result outside the dates Python can represent (`datetime.date.min` to
  `datetime.date.max`). Examples: 2026-01-31 plus 1 -> 2026-02-28;
  2028-01-31 plus 1 -> 2028-02-29; 2026-11-30 plus 3 -> 2027-02-28;
  2026-03-31 minus 1 -> 2026-02-28; 2026-01-15 minus 13 -> 2024-12-15;
  2026-05-15 plus 0 -> 2026-05-15.
- `parse_horizon(text) -> tuple[int, str]`: a horizon is a count and a unit,
  `"1w"` -> `(1, "w")`, `"3m"` -> `(3, "m")`. The text, without the
  whitespace around it, must be one or more ASCII digits that do not begin
  with `0`, followed directly by exactly one of `d` (days), `w` (weeks), `m`
  (months) or `y` (years), in lower case. Anything else raises ValueError,
  including a value that is not a `str`. Examples: `" 12m "` -> `(12, "m")`;
  `"0d"`, `"01w"`, `"1W"`, `"1 w"`, `"-1m"`, `"+1m"`, `"1.5m"`, `"m"`, `"1"`,
  `"1mo"` and `""` raise ValueError.
- `shift_date(d, n, unit) -> datetime.date`: `d` moved by `n` units. `"d"`
  adds `n` days, `"w"` adds `7 * n` days, `"m"` is `add_months(d, n)` and
  `"y"` is `add_months(d, 12 * n)`, so 2028-02-29 plus one year is
  2029-02-28. `n` may be zero or negative and must be an `int` that is not a
  `bool`. Any other `n` or `unit` raises ValueError, and so does a result
  outside the dates Python can represent.
- `horizon_end(d, horizon) -> datetime.date`:
  `shift_date(d, *parse_horizon(horizon))`. Examples from Friday
  2026-07-03: `"1w"` -> 2026-07-10, `"10d"` -> 2026-07-13, `"1m"` ->
  2026-08-03 (a Monday), `"3m"` -> 2026-10-03 (a Saturday), `"1y"` ->
  2027-07-03.
- `last_on_or_before(observations, day)` and
  `first_on_or_after(observations, day)`, each
  `-> tuple[datetime.date, object] | None`: `observations` is a sequence of
  `(datetime.date, value)` pairs in any order, as `event_return` takes them;
  the value may be anything and is returned as it is.
  `last_on_or_before` gives the pair with the latest date on or before
  `day`, and `first_on_or_after` the pair with the earliest date on or after
  it. When several pairs have that date, the one that comes last in
  `observations` is returned (a later line of a file corrects an earlier
  one). The result is a new tuple `(date, value)`, whatever kind of pair the
  sequence held, and the sequence itself is left as it was. `None` if no
  pair qualifies, which includes an empty sequence.

In `tools.finance`:

- `level_change(start, end, kind) -> float | None`: the change from `start`
  to `end`, the way the level is quoted.
  - `"bp"`: `(end - start) * 100`, in basis points of a level quoted in
    percent (a yield, a spread, a breakeven inflation rate): 4.21 to 4.33 is
    12 bp, up to float rounding. A level may be zero or negative, as a curve
    spread can be. (`change_bps` takes fractions, 0.0421, where a basis
    point is 0.0001; here a basis point is 0.01.)
  - `"return"`: `tools.returns.simple_return(start, end)`, for a price or an
    index; so `None` when `start <= 0`.
  - `"points"`: `end - start`, for a level traded in its own units (VIX).
  - `None` if `start` or `end` is not a number (`tools.stats.is_number`),
    and `None` when the result overflows: an infinity, or a quotient too
    large for a float.
  - Any other `kind` raises ValueError, whatever `start` and `end` are.
- `direction(change, dead_band=0.0) -> str | None`: `"up"` if
  `change > dead_band`, `"down"` if `change < -dead_band`, and otherwise
  `"flat"`: a change exactly at the band's edge is flat, and with the default
  band only a zero change is. `None` if `change` is not a number
  (`is_number`). `dead_band` must be a number (`is_number`) that is at least
  0; anything else raises ValueError, whatever `change` is. A binary outcome
  is `direction(change) == "up"`; with a band, the same recorded changes can
  later be scored three ways.
