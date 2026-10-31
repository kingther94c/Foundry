"""Snapshot tables (v23): where the markets and the economy stood on a snapshot's date.

Plain-text tables built from stored series -- *observations*, pairs ``(date, number)``
in any order, as ``series.load_series`` returns them: the targets (``target_table``),
the economic data (``context_table``), speculative positioning (``positioning_table``)
and what was scheduled next (``calendar_list``). Every function ignores observations
dated after ``as_of``, and the same series give the same text byte for byte. Each text
is its lines joined by newlines, with a final newline.
"""

from __future__ import annotations

from datetime import date

from tools.dates import last_on_or_before, parse_horizon, parse_iso_date, shift_date
from tools.finance import level_change
from tools.stats import is_number, percentile_rank

from researchos.cases import KINDS
from researchos.series import _date_argument

LOOKBACKS = ("1w", "1m", "3m", "1y")

# What a cell holds when its value is missing.
NA = "n/a"
TARGET_HEADER = ("Target", "Level", "On", *LOOKBACKS, "1y range", "1y percentile")
CONTEXT_HEADER = ("Series", "Latest", "For", "Previous", "Change", "A year earlier", "Vintage")
POSITIONING_HEADER = ("Contract", "Net", "Report of", "Weekly change", "52-week percentile",
                      "Share of open interest")


# ---- helpers ------------------------------------------------------------------------

def _check_kind(kind) -> str:
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    return kind


def _shifted(day: date, n: int, unit: str) -> date | None:
    """shift_date(day, n, unit); None when that is outside the dates Python can represent.

    Only earlier dates are asked for, so None is a date before every observation.
    """
    try:
        return shift_date(day, n, unit)
    except (ValueError, OverflowError):
        return None


def _on_or_before(observations, day: date | None):
    """last_on_or_before; None when ``day`` is None (before every date)."""
    return None if day is None else last_on_or_before(observations, day)


def _last_before(observations, day: date):
    """The last observation dated strictly before ``day`` (the later pair on a tie)."""
    return last_on_or_before([pair for pair in observations if pair[0] < day], day)


def _values_in(observations, after: date | None, through: date) -> list:
    """The values dated after ``after`` (None: before every date) and on or before ``through``."""
    return [value for day, value in observations
            if day <= through and (after is None or day > after)]


def _observed(series, series_id, through: date) -> list:
    """The observations of ``series_id`` dated on or before ``through``.

    [] when the id is None or ``series`` does not have it.
    """
    if series_id is None or series_id not in series:
        return []
    return [(day, value) for day, value in series[series_id] if day <= through]


def _percentile(value, values) -> str:
    rank = percentile_rank(value, values)
    return NA if rank is None else str(round(100 * rank))


def _row(cells) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(intro: str, header, rows) -> str:
    """The introduction, an empty line, then the header, ``|---|...|`` and the rows."""
    lines = [intro, "", _row(header), "|" + "---|" * len(header)]
    lines.extend(_row(cells) for cells in rows)
    return "\n".join(lines) + "\n"


# ---- changes and how they are written -----------------------------------------------

def trailing_change(observations, as_of, horizon, kind) -> float | None:
    """How far the level moved over ``horizon`` up to ``as_of``, as level_change measures it.

    The end is the last observation on or before ``as_of``; the start, the last on or
    before the date ``horizon`` back. None when either is missing, when they are the same
    observation (nothing was observed in between), or when level_change gives None. A
    bad ``horizon`` or ``kind`` raises ValueError, whatever the observations.
    """
    count, unit = parse_horizon(horizon)
    _check_kind(kind)
    when = _date_argument(as_of, "as_of")
    pairs = list(observations)
    end = last_on_or_before(pairs, when)
    start = _on_or_before(pairs, _shifted(when, -count, unit))
    if end is None or start is None or start[0] == end[0]:
        return None
    return level_change(start[1], end[1], kind)


def format_level(value) -> str:
    """A level with two decimals; n/a when it is not a number."""
    return f"{value:.2f}" if is_number(value) else NA


def format_change(change, kind) -> str:
    """A change as its kind is written: +12 bp, -1.2% (a return), +0.85 (points)."""
    _check_kind(kind)
    if not is_number(change):
        return NA
    if kind == "bp":
        return f"{change:+.0f} bp"
    if kind == "return":
        return f"{100 * change:+.1f}%"
    return f"{change:+.2f}"


# ---- the tables -----------------------------------------------------------------------

