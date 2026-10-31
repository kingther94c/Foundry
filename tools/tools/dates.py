"""Fiscal calendar, business-day and horizon arithmetic."""

import calendar
import datetime
import re
from collections.abc import Iterable, Sequence


def _check_month(month: int) -> None:
    """Raise ValueError unless month is within 1..12."""
    if not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError(f"fiscal_year_end_month must be within 1..12, got {month!r}")


def fiscal_quarter(d: datetime.date, fiscal_year_end_month: int = 12) -> tuple[int, int]:
    """Return (fiscal_year, quarter) for date d; never None."""
    _check_month(fiscal_year_end_month)
    fiscal_year = d.year + 1 if d.month > fiscal_year_end_month else d.year
    months_into_year = (d.month - fiscal_year_end_month - 1) % 12
    return fiscal_year, months_into_year // 3 + 1


def quarter_end(fiscal_year: int, quarter: int, fiscal_year_end_month: int = 12) -> datetime.date:
    """Return the last calendar day of the given fiscal quarter; never None."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be within 1..4, got {quarter!r}")
    _check_month(fiscal_year_end_month)
    # Count months from year 0; the quarter ends 3 * (4 - quarter) months before year end.
    index = fiscal_year * 12 + (fiscal_year_end_month - 1) - 3 * (4 - quarter)
    year, month = divmod(index, 12)
    month += 1
    return datetime.date(year, month, calendar.monthrange(year, month)[1])


def _is_business_day(d: datetime.date, holidays: set) -> bool:
    """Return True if d is Monday to Friday and not a holiday."""
    return d.weekday() < 5 and d not in holidays


def add_business_days(
    d: datetime.date, n: int, holidays: Iterable[datetime.date] = ()
) -> datetime.date:
    """Return the date n business days after d (before it if n < 0); never None."""
    holiday_set = set(holidays)
    step = datetime.timedelta(days=1 if n > 0 else -1)
    remaining = abs(n)
    current = d
    while remaining:
        current += step
        if _is_business_day(current, holiday_set):
            remaining -= 1
    return current


def business_days_between(
    start: datetime.date, end: datetime.date, holidays: Iterable[datetime.date] = ()
) -> int:
    """Return the number of business days in (start, end], negated if end is before start; never None."""
    if end < start:
        return -business_days_between(end, start, holidays)
    days = (end - start).days
    full_weeks, remainder = divmod(days, 7)
    count = full_weeks * 5
    for i in range(1, remainder + 1):
        if (start + datetime.timedelta(days=i)).weekday() < 5:
            count += 1
    for h in set(holidays):
        if start < h <= end and h.weekday() < 5:
            count -= 1
    return count


def parse_iso_date(value: object) -> datetime.date | None:
    """Return value as a date (a datetime's date, a date as is, a stripped ISO string parsed); None otherwise."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


_HORIZON_PATTERN = re.compile(r"[1-9][0-9]*[dwmy]")
_HORIZON_UNITS = ("d", "w", "m", "y")
# int() refuses digit strings longer than about 4300 characters, so long counts are converted in chunks.
_DIGIT_CHUNK = 1000
_OUT_OF_RANGE = "the result is outside the dates Python can represent (datetime.date.min to datetime.date.max)"


def _check_int(value: object, name: str) -> None:
    """Raise ValueError unless value is an int that is not a bool."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int (not a bool), got {value!r}")


def add_months(d: datetime.date, months: int) -> datetime.date:
    """Return d moved by months calendar months, clipped to the end of a shorter month; never None (bad input raises ValueError)."""
    _check_int(months, "months")
    year, month_index = divmod(d.year * 12 + (d.month - 1) + months, 12)
    if not datetime.MINYEAR <= year <= datetime.MAXYEAR:
        raise ValueError(_OUT_OF_RANGE)
    month = month_index + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def parse_horizon(text: str) -> tuple[int, str]:
    """Return (count, unit) for a horizon such as "3m"; never None (bad input raises ValueError)."""
    if not isinstance(text, str):
        raise ValueError(f"horizon must be a str, got {type(text).__name__}")
    stripped = text.strip()
    if _HORIZON_PATTERN.fullmatch(stripped) is None:
        raise ValueError(
            f"horizon must be a count with no leading 0 followed by d, w, m or y (such as '3m'), got {text!r}"
        )
    digits = stripped[:-1]
    count = 0
    for start in range(0, len(digits), _DIGIT_CHUNK):
        chunk = digits[start : start + _DIGIT_CHUNK]
        count = count * 10 ** len(chunk) + int(chunk)
    return count, stripped[-1]


def _add_days(d: datetime.date, days: int) -> datetime.date:
    """Return d plus days days; raise ValueError if that leaves the representable dates."""
    if not 1 <= d.toordinal() + days <= datetime.date.max.toordinal():
        raise ValueError(_OUT_OF_RANGE)
    return d + datetime.timedelta(days=days)


def shift_date(d: datetime.date, n: int, unit: str) -> datetime.date:
    """Return d moved by n days ("d"), weeks ("w"), months ("m") or years ("y"); never None (bad input raises ValueError)."""
    _check_int(n, "n")
    if not isinstance(unit, str) or unit not in _HORIZON_UNITS:
        raise ValueError(f"unit must be one of 'd', 'w', 'm', 'y', got {unit!r}")
    if unit == "d":
        return _add_days(d, n)
    if unit == "w":
        return _add_days(d, 7 * n)
    if unit == "m":
        return add_months(d, n)
    return add_months(d, 12 * n)


def horizon_end(d: datetime.date, horizon: str) -> datetime.date:
    """Return the date on which a horizon such as "1m" counted from d ends; never None (bad input raises ValueError)."""
    return shift_date(d, *parse_horizon(horizon))


def last_on_or_before(
    observations: Sequence[tuple[datetime.date, object]], day: datetime.date
) -> tuple[datetime.date, object] | None:
    """Return (date, value) for the latest date on or before day, the last such pair on a tie; None if no pair qualifies."""
    best: tuple[datetime.date, object] | None = None
    for when, value in observations:
        # >= rather than >: of several pairs with the same date, the later one wins.
        if when <= day and (best is None or when >= best[0]):
            best = (when, value)
    return best


def first_on_or_after(
    observations: Sequence[tuple[datetime.date, object]], day: datetime.date
) -> tuple[datetime.date, object] | None:
    """Return (date, value) for the earliest date on or after day, the last such pair on a tie; None if no pair qualifies."""
    best: tuple[datetime.date, object] | None = None
    for when, value in observations:
        # <= rather than <: of several pairs with the same date, the later one wins.
        if when >= day and (best is None or when <= best[0]):
            best = (when, value)
    return best
