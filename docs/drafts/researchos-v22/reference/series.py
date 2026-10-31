"""Series from free sources: FRED and ALFRED CSV, the CFTC's COT JSON (v22 reference)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from tools.dates import parse_iso_date
from tools.stats import is_number

from researchos.jsonio import write_json
from researchos.sources import _decode, _parse_content_type, _urlopen, load_closes

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ALFRED_CSV = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
CFTC_LEGACY = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

_ID = re.compile(r"[A-Za-z0-9_]+")
_CODE = re.compile(r"[0-9A-Z]{6}")
_DECIMAL = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_INTEGER = re.compile(r"-?[0-9]+")
_VINTAGE = re.compile(r"_[0-9]{8}")
_COUNTS = ("noncomm_positions_long_all", "noncomm_positions_short_all", "open_interest_all")


def _day(value, what: str) -> date:
    if isinstance(value, (date, str)):
        day = parse_iso_date(value)
        if day is not None:
            return day
    raise ValueError(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}")


def _series_id(value) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"series id {value!r} must be ASCII letters, digits and underscores")
    return value


def _code(value) -> str:
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise ValueError(f"contract code {value!r} must be six digits or capital letters")
    return value


def fred_url(series_id, *, start=None, vintage=None) -> str:
    series_id = _series_id(series_id)
    start_day = None if start is None else _day(start, "start")
    if vintage is None:
        url = f"{FRED_CSV}?id={series_id}"
    else:
        url = f"{ALFRED_CSV}?id={series_id}&vintage_date={_day(vintage, 'vintage').isoformat()}"
    if start_day is not None:
        url += f"&cosd={start_day.isoformat()}"
    return url


def _limit(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50000:
        raise ValueError(f"limit must be an integer from 1 to 50000, got {limit!r}")
    return limit


def cftc_url(code, *, limit=600) -> str:
    code = _code(code)
    limit = _limit(limit)
    return (f"{CFTC_LEGACY}?cftc_contract_market_code={code}"
            f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit={limit}")


def parse_fred_csv(text, series_id) -> list[tuple[date, float]]:
    series_id = _series_id(series_id)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{series_id}: no CSV header")
    header = [f.strip() for f in lines[0].split(",")]
    ok = (len(header) == 2 and header[0] in ("observation_date", "DATE")
          and (header[1] == series_id
               or (header[1].startswith(series_id)
                   and _VINTAGE.fullmatch(header[1][len(series_id):]) is not None)))
    if not ok:
        raise ValueError(f"{series_id}: not a FRED CSV of this series: {lines[0][:80]!r}")
    found: dict[date, float] = {}
    for line in lines[1:]:
        fields = [f.strip() for f in line.split(",")]
        if len(fields) != 2:
            raise ValueError(f"{series_id}: expected date,value, got {line[:80]!r}")
        day = parse_iso_date(fields[0])
        if day is None:
            raise ValueError(f"{series_id}: not an ISO date in {line[:80]!r}")
        value = fields[1]
        if value in ("", "."):
            found.pop(day, None)
            continue
        if _DECIMAL.fullmatch(value) is None or not is_number(float(value)):
            raise ValueError(f"{series_id}: not a number in {line[:80]!r}")
        found.pop(day, None)
        found[day] = float(value)
    return sorted(found.items())


def _count(row, field, code):
    value = row.get(field)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value.strip()) is not None:
        return int(value.strip())
    raise ValueError(f"{code}: {field} is not a count: {value!r}")


def parse_cftc(payload, code):
    code = _code(code)
    if not isinstance(payload, list):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise ValueError(f"{code}: expected a list of reports"
                         + (f": {message}" if message else ""))
    net: dict[date, float] = {}
    oi: dict[date, float] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError(f"{code}: a report is not an object: {row!r}")
        row_code = row.get("cftc_contract_market_code")
        if not isinstance(row_code, str) or row_code.strip() != code:
            raise ValueError(f"{code}: a report of another contract: {row_code!r}")
        raw = row.get("report_date_as_yyyy_mm_dd")
        day = parse_iso_date(raw.split("T", 1)[0]) if isinstance(raw, str) else None
        if day is None:
            raise ValueError(f"{code}: bad report date {raw!r}")
        long_, short, open_interest = (_count(row, f, code) for f in _COUNTS)
        net.pop(day, None)
        oi.pop(day, None)
        net[day] = float(long_ - short)
        oi[day] = float(open_interest)
    return sorted(net.items()), sorted(oi.items())


def save_series(path, observations) -> None:
    found: dict[date, object] = {}
    for pair in observations:
        try:
            day, value = pair
        except (TypeError, ValueError):
            raise ValueError(f"expected (date, number), got {pair!r}") from None
        if not isinstance(day, date) or isinstance(day, datetime) or not is_number(value):
            raise ValueError(f"expected (date, number), got {pair!r}")
        found.pop(day, None)
        found[day] = value
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, [[d.isoformat(), v] for d, v in sorted(found.items())])


def load_series(series_dir, series_id, as_of=None) -> list[tuple[date, float]]:
    series_id = _series_id(series_id)
    directory = Path(series_dir)
    if as_of is None:
        return load_closes(directory / f"{series_id}.json")
    day = _day(as_of, "as_of")
    vintage = directory / f"{series_id}@{day.isoformat()}.json"
    path = vintage if vintage.is_file() else directory / f"{series_id}.json"
    return [(d, v) for d, v in load_closes(path) if d <= day]


def fetch_text(url, *, user_agent, transport=None) -> str:
    body, content_type = (transport or _urlopen)(url, {"User-Agent": user_agent})
    _, charset = _parse_content_type(content_type or "")
    return _decode(body, charset)


def fetch_fred(series_id, *, user_agent, start=None, vintage=None, transport=None):
    url = fred_url(series_id, start=start, vintage=vintage)
    return parse_fred_csv(fetch_text(url, user_agent=user_agent, transport=transport), series_id)


def fetch_cftc(code, *, user_agent, limit=600, transport=None):
    url = cftc_url(code, limit=limit)
    text = fetch_text(url, user_agent=user_agent, transport=transport)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{code}: the CFTC did not return JSON ({exc})") from exc
    return parse_cftc(payload, code)

