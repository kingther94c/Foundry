# tools -- specification

A package `tools` with five modules: `finance`, `returns`, `stats`,
`textmask`, `dates`. Python 3.12, standard library only, every function pure.

General rules, unless a function says otherwise:

- Numeric inputs are `int | float | None`. A result that cannot be computed is
  `None` -- never an exception, never 0.
- Results are not rounded.
- "Raises ValueError" means exactly that, with a message saying what was wrong.

## tools.finance

| Function | Returns |
|---|---|
| `safe_div(a, b)` | `a / b`; `None` if either is `None` or `b == 0`. |
| `growth(current, base)` | `(current - base) / base`; `None` if either is `None` or `base <= 0` (growth on a zero or negative base is not meaningful). |
| `cagr(start, end, years)` | `(end / start) ** (1 / years) - 1`; `None` if any is `None`, `start <= 0`, `end < 0`, or `years <= 0`. `end == 0` gives `-1.0`. |
| `margin(numerator, revenue)` | `safe_div(numerator, revenue)`. |
| `change_bps(current, prior)` | `(current - prior) * 10000`; `None` if either is `None`. |
| `free_cash_flow(operating_cash_flow, capex)` | `operating_cash_flow - abs(capex)` (capex may be given with either sign); `None` if either is `None`. |
| `ttm(values)` | The sum of the last four elements of the sequence; `None` if it has fewer than four elements or any of the last four is `None`. |
| `yoy_growth_series(values, lag=4)` | A list the same length as `values`; element `i` is `growth(values[i], values[i - lag])` when `i >= lag`, else `None`. `lag < 1` raises ValueError. |
| `surprise(actual, estimate)` | `(actual - estimate) / abs(estimate)`; `None` if either is `None` or `estimate == 0`. |
| `beat_miss(actual, estimate, tolerance=0.01)` | With `s = surprise(actual, estimate)`: `None` if `s` is `None`; `"beat"` if `s > tolerance`; `"miss"` if `s < -tolerance`; otherwise `"in_line"` (a surprise exactly equal to the tolerance is in line). |

## tools.returns

| Function | Returns |
|---|---|
| `simple_return(p0, p1)` | `p1 / p0 - 1`; `None` if either is `None` or `p0 <= 0`. |
| `log_return(p0, p1)` | `math.log(p1 / p0)`; `None` if either is `None` or either is `<= 0`. |
| `compound(returns)` | `prod(1 + r) - 1` over the sequence; `0.0` for an empty sequence; `None` if any element is `None`. |
| `abnormal_return(stock_return, market_return, beta=1.0)` | `stock_return - beta * market_return`; `None` if any is `None`. |
| `event_return(closes, event_date, after=1)` | The return around an event. `closes` is a sequence of `(datetime.date, close)` pairs in any order. `event_date` is the first session that can react to the news (for results released after the close on day D, that is D+1). The base is the close on the latest date strictly before `event_date`; the end is the `after`-th close on or after `event_date` (`after=1` is the first such close). Returns `simple_return(base, end)`; `None` if there is no base, fewer than `after` closes on or after the event, or `after < 1`. |
| `max_drawdown(prices)` | The largest peak-to-trough decline as a negative fraction (a fall from 100 to 75 is `-0.25`); `0.0` if prices never fall below a prior peak or there are fewer than two. A price `<= 0` raises ValueError. |
| `annualized_volatility(returns, periods_per_year=252)` | Sample standard deviation (n - 1) of the returns times `sqrt(periods_per_year)`; `None` if there are fewer than two returns or any is `None`. |

## tools.stats

`values` are sequences of numbers. `outcomes` are 0/1 (bools allowed).

