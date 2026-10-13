"""Fiscal calendar and business-day arithmetic."""

import calendar
import datetime
from collections.abc import Iterable


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