def target_table(as_of, targets, series) -> str:
    """Each target's level on ``as_of``, its changes over LOOKBACKS, its 1y range and rank."""
    when = _date_argument(as_of, "as_of")
    year_ago = _shifted(when, -1, "y")
    rows = []
    for target in targets:
        try:
            kind = _check_kind(target["kind"])
        except ValueError as exc:
            raise ValueError(f"target {target['id']}: {exc}") from None
        cells = [f"{target['name']} ({target['id']}, {target['unit']})"]
        observations = _observed(series, target["id"], when)
        level = last_on_or_before(observations, when)
        if level is None:
            rows.append(cells + [NA] * (len(TARGET_HEADER) - 1))
            continue
        day, value = level
        cells += [format_level(value), day.isoformat()]
        cells += [format_change(trailing_change(observations, when, lookback, kind), kind)
                  for lookback in LOOKBACKS]
        year = _values_in(observations, year_ago, when)
        if year:
            cells += [f"{format_level(min(year))} to {format_level(max(year))}",
                      _percentile(value, year)]
        else:
            cells += [NA, NA]
        rows.append(cells)
    intro = (f"Each level is the last observation on or before {when.isoformat()}; each change "
             "runs to it from the last observation on or before the date that far back.")
    return _table(intro, TARGET_HEADER, rows)


def context_table(as_of, entries, series, published) -> str:
    """Each economic series' latest value on ``as_of``, the one before it, a year earlier."""
    when = _date_argument(as_of, "as_of")
    rows = []
    for entry in entries:
        cells = [f"{entry['name']} ({entry['id']}, {entry['unit']})"]
        vintage = (f"as published by {when.isoformat()}" if entry["id"] in published
                   else "current values")
        observations = _observed(series, entry["id"], when)
        latest = last_on_or_before(observations, when)
        if latest is None:
            rows.append(cells + [NA] * (len(CONTEXT_HEADER) - 2) + [vintage])
            continue
        day, value = latest
        previous = _last_before(observations, day)
        change = None
        if previous is not None and is_number(value) and is_number(previous[1]):
            change = value - previous[1]
        earlier = _on_or_before(observations, _shifted(day, -1, "y"))
        cells += [format_level(value), day.isoformat(),
                  NA if previous is None else format_level(previous[1]),
                  format_change(change, "points"),
                  NA if earlier is None else format_level(earlier[1]),
                  vintage]
        rows.append(cells)
    intro = f"Economic data, the latest observation on or before {when.isoformat()}."
    return _table(intro, CONTEXT_HEADER, rows)


def _share(net, day: date, open_interest) -> str:
    """100 * net / the open interest observed on exactly ``day``; n/a without one or at 0."""
    observed = last_on_or_before(open_interest, day)
    if observed is None or observed[0] != day or not is_number(observed[1]) \
            or observed[1] == 0:
        return NA
    return f"{100 * net / observed[1]:+.1f}%"


def positioning_table(as_of, entries, series) -> str:
    """Each contract's latest COT report public on ``as_of``: net, weekly change, rank, share."""
    when = _date_argument(as_of, "as_of")
    rows = []
    for entry in entries:
        lag = entry["release_lag_days"]
        if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
            raise ValueError(f"positioning entry {entry['id']}: release_lag_days must be an "
                             f"int of at least 0, got {lag!r}")
        cells = [f"{entry['name']} ({entry['id']})"]
        public = _shifted(when, -lag, "d")  # the last report date public on as_of
        reports = [] if public is None else _observed(series, entry["net"], public)
        if not reports:
            rows.append(cells + [NA] * (len(POSITIONING_HEADER) - 1))
            continue
        day, net = last_on_or_before(reports, public)
        before = _last_before(reports, day)
        window = _values_in(reports, _shifted(day, -52, "w"), day)
        cells += [f"{net:,.0f}", day.isoformat(),
                  NA if before is None else f"{net - before[1]:+,.0f}",
                  _percentile(net, window),
                  _share(net, day, _observed(series, entry["open_interest"], day))]
        rows.append(cells)
    intro = ("Speculative positioning: non-commercial net contracts from the CFTC's "
             f"Commitments of Traders, each report as it was public on {when.isoformat()}.")
    return _table(intro, POSITIONING_HEADER, rows)


def calendar_list(as_of, events, through) -> str:
    """The events dated after ``as_of`` and on or before ``through``, by date."""
    when = _date_argument(as_of, "as_of")
    last = _date_argument(through, "through")
    listed = []
    for number, event in enumerate(events, 1):
        raw = event["date"]
        day = parse_iso_date(raw) if isinstance(raw, str) else None
        if day is None:
            raise ValueError(f"calendar event {number} ({event.get('event')!r}): its date "
                             f"must be an ISO date string (YYYY-MM-DD), got {raw!r}")
        if when < day <= last:
            listed.append((day, event["event"]))
    listed.sort(key=lambda item: item[0])  # stable: events on one date keep their order
    lines = [f"Scheduled after {when.isoformat()}, through {last.isoformat()}:", ""]
    lines += [f"- {day.isoformat()}: {text}" for day, text in listed] or ["- none listed"]
    return "\n".join(lines) + "\n"