| Function | Returns |
|---|---|
| `mean(values)` | The arithmetic mean; `None` if empty or any element is `None`. |
| `stdev(values)` | Sample standard deviation (n - 1); `None` if fewer than two elements or any is `None`. |
| `zscore(x, values)` | `(x - mean) / stdev`; `None` if `x` is `None`, or the stdev is `None` or 0. |
| `quantile(values, q)` | Linear interpolation on the sorted values: `pos = q * (n - 1)`, result `s[floor(pos)] + (s[ceil(pos)] - s[floor(pos)]) * (pos - floor(pos))`. `None` if empty; `q` outside `[0, 1]` raises ValueError. |
| `percentile_rank(x, values)` | Mid-rank: (number of values `< x` + half the number `== x`) / n; `None` if empty. |
| `winsorize(values, lower=0.05, upper=0.95)` | A new list, each value clipped to `[quantile(values, lower), quantile(values, upper)]`, order preserved; `[]` for an empty input. Raises ValueError unless `0 <= lower <= upper <= 1`. |
| `brier_score(probs, outcomes)` | Mean of `(p - o) ** 2`. Raises ValueError if the lengths differ, the input is empty, or a probability is outside `[0, 1]`. |
| `log_loss(probs, outcomes, eps=1e-15)` | Mean of `-(o * ln(p) + (1 - o) * ln(1 - p))` with each `p` clipped to `[eps, 1 - eps]`. Same ValueErrors as `brier_score`. |
| `directional_accuracy(predicted, actual)` | Over the pairs where both values are non-zero, the fraction whose signs agree; pairs with a zero are left out. `None` if no pair is left. Raises ValueError if the lengths differ. |
| `calibration_table(probs, outcomes, bins=10)` | A list of dicts, one per non-empty bin, ordered by `lower`, each `{"lower", "upper", "count", "mean_prob", "frequency"}` where frequency is the mean outcome. Bin `i` covers `[i/bins, (i+1)/bins)`; the last bin also includes 1.0. Same ValueErrors as `brier_score`; `bins < 1` raises ValueError. |
| `hedge_update(weights, losses, eta=0.5)` | Multiplicative weights: each `w * exp(-eta * loss)`, then normalized to sum to 1, as a new dict with the same keys as `weights`. A key missing from `losses` has loss 0; keys only in `losses` are ignored. If every updated weight is 0, the result is uniform. `{}` for empty weights. A negative weight raises ValueError. |
| `combine_probabilities(probs, weights)` | The weighted average of `probs[k]` over keys present in both mappings with `weights[k] > 0`; `None` if that total weight is 0. |

## tools.textmask

Masking helpers for hiding identifying details in documents.

**Whole-word rule** used below: a match must not be immediately preceded or
followed by an ASCII letter or digit. Punctuation and apostrophes are not
letters, so "Acme's" contains a whole-word "Acme".

- `redact(text, mapping, *, case_sensitive=False) -> str`: replace every
  whole-word occurrence of each key of `mapping` with its value. Keys are
  literal strings (regex characters in them have no special meaning). Where
  keys overlap, the longer key wins ("Acme Cloud" before "Acme"). Replacement
  text is never scanned again: a value that contains another key stays as
  written. Empty keys are ignored. Matching ignores case unless
  `case_sensitive`.
- `find_terms(text, terms, *, case_sensitive=False) -> list[str]`: the terms
  that occur in the text under the whole-word rule, in the order given,
  without duplicates.
- `scale_money(text, factor) -> str`: multiply every money amount by
  `factor`. A money amount is a `$` or `US$` prefix, a number (digits, with
  optional comma thousands separators and an optional decimal part), and an
  optional scale: a space and then `thousand`, `million`, `billion` or
  `trillion` (any case), or directly attached `K`, `M`, `B`, `T`, `bn` or `mn`.
  Only the number changes: the prefix, the scale and everything around the
  amount stay as written. The new number keeps the original's decimal places
  and uses comma separators exactly when the original did, formatted as
  Python's `format(value, ",.{d}f")` / `format(value, ".{d}f")` would.
  `factor <= 0` raises ValueError.
  Examples with factor 0.5: `"$90.0 billion"` -> `"$45.0 billion"`;
  `"$90,007 million"` -> `"$45,004 million"`; `"US$4.81"` -> `"US$2.40"`;
  `"$1.2bn"` -> `"$0.6bn"`; `"18%"` is unchanged.
- `shift_years(text, offset) -> str`: shift years by `offset`.
  - A year is four digits from 1900 to 2099 whose preceding character is not
    a digit, `,`, `.` or `$`, and which is not followed by a digit, nor by a
    `,` or `.` that is itself followed by a digit. Letters may precede it, so
    "FY2026" contains a year. It becomes the year plus `offset`.
  - `FY` followed by exactly two digits (not followed by another digit) is a
    two-digit fiscal year: `FYdd` becomes `FY` plus `(2000 + dd + offset) %
    100`, zero-padded to two digits.
  - Nothing else changes. Examples with offset -7: `"fiscal 2026"` ->
    `"fiscal 2019"`; `"FY26"` -> `"FY19"`; `"FY2026"` -> `"FY2019"`;
    `"in 2026, sales"` -> `"in 2019, sales"`; `"$2,026"`, `"12026"` and
    `"2026.5"` are unchanged.

