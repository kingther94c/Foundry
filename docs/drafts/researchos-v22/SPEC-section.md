## Series from free sources (v22)

A macro snapshot reads market and economic series, and every one it needs
is free to download without an account:

- FRED publishes each series as CSV (`observation_date,DGS10` and then one
  `date,value` line per day, the value left empty on a day without one).
- ALFRED, its archive, publishes the same series as it was known on a past
  date, the column then named `<ID>_<YYYYMMDD>`. An economic release such as
  payrolls or CPI is revised after it comes out, so a snapshot of a past date
  must read the series as it stood on that date; a market series is not
  revised, so its current file is enough.
- The CFTC publishes the weekly Commitments of Traders as JSON. Its numbers
  are strings, and the positions of non-commercial traders, long minus
  short, are the usual measure of speculative positioning.

The new module `researchos.series` fetches these series, checks them and
stores them as the JSON files `sources.load_closes` already reads, so the
stored data can be inspected by eye. Nothing in it calls a model, and the
tests never touch the network: they inject a transport or replace
`urllib.request.urlopen`.

In this section a *date argument* is a `datetime.date` or a string that
`tools.dates.parse_iso_date` reads; anything else raises ValueError. A
*series id* is a `str` that matches `[A-Za-z0-9_]+` as a whole, and a
*contract code* a `str` that matches `[0-9A-Z]{6}` as a whole (`"043602"`,
`"13874A"`, `"1170E1"`). Anything else raises ValueError. URLs carry dates
in the form `YYYY-MM-DD`.

### Addresses

```python
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ALFRED_CSV = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
CFTC_LEGACY = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
```

- `fred_url(series_id, *, start=None, vintage=None) -> str`: without a
  vintage, `f"{FRED_CSV}?id={series_id}"`. With one,
  `f"{ALFRED_CSV}?id={series_id}&vintage_date={vintage}"`. Either way,
  `&cosd={start}` is appended when `start` is given. `start` and `vintage`
  are date arguments or None.
- `cftc_url(code, *, limit=600) -> str`:
  `f"{CFTC_LEGACY}?cftc_contract_market_code={code}&$order=report_date_as_yyyy_mm_dd%20DESC&$limit={limit}"`,
  the newest reports first. `limit` must be an `int` that is not a `bool`,
  from 1 to 50000; anything else raises ValueError.

### Reading what the sources return

- `parse_fred_csv(text, series_id) -> list[tuple[datetime.date, float]]`:
  - The lines are `text.splitlines()`; a line that is empty after
    stripping its whitespace is skipped, wherever it is.
  - The first other line is the header. It must have exactly two fields
    separated by a comma, each stripped of whitespace. The first is
    `observation_date` or `DATE`. The second is the series id itself, or
    the id followed by `_` and eight ASCII digits (an ALFRED vintage
    column). Anything else raises ValueError naming the series id: an
    error page, or the CSV of another series. So does a text with no
    header at all.
  - Every later line is an observation and must have exactly two fields,
    each stripped. The first is a date: a string `parse_iso_date` reads.
    The second is empty or `.` when the source has no value for that date
    (the observation is left out), and otherwise a decimal: it matches
    `-?[0-9]+(\.[0-9]+)?` as a whole and `float()` of it is finite. Any
    other line raises ValueError naming the series id.
  - The result is sorted by date. When two lines have the same date, the
    later line wins, even when it has no value.
