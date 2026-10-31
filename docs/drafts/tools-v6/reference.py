"""A hand-written reference for SPEC.md "v6: horizons and level changes".

Written from the spec alone, for differential checks against what Foundry
builds (check.py). It is not part of the package and must never be copied
into the build workspace.
"""

import calendar
import datetime

from tools.returns import simple_return
from tools.stats import is_number

UNITS = ("d", "w", "m", "y")
KINDS = ("bp", "return", "points")


def _whole(n, name):
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("%s must be an int, got %r" % (name, n))


def add_months(d, months):
    _whole(months, "months")
    index = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(index, 12)
    if not datetime.MINYEAR <= year <= datetime.MAXYEAR:
        raise ValueError("%s plus %d months is outside the dates Python can represent"
                         % (d, months))
    last = calendar.monthrange(year, month0 + 1)[1]
    return datetime.date(year, month0 + 1, min(d.day, last))


def parse_horizon(text):
    if not isinstance(text, str):
        raise ValueError("a horizon is a str such as '1m', got %r" % (text,))
    s = text.strip()
    if len(s) < 2 or s[-1] not in UNITS:
        raise ValueError("not a horizon: %r" % (text,))
    digits = s[:-1]
    if not all(c in "0123456789" for c in digits) or digits[0] == "0":
        raise ValueError("not a horizon: %r" % (text,))
    return int(digits), s[-1]


def shift_date(d, n, unit):
    _whole(n, "n")
    if unit not in UNITS or not isinstance(unit, str):
        raise ValueError("unit must be one of d, w, m, y, got %r" % (unit,))
    if unit == "m":
        return add_months(d, n)
    if unit == "y":
        return add_months(d, 12 * n)
    days = n if unit == "d" else 7 * n
    try:
        return d + datetime.timedelta(days=days)
    except OverflowError:
        raise ValueError("%s plus %d days is outside the dates Python can represent"
                         % (d, days)) from None


def horizon_end(d, horizon):
    n, unit = parse_horizon(horizon)
    return shift_date(d, n, unit)


def _pick(observations, day, qualifies, better):
    best = None
    for pair in observations:
        when, value = pair[0], pair[1]
        if not qualifies(when, day):
            continue
        if best is None or better(when, best[0]) or when == best[0]:
            best = (when, value)
    return best


def last_on_or_before(observations, day):
    return _pick(observations, day, lambda when, d: when <= d, lambda a, b: a > b)


def first_on_or_after(observations, day):
    return _pick(observations, day, lambda when, d: when >= d, lambda a, b: a < b)


def level_change(start, end, kind):
    if kind not in KINDS or not isinstance(kind, str):
        raise ValueError("kind must be bp, return or points, got %r" % (kind,))
    if not is_number(start) or not is_number(end):
        return None
    try:
        if kind == "bp":
            result = (end - start) * 100
        elif kind == "return":
            result = simple_return(start, end)
        else:
            result = end - start
    except OverflowError:
        return None
    if result is None:
        return None
    if isinstance(result, float) and not is_number(result):
        return None
    return result


def direction(change, dead_band=0.0):
    if not is_number(dead_band) or dead_band < 0:
        raise ValueError("dead_band must be a number >= 0, got %r" % (dead_band,))
    if not is_number(change):
        return None
    if change > dead_band:
        return "up"
    if change < -dead_band:
        return "down"
    return "flat"