## tools.dates

Fiscal years are named by the calendar year in which they end. With the
fiscal year ending in month `m`, quarter 1 begins in month `m + 1` (January
when `m` is 12).

- `fiscal_quarter(d, fiscal_year_end_month=12) -> tuple[int, int]`:
  `(fiscal_year, quarter)` for date `d`. Examples: year ending June,
  2026-07-15 -> (2027, 1) and 2026-06-30 -> (2026, 4); year ending
  September, 2026-06-27 -> (2026, 3); calendar year, 2026-05-01 -> (2026, 2).
  A month outside 1..12 raises ValueError.
- `quarter_end(fiscal_year, quarter, fiscal_year_end_month=12) -> date`: the
  last calendar day of that quarter. Examples: (2026, 4, 6) -> 2026-06-30;
  (2027, 1, 6) -> 2026-09-30; (2026, 3, 9) -> 2026-06-30. A quarter outside
  1..4 or a month outside 1..12 raises ValueError.
- `add_business_days(d, n, holidays=())`: the date `n` business days after
  `d` (before it when `n` is negative). Business days are Monday to Friday and
  not in `holidays`. `n == 0` returns `d` unchanged, even on a weekend.
- `business_days_between(start, end, holidays=())`: the number of business
  days in `(start, end]` -- after `start`, up to and including `end`. If `end`
  is before `start`, the count over `(end, start]`, negated. 0 when equal.

## v2: values read from files

Values read from JSON files or written by hand arrive loosely typed: a date
may be a string with stray spaces, a "number" may be a bool, NaN or a string.
Two helpers decide what such a value is, so every caller asks the same
question the same way. Neither ever raises.

- `tools.dates.parse_iso_date(value) -> datetime.date | None`: a
  `datetime.datetime` gives its `.date()`; a `datetime.date` is returned as
  it is; a `str` gives `datetime.date.fromisoformat` of the string with
  surrounding whitespace removed, or None if that fails; anything else
  (None, numbers, bools, lists, ...) gives None.
- `tools.stats.is_number(value) -> bool`: True for an `int` (of any size --
  an int is always finite) and for a finite `float`; False for a bool, NaN,
  plus or minus infinity, None, a string, and anything else.

## v3: amounts in tables, years long past

Masking scales money and shifts years so that a document's figures cannot
be looked up. Two gaps let real figures through. Financial tables, as
`html_to_text`-style converters write them (cells joined by `" | "`), put
the `$` in a cell of its own (`"Book value per share | $ | 27.13"`) and
print most rows with no `$` at all, so `scale_money` leaves them as
reported. And a year long past -- a founding year, the year of a statute
everyone knows -- tells a reader the shift, which then undoes every other
year. In `tools.textmask`:

- `scale_table_numbers(text, factor) -> str`: multiply the numbers in table
  cells by `factor`.
  - The text is split on `"\n"` and rejoined with `"\n"`. A line that
    contains `" | "` is a table row; every other line stays exactly as it is.
  - The cells of a table row are the pieces between `"|"` characters (the
    first piece, before the first `"|"`, is a cell too). A cell is scaled
    when its text, without the whitespace around it, is a number: an
    optional `-`, then digits with comma thousands separators (one to three
    digits, then groups of a comma and three digits) or plain digits, then
    an optional `.` and digits; the whole may instead be wrapped in
    parentheses, `(...)`, with no `-` (a negative amount in accounts).
  - The new number is the old one times `factor`, keeping its decimal
    places, using comma separators exactly when the original did (formatted
    as `scale_money` formats), and keeping the `-` or the parentheses and
    the whitespace around it; the `|` characters stay where they were.
  - Not scaled: a cell whose next cell, without whitespace, begins with `%`
    (a percentage whose sign the table printed in a cell of its own); a
    year, four digits from 1900 to 2099 with no sign, comma, decimal part or
    parentheses; and every cell that is not just a number (`"$1.02"`,
    `"5.90%"`, `"Q2"`, `"1,234 units"`, an empty cell). Amounts with an
    attached `$` are `scale_money`'s.
  - A ratio printed without a percent sign is scaled like any other number:
    nothing in a cell says it is a ratio.
  - `factor <= 0` raises ValueError.
  - Examples with factor 0.5: `"Revenue | 1,234 | 1,000"` -> `"Revenue | 617
    | 500"`; `"Net loss | $ | (80.2) | | -4.6"` -> `"Net loss | $ | (40.1) |
    | -2.3"`; `"Margin | 23.6 | % | | 23.9 | %"`, `"| 2026 | | 2025 |"` and
    `"Sales of 1,234 units"` are unchanged.
