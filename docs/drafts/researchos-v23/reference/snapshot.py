"""Snapshot tables: the markets, the economy, positioning and the calendar (v23 reference)."""

from __future__ import annotations

from datetime import date

from tools.dates import last_on_or_before, parse_horizon, parse_iso_date, shift_date
from tools.finance import level_change
from tools.stats import is_number, percentile_rank

from researchos.cases import KINDS

LOOKBACKS = ("1w", "1m", "3m", "1y")
NA = "n/a"


def _day(value) -> date:
    if isinstance(value, (date, str)):
        day = parse_iso_date(value)
        if day is not None:
            return day
    raise ValueError(f"expected an ISO date (YYYY-MM-DD), got {value!r}")


def _past(observations, as_of: date):
    return [(d, v) for d, v in observations if d <= as_of]


def trailing_change(observations, as_of, horizon, kind):
    n, unit = parse_horizon(horizon)
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} is not one of {KINDS}")
    day = _day(as_of)
    end = last_on_or_before(observations, day)
    start = last_on_or_before(observations, shift_date(day, -n, unit))
    if end is None or start is None or start[0] == end[0]:
        return None
    return level_change(start[1], end[1], kind)


def format_level(value) -> str:
    return f"{value:.2f}" if is_number(value) else NA


def format_change(change, kind) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} is not one of {KINDS}")
    if not is_number(change):
        return NA
    if kind == "bp":
        return f"{change:+.0f} bp"
    if kind == "return":
        return f"{100 * change:+.1f}%"
    return f"{change:+.2f}"


def _row(cells) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(intro, header, rows) -> str:
    lines = [intro, "", _row(header), "|" + "---|" * len(header)]
    lines += [_row(r) for r in rows]
    return "\n".join(lines) + "\n"


def target_table(as_of, targets, series) -> str:
    day = _day(as_of)
    year_ago = shift_date(day, -1, "y")
    rows = []
    for t in targets:
        obs = _past(series.get(t["id"], []), day)
        name = f"{t['name']} ({t['id']}, {t['unit']})"
        level = last_on_or_before(obs, day)
        if level is None:
            rows.append([name] + [NA] * 8)
            continue
        changes = [format_change(trailing_change(obs, day, L, t["kind"]), t["kind"])
                   for L in LOOKBACKS]
        values = [v for d, v in obs if year_ago < d <= day]
        if values:
            span = f"{format_level(min(values))} to {format_level(max(values))}"
            pct = str(round(100 * percentile_rank(level[1], values)))
        else:
            span = pct = NA
        rows.append([name, format_level(level[1]), level[0].isoformat(), *changes, span, pct])
    intro = (f"Each level is the last observation on or before {day.isoformat()}; each change "
             f"runs to it from the last observation on or before the date that far back.")
    return _table(intro, ["Target", "Level", "On", *LOOKBACKS, "1y range", "1y percentile"], rows)


def context_table(as_of, entries, series, published) -> str:
    day = _day(as_of)
    published = set(published)
    rows = []
    for e in entries:
        obs = _past(series.get(e["id"], []), day)
        name = f"{e['name']} ({e['id']}, {e['unit']})"
        vintage = f"as published by {day.isoformat()}" if e["id"] in published else "current values"
        latest = last_on_or_before(obs, day)
        if latest is None:
            rows.append([name] + [NA] * 5 + [vintage])
            continue
        previous = last_on_or_before(obs, shift_date(latest[0], -1, "d"))
        earlier = last_on_or_before(obs, shift_date(latest[0], -1, "y"))
        change = latest[1] - previous[1] if previous is not None else None
        rows.append([name, format_level(latest[1]), latest[0].isoformat(),
                     format_level(previous[1] if previous else None),
                     format_change(change, "points"),
                     format_level(earlier[1] if earlier else None), vintage])
    intro = f"Economic data, the latest observation on or before {day.isoformat()}."
    return _table(intro, ["Series", "Latest", "For", "Previous", "Change", "A year earlier",
                          "Vintage"], rows)


def positioning_table(as_of, entries, series) -> str:
    day = _day(as_of)
    rows = []
    for e in entries:
        lag = e["release_lag_days"]
        if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
            raise ValueError(f"positioning {e['id']}: release_lag_days must be an integer >= 0, "
                             f"got {lag!r}")
        public = shift_date(day, -lag, "d")
        reports = [(d, v) for d, v in series.get(e["net"], []) if d <= public]
        name = f"{e['name']} ({e['id']})"
        latest = last_on_or_before(reports, public)
        if latest is None:
            rows.append([name] + [NA] * 5)
            continue
        when, net = latest
        before = last_on_or_before(reports, shift_date(when, -1, "d"))
        change = f"{net - before[1]:+,.0f}" if before is not None else NA
        start = shift_date(when, -52, "w")
        values = [v for d, v in reports if start < d <= when]
        pct = str(round(100 * percentile_rank(net, values)))
        share = NA
        if e.get("open_interest") is not None:
            same_day = [v for d, v in series.get(e["open_interest"], []) if d == when]
            if same_day and same_day[-1] != 0:
                share = f"{100 * net / same_day[-1]:+.1f}%"
        rows.append([name, f"{net:,.0f}", when.isoformat(), change, pct, share])
    intro = ("Speculative positioning: non-commercial net contracts from the CFTC's Commitments "
             f"of Traders, each report as it was public on {day.isoformat()}.")
    return _table(intro, ["Contract", "Net", "Report of", "Weekly change", "52-week percentile",
                          "Share of open interest"], rows)


def calendar_list(as_of, events, through) -> str:
    day, last = _day(as_of), _day(through)
    kept = []
    for e in events:
        when = parse_iso_date(e["date"]) if isinstance(e["date"], str) else None
        if when is None:
            raise ValueError(f"calendar event {e.get('event')!r}: date {e['date']!r} is not an ISO date")
        if day < when <= last:
            kept.append((when, e["event"]))
    kept.sort(key=lambda item: item[0])
    lines = [f"Scheduled after {day.isoformat()}, through {last.isoformat()}:", ""]
    lines += [f"- {w.isoformat()}: {ev}" for w, ev in kept] or ["- none listed"]
    return "\n".join(lines) + "\n"
