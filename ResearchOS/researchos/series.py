"""Series from free sources: FRED and ALFRED, and the CFTC's Commitments of Traders.

Each series is fetched with one GET (``fetch_text``: a transport ``transport(url,
headers) -> (body, content_type)``, urllib by default), checked (``parse_fred_csv``,
``parse_cftc``) and stored as the ``[["YYYY-MM-DD", value], ...]`` files that
``sources.load_closes`` reads (``save_series``, ``load_series``). Nothing here calls
a model.
"""

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

MAX_CFTC_LIMIT = 50000

_SERIES_ID = re.compile(r"[A-Za-z0-9_]+")
_CONTRACT_CODE = re.compile(r"[0-9A-Z]{6}")
_DECIMAL = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_COUNT = re.compile(r"-?[0-9]+")
# The first column of a FRED CSV: its name now, and the one older files carry.
_DATE_COLUMNS = ("observation_date", "DATE")
# What FRED writes for a date without a value.
_NO_VALUE = ("", ".")
# The fields of a CFTC report that are read.
_CODE_FIELD = "cftc_contract_market_code"
_DATE_FIELD = "report_date_as_yyyy_mm_dd"
_LONG_FIELD = "noncomm_positions_long_all"
_SHORT_FIELD = "noncomm_positions_short_all"
_OPEN_INTEREST_FIELD = "open_interest_all"
# How much of a line an error message quotes (an error page can be one long line).
_QUOTE = 80


# ---- arguments ----------------------------------------------------------------------

def _check_series_id(series_id) -> str:
    if not isinstance(series_id, str) or _SERIES_ID.fullmatch(series_id) is None:
        raise ValueError(f"a series id is letters, digits and '_', got {series_id!r}")
    return series_id


def _check_code(code) -> str:
    if not isinstance(code, str) or _CONTRACT_CODE.fullmatch(code) is None:
        raise ValueError(f"a CFTC contract code is six digits or capital letters, got {code!r}")
    return code


def _date_argument(value, name: str) -> date:
    """A date argument: a date, or a string parse_iso_date reads; ValueError otherwise."""
    when = parse_iso_date(value) if isinstance(value, (date, str)) else None
    if when is None:
        raise ValueError(f"{name} must be a date or an ISO date string (YYYY-MM-DD), "
                         f"got {value!r}")
    return when


# ---- addresses ----------------------------------------------------------------------

def fred_url(series_id, *, start=None, vintage=None) -> str:
    """FRED's CSV of a series or, with ``vintage``, ALFRED's as it was known on that date.

    ``&cosd=<start>`` (the first observation date) is appended when ``start`` is given.
    """
    _check_series_id(series_id)
    first = None if start is None else _date_argument(start, "start")
    if vintage is None:
        url = f"{FRED_CSV}?id={series_id}"
    else:
        known = _date_argument(vintage, "vintage")
        url = f"{ALFRED_CSV}?id={series_id}&vintage_date={known.isoformat()}"
    if first is not None:
        url += f"&cosd={first.isoformat()}"
    return url