- `replace_years_before(text, cutoff, replacement) -> str`: every year, as
  `shift_years` recognizes one (four digits from 1900 to 2099 with the same
  rules about the characters around it), that is less than `cutoff` becomes
  `replacement`. A year directly preceded by an ASCII letter (as in
  `"FY2019"`) and the two-digit `FYdd` form are left as they are; so is
  everything else. Examples with cutoff 2023 and replacement `"an earlier
  year"`: `"founded in 1902"` -> `"founded in an earlier year"`;
  `"Reform Act of 1995."` -> `"Reform Act of an earlier year."`; `"in 2025
  and 2022"` -> `"in 2025 and an earlier year"`; `"$1,995"`, `"2019.5"`,
  `"FY2019"` and `"FY19"` are unchanged.

## v4: every amount, whatever the layout

`scale_table_numbers` reads a table as rows of `" | "`-separated cells. Real
releases come in other layouts too: converters that put every cell on a
line of its own, accounts that print a negative as `(22` in one cell and
`)` in the next, slide decks flattened into long lines. In those, real
figures still came through. `scale_numbers` scales every number that is an
amount, whatever the layout, except the money amounts `scale_money`
handles; it is meant to run after `scale_money` and to take the place of
`scale_table_numbers`. In `tools.textmask`:

- `scale_numbers(text, factor) -> str`: the text is split on `"\n"` and
  rejoined with `"\n"`; each line is handled on its own, and every number is
  scaled at most once.
  - A table row (a line containing `" | "`) is handled as
    `scale_table_numbers` handles it, with one addition: a cell that,
    without whitespace, is `(` followed by a number (no `-`, no closing
    parenthesis), and whose next cell, without whitespace, begins with `)`,
    is a negative amount in parentheses and is scaled too. In the row's
    remaining cells (those not scaled as a number), comma-grouped numbers
    are scaled as in prose (below).
  - A line that, without the whitespace around it, is just a number, as a
    table cell would be (`"63,296"`, `"(1.5)"`, `"-4"`), is scaled as a
    table cell is, unless it is a year (four digits from 1900 to 2099 with
    no sign, comma, decimal part or parentheses).
  - Any other line is prose. In prose, a comma-grouped number -- one to
    three digits, then one or more groups of a comma and three digits, then
    optionally `.` and digits -- is scaled when the character before it is
    not a digit, an ASCII letter, `,`, `.` or `$`, and the number is not
    followed by a digit, an ASCII letter, or a `,` or `.` that is itself
    followed by a digit; unless it is a percentage: followed, after optional
    spaces, by `%`, `percent`, `per cent`, `bps` or `basis point` (any
    case). Other numbers in prose (`500`, `3.5`, years) stay as they are.
  - Scaled numbers keep their decimal places, use comma separators exactly
    when the original did, and keep their sign or parentheses and the
    whitespace around them (formatted as `scale_money` formats).
  - `factor <= 0` raises ValueError.
  - Examples with factor 0.5: `"over 1,300 tenants"` -> `"over 650
    tenants"`; `"a 1,000 percent rise"`, `"$1,300"` and `"500 tenants"` are
    unchanged; `"Net | 1,000 | | 30 | %"` -> `"Net | 500 | | 30 | %"`;
    `"Loss | (22 | )"` -> `"Loss | (11 | )"`; `"Q2 | Sales 2,000 and 3"` ->
    `"Q2 | Sales 1,000 and 3"`; the lines `"(1,000)"` and `"  64"` become
    `"(500)"` and `"  32"`; the line `"2026"` is unchanged.
