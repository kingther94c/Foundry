## Snapshot tables (v23)

A macro snapshot shows the analyst where the markets and the economy stood
on its date, as plain-text tables built from the stored series:
- where each target was and how it had moved;
- the economic data as published by then;
- how speculators were positioned;
- what was scheduled next.

The tables are deterministic: the same series give the same text byte for
byte, with no clock and nothing random. They can only see the past: every
function here ignores observations dated after the snapshot's date.
Assembling a snapshot from them comes in the next version.

The new module `researchos.snapshot` holds them. Its *observations* are
pairs `(datetime.date, number)` in any order, as `series.load_series`
returns them. An `as_of` is a date argument as v22 defines it (a
`datetime.date`, or a string `parse_iso_date` reads); anything else raises
ValueError. Every text a function returns is its lines joined by `"\n"`,
with a final `"\n"`.

```python
LOOKBACKS = ("1w", "1m", "3m", "1y")
```

### Changes and how they are written

- `trailing_change(observations, as_of, horizon, kind) -> float | None`:
  how far the level moved over `horizon` up to `as_of`, as
  `tools.finance.level_change` measures it for `kind`.
  - The end is the last observation on or before `as_of`
    (`tools.dates.last_on_or_before`).
  - The start is the last observation on or before
    `tools.dates.shift_date(as_of, -n, unit)`, where `(n, unit) =
    tools.dates.parse_horizon(horizon)`.
  - Returns None when either is missing, when they are the same
    observation (the same date: nothing was observed in between), or when
    `level_change` returns None.
  - `horizon` and `kind` are checked first, whatever the observations: a
    horizon `parse_horizon` rejects, or a kind not in `cases.KINDS`, raises
    ValueError.
- `format_level(value) -> str`: `f"{value:.2f}"`, or `"n/a"` when `value`
  is not a number (`tools.stats.is_number`).
- `format_change(change, kind) -> str`: `"n/a"` when `change` is not a
  number. Otherwise:
  - `"bp"`: `f"{change:+.0f} bp"`;
  - `"return"`: `f"{100 * change:+.1f}%"`;
  - `"points"`: `f"{change:+.2f}"`.

  So `12.0` in bp is `"+12 bp"`, `-0.0123` as a return is `"-1.2%"`, and
  `0.85` in points is `"+0.85"`. Another kind raises ValueError, whatever
  `change` is.

### The tables

Each table function returns an introduction (one line), an empty line, and
then a Markdown table: a header line, the line `|---|...|` with one `---`
per column, and one line per entry, in the order given. A line is the
cells, each with one space either side, between `|` characters:
`| a | b |`. Wherever a value is missing, the cell is `n/a`.

- `target_table(as_of, targets, series) -> str`: `targets` is a list of
  dicts with the keys `id`, `name`, `unit` and `kind`. `series` maps an id
  to its observations; an id it does not have has none.
  - The introduction: `Each level is the last observation on or before
    <as_of>; each change runs to it from the last observation on or before
    the date that far back.`
  - The header: `| Target | Level | On | 1w | 1m | 3m | 1y | 1y range | 1y
    percentile |`.
  - A row: `<name> (<id>, <unit>)`, then:
    - the level, `format_level` of the last observation on or before
      `as_of`, and its date (`On`);
    - `format_change(trailing_change(obs, as_of, L, kind), kind)` for each
      `L` in `LOOKBACKS`;
    - the range, `<low> to <high>`, from `format_level` of the smallest and
      largest values among the observations dated after
      `shift_date(as_of, -1, "y")` and on or before `as_of`;
    - the percentile, `str(round(100 * tools.stats.percentile_rank(level,
      values)))` over those same values.
  - With no level, every cell after the name is `n/a`; with no value in
    the year, so are the range and the percentile.
- `context_table(as_of, entries, series, published) -> str`: `entries` is a
  list of dicts with the keys `id`, `name` and `unit`. `series` is as
  above. `published` is a collection of the ids whose observations are the
  series as it was published by `as_of` (its vintage file).
  - The introduction: `Economic data, the latest observation on or before
    <as_of>.`
  - The header: `| Series | Latest | For | Previous | Change | A year
    earlier | Vintage |`.
  - A row: `<name> (<id>, <unit>)`, then:
    - the latest value, `format_level` of the last observation on or
      before `as_of`, and its date (`For`);
    - the previous value, the last observation before that date;
    - the change from the previous value to the latest,
      `format_change(latest - previous, "points")`;
    - the value a year earlier, the last observation on or before
      `shift_date(<the latest date>, -1, "y")`;
    - `as published by <as_of>` when the id is in `published`, and
      `current values` otherwise.
  - With no latest value, every cell after the name but the last is `n/a`.
- `positioning_table(as_of, entries, series) -> str`: `entries` is a list of
  dicts with the keys `id`, `name`, `net` (the id of the net-position
  series), `open_interest` (the id of the open-interest series, or None)
  and `release_lag_days`.
  - A report dated `D` became public `release_lag_days` days later, so only
    the reports of the `net` series dated on or before `as_of` minus that
    many days are used. `release_lag_days` must be an `int` that is not a
    `bool`, at least 0; anything else raises ValueError naming the entry's
    id.
  - The introduction: `Speculative positioning: non-commercial net
    contracts from the CFTC's Commitments of Traders, each report as it
    was public on <as_of>.`
  - The header: `| Contract | Net | Report of | Weekly change | 52-week
    percentile | Share of open interest |`.
  - A row: `<name> (<id>)`, then:
    - the net position of the latest report used, `f"{net:,.0f}"`, and its
      date;
    - the change from the report before it, `f"{change:+,.0f}"`;
    - `str(round(100 * percentile_rank(net, values)))` over the net
      positions of the reports used that are dated after
      `shift_date(<the latest date>, -52, "w")` and on or before that
      date;
    - the share of open interest, `f"{100 * net / oi:+.1f}%"`, where `oi`
      is the open interest observed on exactly the latest report's date.
      It is `n/a` when there is no such observation, when `oi` is 0, or
      when `open_interest` is None.
  - With no report, every cell after the name is `n/a`.
- `calendar_list(as_of, events, through) -> str`: `events` is a list of
  dicts with the keys `date` (an ISO date string) and `event`. `through`
  is a date argument.
  - The first line is `Scheduled after <as_of>, through <through>:`; then
    an empty line.
  - Then one line `- <date>: <event>` per event dated after `as_of` and on
    or before `through`, by date. Events on the same date keep their
    order. The date is written `YYYY-MM-DD`.
  - With no such event, the one line `- none listed`.
  - An event whose `date` is not a string `parse_iso_date` reads raises
    ValueError naming the event.

Every date written into a table is in the form `YYYY-MM-DD`, `<as_of>`
included.