def cftc_url(code, *, limit=600) -> str:
    """The CFTC's legacy Commitments of Traders reports of a contract, the newest first."""
    _check_code(code)
    if isinstance(limit, bool) or not isinstance(limit, int) \
            or not 1 <= limit <= MAX_CFTC_LIMIT:
        raise ValueError(f"limit must be an int from 1 to {MAX_CFTC_LIMIT}, got {limit!r}")
    return (f"{CFTC_LEGACY}?cftc_contract_market_code={code}"
            f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit={limit}")


# ---- reading what the sources return ------------------------------------------------

def _is_fred_header(fields: list[str], series_id: str) -> bool:
    """observation_date (or DATE), then the series id or its ALFRED column <ID>_<YYYYMMDD>."""
    if len(fields) != 2 or fields[0] not in _DATE_COLUMNS:
        return False
    column = fields[1]
    return column == series_id \
        or re.fullmatch(re.escape(series_id) + r"_[0-9]{8}", column) is not None


def _fred_observation(line: str, number: int, series_id: str) -> tuple[date, float | None]:
    """The date and value (None when the source has none) of one observation line."""
    fields = [field.strip() for field in line.split(",")]
    day = parse_iso_date(fields[0]) if len(fields) == 2 else None
    if day is None:
        raise ValueError(f"FRED series {series_id}: line {number} is not 'date,value': "
                         f"{line.strip()[:_QUOTE]!r}")
    raw = fields[1]
    if raw in _NO_VALUE:
        return day, None
    value = float(raw) if _DECIMAL.fullmatch(raw) else None
    if not is_number(value):  # not a decimal, or too large to be finite
        raise ValueError(f"FRED series {series_id}: line {number} has the value "
                         f"{raw[:_QUOTE]!r}, not a decimal number")
    return day, value


def parse_fred_csv(text, series_id) -> list[tuple[date, float]]:
    """The observations of a FRED or ALFRED CSV, sorted by date.

    Lines that are blank are skipped. The header is ``observation_date`` (or ``DATE``)
    and the series id (or its ALFRED vintage column ``<ID>_<YYYYMMDD>``); every later
    line is ``date,value``, the value empty or "." when there is none (that date is left
    out). Of two lines with the same date the later wins, even one with no value.
    Anything else -- an error page, the CSV of another series -- raises ValueError
    naming the series id.
    """
    _check_series_id(series_id)
    if not isinstance(text, str):
        raise ValueError(f"FRED series {series_id}: expected the CSV as text, "
                         f"got {type(text).__name__}")
    values: dict[date, float | None] = {}
    header_seen = False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if not header_seen:
            if not _is_fred_header([field.strip() for field in line.split(",")], series_id):
                raise ValueError(f"FRED series {series_id}: not a CSV of this series; its "
                                 f"first line is {line.strip()[:_QUOTE]!r}")
            header_seen = True
            continue
        day, value = _fred_observation(line, number, series_id)
        values[day] = value  # the later line wins, even with no value
    if not header_seen:
        raise ValueError(f"FRED series {series_id}: no CSV header (the response is empty)")
    return [(day, values[day]) for day in sorted(values) if values[day] is not None]


def _field(row: dict, name: str, where: str):
    if name not in row:
        raise ValueError(f"{where}: no {name}")
    return row[name]


def _report_date(value) -> date | None:
    """The date of "2026-10-27T00:00:00.000" (the part before the first T); None if none."""
    if not isinstance(value, str):
        return None
    return parse_iso_date(value.split("T", 1)[0])


def _count(value, name: str, where: str) -> int:
    """A count: an int that is not a bool, or a string of digits (after an optional '-')."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _COUNT.fullmatch(value.strip()):
        try:
            return int(value.strip())
        except ValueError:  # more digits than int() converts
            pass
    raise ValueError(f"{where}: {name} is not a count: {repr(value)[:_QUOTE]}")


def parse_cftc(payload, code) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    """The net non-commercial position (long minus short) and the open interest, by date.

    ``payload`` is the decoded JSON ``cftc_url`` returns: a list of reports, each of
    contract ``code``. Of two reports with the same date the later wins in both
    series. Anything else raises ValueError naming the code (and, when the CFTC
    reports an error, its message).
    """
    _check_code(code)
    if not isinstance(payload, list):
        detail = ""
        if isinstance(payload, dict) and "message" in payload:
            detail = f": {payload['message']}"
        raise ValueError(f"CFTC contract {code}: expected a JSON list of reports, got "
                         f"{type(payload).__name__}{detail}")
    reports: dict[date, tuple[float, float]] = {}
    for number, row in enumerate(payload, 1):
        where = f"CFTC contract {code}, report {number}"
        if not isinstance(row, dict):
            raise ValueError(f"{where}: expected a JSON object, got {type(row).__name__}")
        row_code = _field(row, _CODE_FIELD, where)
        if not isinstance(row_code, str) or row_code.strip() != code:
            raise ValueError(f"{where}: a report of contract {row_code!r}, not of {code}")
        day = _report_date(_field(row, _DATE_FIELD, where))
        if day is None:
            raise ValueError(f"{where}: {_DATE_FIELD} is not an ISO date: "
                             f"{repr(row[_DATE_FIELD])[:_QUOTE]}")
        long_count, short_count, open_interest = (
            _count(_field(row, name, where), name, where)
            for name in (_LONG_FIELD, _SHORT_FIELD, _OPEN_INTEREST_FIELD))
        try:
            reports[day] = (float(long_count - short_count), float(open_interest))
        except OverflowError:
            raise ValueError(f"{where}: a count too large to hold") from None
    days = sorted(reports)
    return [(day, reports[day][0]) for day in days], [(day, reports[day][1]) for day in days]


# ---- files ----------------------------------------------------------------------------

def save_series(path, observations) -> None:
    """Write ``(date, number)`` pairs as a series file through write_json, sorted by date.

    Of two pairs with the same date the later is kept; the parent directories are
    created. Any other pair raises ValueError before anything is written.
    """
    latest: dict[date, object] = {}
    for pair in observations:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError(f"{path}: expected (date, number) pairs, got {pair!r}")
        day, value = pair
        if not isinstance(day, date) or isinstance(day, datetime) or not is_number(value):
            raise ValueError(f"{path}: expected (date, number) pairs, got {pair!r}")
        latest[day] = value
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, [[day.isoformat(), latest[day]] for day in sorted(latest)])


def load_series(series_dir, series_id, as_of=None) -> list[tuple[date, float]]:
    """A stored series through load_closes: the current file, or as known on ``as_of``.

    With ``as_of`` the file ``<series_id>@<YYYY-MM-DD>.json`` is read when it exists,
    the current ``<series_id>.json`` otherwise, and only the observations dated on or
    before ``as_of`` are returned.
    """
    _check_series_id(series_id)
    current = Path(series_dir) / f"{series_id}.json"
    if as_of is None:
        return load_closes(current)
    when = _date_argument(as_of, "as_of")
    known = Path(series_dir) / f"{series_id}@{when.isoformat()}.json"
    observations = load_closes(known if known.exists() else current)
    return [(day, value) for day, value in observations if day <= when]


# ---- fetching -------------------------------------------------------------------------

def fetch_text(url: str, *, user_agent: str, transport=None) -> str:
    """One GET of ``url`` with ``User-Agent: <user_agent>``; the body as text.

    ``transport(url, headers) -> (body, content_type)`` defaults to urllib, as for
    ``sources.get_page`` (30 seconds' timeout; HTTP errors are OSErrors). The body is
    decoded by the charset its content type names (UTF-8 when it names none, or one
    Python does not know), a byte order mark dropped. Any content type is accepted:
    FRED serves ``application/csv``. The transport's errors propagate.
    """
    body, content_type = (transport or _urlopen)(url, {"User-Agent": user_agent})
    _, charset = _parse_content_type(content_type or "")
    return _decode(body, charset)


def fetch_fred(series_id, *, user_agent: str, start=None, vintage=None,
               transport=None) -> list[tuple[date, float]]:
    """A FRED series (ALFRED's as it was known on ``vintage``), from ``start`` when given."""
    url = fred_url(series_id, start=start, vintage=vintage)  # the arguments, checked first
    return parse_fred_csv(fetch_text(url, user_agent=user_agent, transport=transport),
                          series_id)


def fetch_cftc(code, *, user_agent: str, limit=600,
               transport=None) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    """The net non-commercial position and the open interest of a CFTC contract."""
    url = cftc_url(code, limit=limit)  # the arguments, checked first
    text = fetch_text(url, user_agent=user_agent, transport=transport)
    try:
        payload = json.loads(text)
    except ValueError as exc:  # json.JSONDecodeError, or an integer too long to read
        raise ValueError(f"CFTC contract {code}: {url} did not return JSON: {exc}") from None
    return parse_cftc(payload, code)
