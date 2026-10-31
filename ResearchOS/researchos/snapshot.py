"""Snapshot tables (v23): where the markets and the economy stood on a snapshot's date.

Plain-text tables built from stored series -- *observations*, pairs ``(date, number)``
in any order, as ``series.load_series`` returns them: the targets (``target_table``),
the economic data (``context_table``), speculative positioning (``positioning_table``)
and what was scheduled next (``calendar_list``). Every function ignores observations
dated after ``as_of``, and the same series give the same text byte for byte. Each text
is its lines joined by newlines, with a final newline.

Building a snapshot (v24): ``load_macro_config`` reads and checks the configuration of a
run's snapshots, and ``build_snapshot`` saves the macro case of one date from it -- the
tables above and, for each kind of official text, the latest one published by then --
with its resolution manifest and no outcome.

Resolving a snapshot (v25): ``resolve_cases`` reads each target's series as it stands now
and, for each horizon that has ended, records in outcome.json how far the target moved
from the level the case started from, and marks that key resolved in the manifest. The
forecast (prediction.json) is never opened.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from datetime import date
from pathlib import Path

from tools.dates import (first_on_or_after, horizon_end, last_on_or_before, parse_horizon,
                         parse_iso_date, shift_date)
from tools.finance import level_change
from tools.stats import is_number, percentile_rank

from researchos.cases import (KINDS, OUTCOME_FILE, Document, Outcome, Target, TargetOutcome,
                              check_targets, load_case, load_outcome, macro_case,
                              resolution_manifest, save_case, save_outcome, save_resolution,
                              target_key, target_keys)
from researchos.jsonio import read_json
from researchos.loop import PREDICTION_FILE
from researchos.series import _check_series_id, _date_argument, load_series

log = logging.getLogger("researchos")

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


# ---- the configuration (v24) ----------------------------------------------------------

_CASE_PREFIX = re.compile(r"[A-Za-z0-9_-]+")
_HORIZON_RULE = "a count and d, w, m or y, such as '3m', with no whitespace around it"


def _is_series_id(value) -> bool:
    try:
        _check_series_id(value)
    except ValueError:
        return False
    return True


def _is_iso_date(value) -> bool:
    return isinstance(value, str) and parse_iso_date(value) is not None


def _objects(data: dict, name: str, path, *, required: bool = False) -> list:
    """data[name], checked to be a list of JSON objects; [] when absent and not required."""
    if name not in data and not required:
        return []
    items = data.get(name)
    if not isinstance(items, list):
        raise ValueError(f"{path}: {name} must be a list of objects, got "
                         f"{type(items).__name__}")
    for number, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: {name}[{number}] must be an object, got "
                             f"{type(item).__name__}")
    return items


def _require_strings(item: dict, names, where: str) -> None:
    for name in names:
        if not isinstance(item.get(name), str):
            raise ValueError(f"{where}: {name} must be a string, got {item.get(name)!r}")


def _config_horizons(horizons, path) -> list[str]:
    if not isinstance(horizons, list) or not horizons:
        raise ValueError(f"{path}: horizons must be a non-empty list of horizons, got "
                         f"{horizons!r}")
    seen: set[str] = set()
    for horizon in horizons:
        try:
            target_key("target", horizon)
        except ValueError:
            raise ValueError(f"{path}: horizon {horizon!r} is not a valid horizon "
                             f"({_HORIZON_RULE})") from None
        if horizon in seen:
            raise ValueError(f"{path}: horizon {horizon!r} is given twice")
        seen.add(horizon)
    return list(horizons)


def _config_targets(data: dict, horizons: list[str], path) -> list[dict]:
    items = _objects(data, "targets", path, required=True)
    if not items:
        raise ValueError(f"{path}: targets is empty; a snapshot forecasts at least one target")
    targets: list[dict] = []
    seen: set[str] = set()
    for number, item in enumerate(items):
        target_id = item.get("id")
        try:
            target_key(target_id, horizons[0])
        except ValueError:
            raise ValueError(f"{path}: targets[{number}]: id {target_id!r} is not ASCII "
                             f"letters, digits and underscores only") from None
        where = f"{path}: target {target_id}"
        if target_id in seen:
            raise ValueError(f"{where}: two targets have this id (the second is "
                             f"targets[{number}])")
        seen.add(target_id)
        _require_strings(item, ("name", "unit"), where)
        if item.get("kind") not in KINDS:
            raise ValueError(f"{where}: kind {item.get('kind')!r} is not one of "
                             f"{', '.join(KINDS)}")
        optional = item.get("optional", False)
        if not isinstance(optional, bool):
            raise ValueError(f"{where}: optional must be true or false, got {optional!r}")
        targets.append({**item, "optional": optional})
    return targets


def _config_context(data: dict, path) -> list[dict]:
    entries: list[dict] = []
    for number, item in enumerate(_objects(data, "context", path)):
        where = f"{path}: context[{number}]"
        if not _is_series_id(item.get("id")):
            raise ValueError(f"{where}: id {item.get('id')!r} is not a series id (letters, "
                             f"digits and '_')")
        _require_strings(item, ("name", "unit"), where)
        entries.append(dict(item))
    return entries


def _config_positioning(data: dict, path) -> list[dict]:
    entries: list[dict] = []
    for number, item in enumerate(_objects(data, "positioning", path)):
        where = f"{path}: positioning[{number}]"
        for name in ("id", "net"):
            if not _is_series_id(item.get(name)):
                raise ValueError(f"{where}: {name} {item.get(name)!r} is not a series id "
                                 f"(letters, digits and '_')")
        _require_strings(item, ("name",), where)
        open_interest = item.get("open_interest")
        if open_interest is not None and not _is_series_id(open_interest):
            raise ValueError(f"{where}: open_interest {open_interest!r} is neither a series "
                             f"id nor null")
        lag = item.get("release_lag_days")
        if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
            raise ValueError(f"{where}: release_lag_days must be an int of at least 0, got "
                             f"{lag!r}")
        entries.append({**item, "open_interest": open_interest})
    return entries


def _config_documents(data: dict, path) -> list[dict]:
    entries: list[dict] = []
    for number, item in enumerate(_objects(data, "documents", path)):
        where = f"{path}: documents[{number}]"
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"{where}: kind must be a non-empty string, got {kind!r}")
        _require_strings(item, ("title",), where)
        if not _is_iso_date(item.get("dated")):
            raise ValueError(f"{where}: dated {item.get('dated')!r} is not an ISO date "
                             f"(YYYY-MM-DD)")
        given = [name for name in ("url", "text") if name in item]
        if len(given) != 1:
            raise ValueError(f"{where}: give exactly one of url and text, got "
                             f"{' and '.join(given) or 'neither'}")
        _require_strings(item, given, where)
        entries.append(dict(item))
    return entries


def _config_calendar(data: dict, path) -> list[dict]:
    entries: list[dict] = []
    for number, item in enumerate(_objects(data, "calendar", path)):
        where = f"{path}: calendar[{number}]"
        if not _is_iso_date(item.get("date")):
            raise ValueError(f"{where}: date {item.get('date')!r} is not an ISO date "
                             f"(YYYY-MM-DD)")
        _require_strings(item, ("event",), where)
        entries.append(dict(item))
    return entries


def load_macro_config(path) -> dict:
    """The configuration of a run's snapshots, read with read_json and checked.

    ``context``, ``positioning``, ``documents`` and ``calendar`` default to [], a target's
    ``optional`` to False and a positioning entry's ``open_interest`` to None. Anything
    malformed raises ValueError naming ``path``; keys not named in SPEC.md are ignored.
    OSError (a missing file) passes through.
    """
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a macro config must be a JSON object, got "
                         f"{type(data).__name__}")
    prefix = data.get("case_prefix")
    if not isinstance(prefix, str) or _CASE_PREFIX.fullmatch(prefix) is None:
        raise ValueError(f"{path}: case_prefix must be ASCII letters, digits, '_' and '-', "
                         f"got {prefix!r}")
    horizons = _config_horizons(data.get("horizons"), path)
    # Checked in this order: the values of a dict display are evaluated left to right.
    return {**data,
            "horizons": horizons,
            "targets": _config_targets(data, horizons, path),
            "context": _config_context(data, path),
            "positioning": _config_positioning(data, path),
            "documents": _config_documents(data, path),
            "calendar": _config_calendar(data, path)}


# ---- building a snapshot (v24) --------------------------------------------------------

def _stored(series_dir, series_id, when: date) -> list:
    """load_series(series_dir, series_id, when); no observations when there is no file."""
    try:
        return load_series(series_dir, series_id, when)
    except OSError:
        return []


def _horizon_end(when: date, horizon: str, case_id: str) -> date:
    try:
        return horizon_end(when, horizon)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"snapshot {case_id}: the {horizon} horizon from {when.isoformat()} "
                         f"does not end on a date: {exc}") from None


def _latest_of_each_kind(entries, when: date) -> list[tuple[date, dict]]:
    """(date, entry) per kind, in the order the kinds first appear in ``entries``.

    Each is the entry dated latest on or before ``when`` (of two on the same date, the
    later entry); a kind with no such entry gives nothing.
    """
    taken: dict[str, tuple[date, dict] | None] = {}
    for entry in entries:
        best = taken.setdefault(entry["kind"], None)
        dated = parse_iso_date(entry["dated"])
        if dated <= when and (best is None or dated >= best[0]):
            taken[entry["kind"]] = (dated, entry)
    return [chosen for chosen in taken.values() if chosen is not None]


def build_snapshot(root, config, as_of, *, series_dir, fetch_page) -> dict:
    """Build and save the macro case of ``as_of`` from a config load_macro_config returned.

    Only what was known on ``as_of`` is used: the stored series cut at it, and of each kind
    of document the latest dated on or before it (``fetch_page(url).text`` for one given by
    address; a fetch raising OSError or ValueError leaves it out, with a warning). A case
    whose prediction.json or outcome.json exists is refused before anything is read or
    written; a missing target that is not optional, before anything is written. The case
    is saved without an outcome, with its resolution manifest, nothing resolved. Returns
    the summary {"case_id", "as_of", "targets", "left_out", "documents", "keys"}.
    """
    when = _date_argument(as_of, "as_of")
    day = when.isoformat()
    case_id = f"{config['case_prefix']}-{day}"
    case_dir = Path(root) / case_id
    for name, reason in ((PREDICTION_FILE, "its forecast is already sealed"),
                         (OUTCOME_FILE, "its outcome is already written")):
        if (case_dir / name).exists():
            raise ValueError(f"snapshot {case_id}: {case_dir / name} exists ({reason}); a "
                             f"case is never rebuilt under it")
    horizons = list(config["horizons"])
    through = max(_horizon_end(when, horizon, case_id) for horizon in horizons)

    kept: list[dict] = []
    series: dict[str, list] = {}
    left_out: list[str] = []
    missing: list[str] = []
    for entry in config["targets"]:
        observations = _stored(series_dir, entry["id"], when)
        if last_on_or_before(observations, when) is not None:
            kept.append(entry)
            series[entry["id"]] = observations
        elif entry.get("optional", False):
            left_out.append(entry["id"])
        else:
            missing.append(entry["id"])
    if missing:
        raise ValueError(f"snapshot {case_id}: no observation on or before {day} under "
                         f"{series_dir} for the target(s) {', '.join(missing)}")
    if not kept:
        raise ValueError(f"snapshot {case_id}: no target has an observation on or before "
                         f"{day} (left out: {', '.join(left_out)}); a macro case forecasts "
                         f"at least one target")
    for target_id in left_out:
        log.warning("snapshot %s: optional target %s has no observation on or before %s; "
                    "it is left out", case_id, target_id, day)

    targets: list[Target] = []
    for entry in kept:
        observations = series[entry["id"]]
        level_day, level = last_on_or_before(observations, when)
        targets.append(Target(
            id=entry["id"], name=entry["name"], kind=entry["kind"], horizons=list(horizons),
            as_of_date=level_day.isoformat(), as_of_value=level,
            trailing={horizon: trailing_change(observations, when, horizon, entry["kind"])
                      for horizon in horizons}))
    check_targets(targets)  # what macro_case would refuse, refused before anything is fetched

    documents = [Document("dashboard", f"Markets as of {day}",
                          target_table(when, kept, series))]
    context = config.get("context", [])
    if context:
        stored = {entry["id"]: _stored(series_dir, entry["id"], when) for entry in context}
        published = {entry["id"] for entry in context
                     if (Path(series_dir) / f"{entry['id']}@{day}.json").exists()}
        documents.append(Document("economy", f"Economic data as of {day}",
                                  context_table(when, context, stored, published)))
    positioning = config.get("positioning", [])
    if positioning:
        ids = [entry[name] for entry in positioning for name in ("net", "open_interest")
               if entry.get(name) is not None]
        stored = {series_id: _stored(series_dir, series_id, when) for series_id in ids}
        documents.append(Document("positioning", f"Positioning as of {day}",
                                  positioning_table(when, positioning, stored)))
    calendar = config.get("calendar", [])
    if calendar:
        documents.append(Document("calendar", f"Calendar after {day}",
                                  calendar_list(when, calendar, through)))
    for dated, entry in _latest_of_each_kind(config.get("documents", []), when):
        title = f"{entry['title']} ({dated.isoformat()})"
        if "url" not in entry:
            documents.append(Document(entry["kind"], title, entry["text"]))
            continue
        url = entry["url"]
        try:
            page = fetch_page(url)
        except (OSError, ValueError) as exc:  # a dead link, a page that is not text
            log.warning("snapshot %s: %s is left out: cannot fetch %s (%s)", case_id, title,
                        url, exc)
            continue
        documents.append(Document(entry["kind"], title, page.text, source=url))

    case = macro_case(case_id, day, targets, documents)
    manifest = resolution_manifest(case)  # nothing resolved; ready before anything is written
    save_case(root, case)
    save_resolution(root, case_id, manifest)
    return {"case_id": case_id, "as_of": day, "targets": [target.id for target in targets],
            "left_out": left_out, "documents": [document.kind for document in documents],
            "keys": target_keys(case)}


# ---- resolving a snapshot (v25) -------------------------------------------------------

def _current_series(series_dir, series_id) -> list:
    """load_series(series_dir, series_id): the current file, not cut at any date.

    No observations when there is no file (OSError); a malformed file's ValueError passes
    through.
    """
    try:
        return load_series(series_dir, series_id)
    except OSError:
        return []


def _checked(root, case_id: str) -> tuple:
    """Step 1 of resolve_cases for one case: (the case, its outcome so far)."""
    case = load_case(root, case_id)  # OSError: no such case
    if case.domain != "macro":
        raise ValueError(f"case {case_id}: domain {case.domain!r} is not 'macro'; only a "
                         f"snapshot's targets resolve")
    resolution_manifest(case)  # checks the case's targets and date
    keys = target_keys(case)
    if (Path(root) / case_id / OUTCOME_FILE).exists():
        outcome = load_outcome(root, case_id)
    else:
        outcome = Outcome()
    for key in outcome.targets:
        if key not in keys:
            raise ValueError(f"case {case_id}: {OUTCOME_FILE} has an outcome for {key!r}, "
                             f"which is not one of its target keys ({', '.join(keys)})")
    return case, outcome


def _resolve_key(target: Target, horizon: str, as_of: date, series_dir):
    """The TargetOutcome of a key not resolved yet; else "pending" or "unmeasurable"."""
    observations = _current_series(series_dir, target.id)
    end_day = horizon_end(as_of, horizon)
    if first_on_or_after(observations, end_day) is None:
        return "pending"  # a later observation could still fall inside the horizon
    end = last_on_or_before(observations, end_day)
    if end is None or end[0] < parse_iso_date(target.as_of_date):
        return "unmeasurable"  # the file no longer holds the window
    change = level_change(target.as_of_value, end[1], target.kind)
    if change is None:
        return "unmeasurable"  # a return from a level at or below zero
    # The start is the level the case recorded, not what the series now holds for that date.
    return TargetOutcome(target=target.id, horizon=horizon, kind=target.kind,
                         start_date=target.as_of_date, start=target.as_of_value,
                         end_date=end[0].isoformat(), end=end[1], change=change,
                         up=change > 0)


def resolve_cases(root, case_ids, *, series_dir) -> list[dict]:
    """Resolve each horizon of the snapshots' targets that has ended; one summary per case.

    Every case is checked first (an id given twice, no such case (OSError), a case that is
    not a macro case, a manifest that cannot be made, an outcome key the case does not
    have); then each key is found resolved, pending or unmeasurable from the current
    series files under ``series_dir``; only then, case by case, is outcome.json written
    (when any key is resolved) and resolution.json. A key already in outcome.json is kept
    exactly and its series is not read for it. prediction.json is never opened. Each
    summary is {"case_id", "resolved", "new", "pending", "unmeasurable"}, every list in
    the order of target_keys(case).
    """
    checked = []
    seen: set[str] = set()
    for case_id in case_ids:
        if case_id in seen:
            raise ValueError(f"case {case_id} is named twice; a call resolves each case once")
        seen.add(case_id)
        checked.append((case_id, *_checked(root, case_id)))

    planned = []
    for case_id, case, outcome in checked:
        as_of = parse_iso_date(case.event_date)
        resolved: dict[str, TargetOutcome] = {}
        states: dict[str, list[str]] = {"new": [], "pending": [], "unmeasurable": []}
        for target in case.targets:  # in the order of target_keys(case)
            for horizon in target.horizons:
                key = target_key(target.id, horizon)
                if key in outcome.targets:  # kept exactly, even when the series has changed
                    resolved[key] = outcome.targets[key]
                    continue
                state = _resolve_key(target, horizon, as_of, series_dir)
                if isinstance(state, TargetOutcome):
                    resolved[key] = state
                    states["new"].append(key)
                else:
                    states[state].append(key)
        planned.append((case_id, outcome, resolved, resolution_manifest(case, resolved),
                        states))

    summaries = []
    for case_id, outcome, resolved, manifest, states in planned:
        if resolved:
            save_outcome(root, case_id, dataclasses.replace(outcome, targets=resolved))
        save_resolution(root, case_id, manifest)
        summaries.append({"case_id": case_id, "resolved": list(resolved), **states})
    return summaries


def resolve_case(root, case_id, *, series_dir) -> dict:
    """resolve_cases(root, [case_id], series_dir=series_dir)[0]."""
    return resolve_cases(root, [case_id], series_dir=series_dir)[0]