- `parse_cftc(payload, code) -> tuple[list[tuple[datetime.date, float]], list[tuple[datetime.date, float]]]`:
  the payload is the decoded JSON. The result is two series, the net
  non-commercial position and the open interest, both sorted by date.
  - The payload must be a list. Anything else raises ValueError; when it is
    a dict with a `"message"` (how the CFTC's site reports an error), that
    message is included.
  - Every element must be a dict. Its `cftc_contract_market_code`,
    stripped, must equal `code`, so a report of another contract is never
    stored under this one.
  - Its `report_date_as_yyyy_mm_dd` is a string whose part before the first
    `T` (the whole string when there is none) `parse_iso_date` reads:
    `"2026-10-27T00:00:00.000"` is 2026-10-27.
  - Its `noncomm_positions_long_all`, `noncomm_positions_short_all` and
    `open_interest_all` are each an `int` that is not a `bool`, or a string
    that, stripped, matches `-?[0-9]+`.
  - The net position is long minus short. Both series hold floats.
  - Anything else in a row raises ValueError naming the code: a missing
    field (never KeyError), a row that is not a dict, a bad date, a bad
    count, a row of another contract.
  - When two rows have the same date, the later row wins in both series.
  - An empty list gives two empty series.

### Files

A series file is the JSON list `[["YYYY-MM-DD", value], ...]` that
`sources.load_closes` reads, sorted by date. Under a series directory, the
current file of a series is `<dir>/<series_id>.json`. The file of the series
as it was known on a date is `<dir>/<series_id>@<YYYY-MM-DD>.json`.

- `save_series(path, observations) -> None`: writes `observations`, pairs of
  a `datetime.date` that is not a `datetime.datetime` and a number
  (`tools.stats.is_number`), as a series file through `write_json`.
  - The parent directories are created.
  - The pairs are sorted by date, and of two pairs with the same date the
    later one is kept.
  - Any other pair raises ValueError before anything is written, so an
    existing file is left as it was.
- `load_series(series_dir, series_id, as_of=None) -> list[tuple[datetime.date, float]]`:
  the series through `sources.load_closes` (its ValueError and OSError,
  for a missing file, pass through).
  - Without `as_of`, the current file is read.
  - With `as_of` (a date argument), the file for that date is read when it
    exists, and the current file otherwise. Either way, only the
    observations dated on or before `as_of` are returned, so a current file
    never gives a snapshot a value from after its date.

### Fetching

- `fetch_text(url, *, user_agent, transport=None) -> str`: one GET through
  `transport(url, headers) -> (body, content_type)` with the headers
  `{"User-Agent": user_agent}`. Without a transport it goes through urllib
  as `sources.get_page` does: 30 seconds' timeout, and HTTP errors are
  OSErrors.
  - The body is decoded by the charset its Content-Type names, as
    `get_page` decodes, and as UTF-8 when it names none or one Python does
    not know. A byte order mark is dropped.
  - Unlike `get_page`, it accepts any content type (FRED serves
    `application/csv`).
  - The transport's errors propagate.
- `fetch_fred(series_id, *, user_agent, start=None, vintage=None, transport=None)`:
  `parse_fred_csv(fetch_text(fred_url(series_id, start=start, vintage=vintage), ...), series_id)`.
  The arguments are checked before anything is fetched.
- `fetch_cftc(code, *, user_agent, limit=600, transport=None)`: the text
  `fetch_text` returns for `cftc_url(code, limit=limit)`, decoded as JSON
  (text that is not JSON raises ValueError), then `parse_cftc(payload,
  code)`. The arguments are checked before anything is fetched.

### CLI

```text
python -m researchos fetch-series --source {fred,cftc} --id ID --file FILE
    [--start YYYY-MM-DD] [--vintage YYYY-MM-DD] [--limit N] [--oi-file FILE]
    [--user-agent TEXT] [--out FILE]
```

- `--source fred`: `fetch_fred` with `--id` as the series id, and
  `--start` and `--vintage` when they are given; the series is saved to
  `--file`.
- `--source cftc`: `fetch_cftc` with `--id` as the contract code, and
  `--limit` when it is given. The net position is saved to `--file`, and
  the open interest to `--oi-file` when that is given.
- The user agent is chosen as `build` chooses it: `--user-agent`, else
  `RESEARCHOS_USER_AGENT`, else the default.
- Exit 2, as argparse exits, on bad arguments:
  - `--start` or `--vintage` not an ISO date, or given with `--source cftc`;
  - `--limit` not an integer from 1 to 50000, or given with `--source fred`;
  - `--oi-file` given with `--source fred`;
  - a missing `--source`, `--id` or `--file`.
- Exit 1, with a message on stderr, when fetching or saving raises OSError
  or ValueError (a bad id or code included). When fetching or reading fails,
  nothing is written: every series is parsed before the first file is saved.
- On success, exit 0 and emit, as every command does (printed, or written
  to `--out`), `{"source": ..., "id": ..., "observations": <n>, "first":
  <first date or null>, "last": <last date or null>, "file": <FILE>}`.
  For `cftc` there is also `"oi_file"`: the `--oi-file` path, or null. A
  source with no observations is still a success.
