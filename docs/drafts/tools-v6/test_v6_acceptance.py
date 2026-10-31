"""Acceptance tests for SPEC.md "v6: horizons and level changes".

add_months, parse_horizon, shift_date, horizon_end, last_on_or_before,
first_on_or_after (tools.dates); level_change, direction (tools.finance).
"""

import calendar
import random
from datetime import date, timedelta

import pytest

from tools.dates import (add_months, first_on_or_after, horizon_end, last_on_or_before,
                         parse_horizon, shift_date)
from tools.finance import change_bps, direction, level_change

D = date


# ---- add_months --------------------------------------------------------------

@pytest.mark.parametrize("d, months, expected", [
    (D(2026, 1, 31), 1, D(2026, 2, 28)),
    (D(2028, 1, 31), 1, D(2028, 2, 29)),
    (D(2026, 11, 30), 3, D(2027, 2, 28)),
    (D(2026, 3, 31), -1, D(2026, 2, 28)),
    (D(2026, 1, 15), -13, D(2024, 12, 15)),
    (D(2026, 5, 15), 0, D(2026, 5, 15)),
    (D(2026, 5, 31), 0, D(2026, 5, 31)),
    (D(2026, 1, 31), 2, D(2026, 3, 31)),
    (D(2026, 8, 31), 1, D(2026, 9, 30)),
    (D(2026, 12, 15), 1, D(2027, 1, 15)),
    (D(2027, 1, 15), -1, D(2026, 12, 15)),
    (D(2024, 2, 29), 12, D(2025, 2, 28)),
    (D(2024, 2, 29), 48, D(2028, 2, 29)),
    (D(2024, 2, 29), -12, D(2023, 2, 28)),
    (D(2000, 1, 31), 1, D(2000, 2, 29)),
    (D(1900, 1, 31), 1, D(1900, 2, 28)),
    (D(2026, 7, 31), 3, D(2026, 10, 31)),
    (D(2026, 7, 31), 2, D(2026, 9, 30)),
    (D(2026, 6, 30), 1, D(2026, 7, 30)),
    (D(2026, 10, 30), 1, D(2026, 11, 30)),
    (D(2026, 1, 1), 1200, D(2126, 1, 1)),
    (D(9999, 11, 30), 1, D(9999, 12, 30)),
    (D(1, 2, 28), -1, D(1, 1, 28)),
])
def test_add_months_values(d, months, expected):
    assert add_months(d, months) == expected


def test_add_months_agrees_with_counting_months():
    rng = random.Random(6)
    for _ in range(3000):
        d = D(1900, 1, 1) + timedelta(days=rng.randrange(0, 365 * 200))
        months = rng.randint(-600, 600)
        got = add_months(d, months)
        index = d.year * 12 + d.month - 1 + months
        assert (got.year, got.month) == (index // 12, index % 12 + 1)
        assert got.day == min(d.day, calendar.monthrange(got.year, got.month)[1])


@pytest.mark.parametrize("months", [True, False, 1.0, 0.5, None, "1", [1]])
def test_add_months_rejects_a_count_that_is_not_an_int(months):
    with pytest.raises(ValueError):
        add_months(D(2026, 1, 15), months)


@pytest.mark.parametrize("d, months", [
    (D(9999, 12, 1), 1),
    (D(1, 1, 31), -1),
    (D(2026, 1, 1), 10 ** 6),
    (D(2026, 1, 1), -(10 ** 6)),
    (D(2026, 1, 1), 10 ** 30),
])
def test_add_months_out_of_range_raises_valueerror(d, months):
    with pytest.raises(ValueError):
        add_months(d, months)


# ---- parse_horizon -----------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("1w", (1, "w")),
    ("3m", (3, "m")),
    ("1d", (1, "d")),
    ("2y", (2, "y")),
    ("10d", (10, "d")),
    (" 12m ", (12, "m")),
    ("\t1w\n", (1, "w")),
    ("52w", (52, "w")),
    ("100y", (100, "y")),
])
def test_parse_horizon_accepts(text, expected):
    got = parse_horizon(text)
    assert got == expected
    assert type(got) is tuple and type(got[0]) is int and type(got[1]) is str


@pytest.mark.parametrize("text", [
    "0d", "00w", "01w", "1W", "1M", "1 w", "-1m", "+1m", "1.5m", "1,000d", "m", "1",
    "1mo", "", "   ", "w1", "1m1", "1q", "1h", "1ww", "one m",
    "\u0661w",  # an Arabic-Indic one: a digit to str.isdigit, not ASCII
    "\uff11w",  # a fullwidth one
    "\u00b2w",  # a superscript two
])
def test_parse_horizon_rejects(text):
    with pytest.raises(ValueError):
        parse_horizon(text)


@pytest.mark.parametrize("value", [None, 1, 1.0, b"1w", ["1w"], ("1", "w"), True])
def test_parse_horizon_rejects_what_is_not_a_str(value):
    with pytest.raises(ValueError):
        parse_horizon(value)


# ---- shift_date ----------------------------------------------------------------

@pytest.mark.parametrize("d, n, unit, expected", [
    (D(2026, 7, 3), 1, "d", D(2026, 7, 4)),
    (D(2026, 7, 3), -3, "d", D(2026, 6, 30)),
    (D(2026, 7, 3), 0, "d", D(2026, 7, 3)),
    (D(2026, 7, 3), 1, "w", D(2026, 7, 10)),
    (D(2026, 7, 3), -2, "w", D(2026, 6, 19)),
    (D(2026, 7, 3), 0, "w", D(2026, 7, 3)),
    (D(2026, 12, 28), 1, "w", D(2027, 1, 4)),
    (D(2026, 1, 31), 1, "m", D(2026, 2, 28)),
    (D(2026, 3, 31), -1, "m", D(2026, 2, 28)),
    (D(2026, 7, 3), 0, "m", D(2026, 7, 3)),
    (D(2028, 2, 29), 1, "y", D(2029, 2, 28)),
    (D(2028, 2, 29), 4, "y", D(2032, 2, 29)),
    (D(2028, 2, 29), -1, "y", D(2027, 2, 28)),
    (D(2026, 7, 3), 0, "y", D(2026, 7, 3)),
])
def test_shift_date_values(d, n, unit, expected):
    assert shift_date(d, n, unit) == expected


@pytest.mark.parametrize("unit", ["D", "W", "q", "", "days", "1d", None, 1, "m "])
def test_shift_date_rejects_an_unknown_unit(unit):
    with pytest.raises(ValueError):
        shift_date(D(2026, 7, 3), 1, unit)


@pytest.mark.parametrize("n", [True, False, 1.0, None, "1"])
def test_shift_date_rejects_a_count_that_is_not_an_int(n):
    for unit in ("d", "w", "m", "y"):
        with pytest.raises(ValueError):
            shift_date(D(2026, 7, 3), n, unit)


@pytest.mark.parametrize("d, n, unit", [
    (date.max, 1, "d"),
    (date.min, -1, "d"),
    (date.max, 1, "w"),
    (date.min, -1, "w"),
    (D(9999, 12, 1), 1, "m"),
    (D(9999, 1, 1), 1, "y"),
    (D(1, 6, 1), -1, "y"),
    (D(2026, 7, 3), 10 ** 12, "d"),
    (D(2026, 7, 3), -(10 ** 12), "w"),
    (D(2026, 7, 3), 10 ** 30, "y"),
])
def test_shift_date_out_of_range_raises_valueerror(d, n, unit):
    with pytest.raises(ValueError):
        shift_date(d, n, unit)


def test_shift_date_reaches_the_ends_of_the_calendar():
    assert shift_date(date.max - timedelta(days=7), 1, "w") == date.max
    assert shift_date(date.min + timedelta(days=1), -1, "d") == date.min


# ---- horizon_end ---------------------------------------------------------------

@pytest.mark.parametrize("horizon, expected", [
    ("1w", D(2026, 7, 10)),
    ("10d", D(2026, 7, 13)),
    ("1m", D(2026, 8, 3)),
    ("3m", D(2026, 10, 3)),
    ("1y", D(2027, 7, 3)),
    (" 2w ", D(2026, 7, 17)),
])
def test_horizon_end_from_friday_2026_07_03(horizon, expected):
    assert horizon_end(D(2026, 7, 3), horizon) == expected


FRIDAYS_2026 = [D(2026, 7, 3) + timedelta(weeks=i) for i in range(18)]  # 07-03 .. 10-30
ONE_MONTH = ["08-03", "08-10", "08-17", "08-24", "08-31", "09-07", "09-14", "09-21",
             "09-28", "10-04", "10-11", "10-18", "10-25", "11-02", "11-09", "11-16",
             "11-23", "11-30"]
THREE_MONTHS = ["2026-10-03", "2026-10-10", "2026-10-17", "2026-10-24", "2026-10-31",
                "2026-11-07", "2026-11-14", "2026-11-21", "2026-11-28", "2026-12-04",
                "2026-12-11", "2026-12-18", "2026-12-25", "2027-01-02", "2027-01-09",
                "2027-01-16", "2027-01-23", "2027-01-30"]


def test_horizon_end_for_every_friday_from_july_to_october_2026():
    assert FRIDAYS_2026[-1] == D(2026, 10, 30)
    assert all(friday.weekday() == 4 for friday in FRIDAYS_2026)
    for friday, one, three in zip(FRIDAYS_2026, ONE_MONTH, THREE_MONTHS):
        assert horizon_end(friday, "1w") == friday + timedelta(days=7)
        assert horizon_end(friday, "1m") == D.fromisoformat("2026-" + one)
        assert horizon_end(friday, "3m") == D.fromisoformat(three)


def test_horizon_end_clips_to_the_end_of_a_short_month():
    assert horizon_end(D(2026, 1, 30), "1m") == D(2026, 2, 28)
    assert horizon_end(D(2026, 8, 31), "1m") == D(2026, 9, 30)
    assert horizon_end(D(2026, 11, 30), "3m") == D(2027, 2, 28)


@pytest.mark.parametrize("horizon", ["1W", "0m", "", "1", None, 1])
def test_horizon_end_rejects_a_bad_horizon(horizon):
    with pytest.raises(ValueError):
        horizon_end(D(2026, 7, 3), horizon)


# ---- last_on_or_before / first_on_or_after ----------------------------------------

OBS = [
    (D(2026, 7, 9), 4.30),
    (D(2026, 7, 2), 4.21),
    (D(2026, 7, 13), 4.35),
    (D(2026, 7, 10), 4.33),
    (D(2026, 7, 6), 4.25),
]


@pytest.mark.parametrize("day, expected", [
    (D(2026, 7, 10), (D(2026, 7, 10), 4.33)),  # on the day
    (D(2026, 7, 11), (D(2026, 7, 10), 4.33)),  # a Saturday: the Friday before
    (D(2026, 7, 12), (D(2026, 7, 10), 4.33)),
    (D(2026, 7, 3), (D(2026, 7, 2), 4.21)),    # a market holiday: the Thursday
    (D(2026, 7, 2), (D(2026, 7, 2), 4.21)),
    (D(2026, 12, 31), (D(2026, 7, 13), 4.35)),
    (D(2026, 7, 1), None),
])
def test_last_on_or_before(day, expected):
    assert last_on_or_before(OBS, day) == expected


@pytest.mark.parametrize("day, expected", [
    (D(2026, 7, 10), (D(2026, 7, 10), 4.33)),
    (D(2026, 7, 11), (D(2026, 7, 13), 4.35)),  # a Saturday: the Monday after
    (D(2026, 7, 3), (D(2026, 7, 6), 4.25)),
    (D(2026, 1, 1), (D(2026, 7, 2), 4.21)),
    (D(2026, 7, 13), (D(2026, 7, 13), 4.35)),
    (D(2026, 7, 14), None),
])
def test_first_on_or_after(day, expected):
    assert first_on_or_after(OBS, day) == expected


def test_nothing_to_choose_from():
    assert last_on_or_before([], D(2026, 7, 10)) is None
    assert first_on_or_after([], D(2026, 7, 10)) is None
    assert last_on_or_before((), D(2026, 7, 10)) is None


def test_the_last_of_several_pairs_on_one_date_wins():
    obs = [(D(2026, 7, 10), 1.0), (D(2026, 7, 9), 0.5), (D(2026, 7, 10), 2.0),
           (D(2026, 7, 13), 3.0), (D(2026, 7, 10), 2.5), (D(2026, 7, 13), 3.5)]
    assert last_on_or_before(obs, D(2026, 7, 11)) == (D(2026, 7, 10), 2.5)
    assert first_on_or_after(obs, D(2026, 7, 10)) == (D(2026, 7, 10), 2.5)
    assert first_on_or_after(obs, D(2026, 7, 11)) == (D(2026, 7, 13), 3.5)
    assert last_on_or_before(obs, D(2026, 7, 20)) == (D(2026, 7, 13), 3.5)


def test_values_come_back_as_they_are():
    marker = {"close": 1}
    obs = [(D(2026, 7, 9), None), (D(2026, 7, 10), "4.33"), (D(2026, 7, 13), marker)]
    assert last_on_or_before(obs, D(2026, 7, 9)) == (D(2026, 7, 9), None)
    assert last_on_or_before(obs, D(2026, 7, 10)) == (D(2026, 7, 10), "4.33")
    got = first_on_or_after(obs, D(2026, 7, 11))
    assert got[1] is marker


def test_the_result_is_a_tuple_and_the_input_is_left_alone():
    obs = [[D(2026, 7, 10), 4.33], [D(2026, 7, 2), 4.21]]
    before = [list(pair) for pair in obs]
    got = last_on_or_before(obs, D(2026, 7, 31))
    assert got == (D(2026, 7, 10), 4.33) and type(got) is tuple
    got = first_on_or_after(obs, D(2026, 7, 1))
    assert got == (D(2026, 7, 2), 4.21) and type(got) is tuple
    assert obs == before


def test_agrees_with_a_sorted_scan():
    rng = random.Random(60)
    for _ in range(500):
        obs = [(D(2026, 1, 1) + timedelta(days=rng.randrange(0, 60)), rng.random())
               for _ in range(rng.randrange(0, 12))]
        day = D(2026, 1, 1) + timedelta(days=rng.randrange(-5, 65))
        before = [(i, p) for i, p in enumerate(obs) if p[0] <= day]
        after = [(i, p) for i, p in enumerate(obs) if p[0] >= day]
        want_last = max(before, key=lambda ip: (ip[1][0], ip[0]))[1] if before else None
        want_first = min(after, key=lambda ip: (ip[1][0], -ip[0]))[1] if after else None
        assert last_on_or_before(obs, day) == want_last
        assert first_on_or_after(obs, day) == want_first


# ---- level_change -----------------------------------------------------------------

@pytest.mark.parametrize("start, end, kind, expected", [
    (4.21, 4.33, "bp", 12.0),
    (4.33, 4.21, "bp", -12.0),
    (4.21, 4.21, "bp", 0.0),
    (-0.5, 0.25, "bp", 75.0),     # a curve that un-inverts
    (0.0, -0.1, "bp", -10.0),
    (3, 4, "bp", 100),
    (100.0, 110.0, "return", 0.1),
    (110.0, 99.0, "return", -0.1),
    (2400.0, 2400.0, "return", 0.0),
    (15.2, 18.7, "points", 3.5),
    (18.7, 15.2, "points", -3.5),
    (20, 20, "points", 0),
])
def test_level_change_values(start, end, kind, expected):
    assert level_change(start, end, kind) == pytest.approx(expected, rel=1e-12, abs=1e-9)


def test_basis_points_here_are_hundredths_of_a_percent():
    assert level_change(4.21, 4.33, "bp") == pytest.approx(change_bps(0.0433, 0.0421))


@pytest.mark.parametrize("start, end", [(0, 1.0), (0.0, 1.0), (-5.0, 1.0), (-1, -2)])
def test_a_return_needs_a_positive_start(start, end):
    assert level_change(start, end, "return") is None


@pytest.mark.parametrize("start, end", [
    (None, 1.0), (1.0, None), (None, None), (True, 1.0), (1.0, False),
    (float("nan"), 1.0), (1.0, float("nan")), (float("inf"), 1.0), (1.0, -float("inf")),
    ("4.21", 4.33), (4.21, "4.33"), ([1.0], 1.0),
])
def test_level_change_of_something_that_is_not_a_number_is_none(start, end):
    for kind in ("bp", "return", "points"):
        assert level_change(start, end, kind) is None


@pytest.mark.parametrize("start, end, kind", [
    (-1e308, 1e308, "bp"),
    (-1e308, 1e308, "points"),
    (1e-300, 1e300, "return"),
    (1, 10 ** 400, "return"),
])
def test_level_change_that_overflows_is_none(start, end, kind):
    assert level_change(start, end, kind) is None


def test_level_change_of_large_ints_is_exact():
    assert level_change(10 ** 20, 10 ** 20 + 1, "points") == 1
    assert level_change(10 ** 20, 10 ** 20 + 1, "bp") == 100


@pytest.mark.parametrize("kind", ["bps", "BP", "Return", "pct", "", None, 1, "points "])
def test_level_change_rejects_an_unknown_kind_whatever_the_values(kind):
    for start, end in ((4.21, 4.33), (None, None), (0, 1)):
        with pytest.raises(ValueError):
            level_change(start, end, kind)


# ---- direction ----------------------------------------------------------------------

@pytest.mark.parametrize("change, expected", [
    (0.1, "up"), (12.0, "up"), (1, "up"), (1e-12, "up"),
    (-0.1, "down"), (-12.0, "down"), (-1, "down"), (-1e-12, "down"),
    (0, "flat"), (0.0, "flat"), (-0.0, "flat"),
])
def test_direction_with_no_band(change, expected):
    assert direction(change) == expected


@pytest.mark.parametrize("change, expected", [
    (0.5, "flat"), (-0.5, "flat"), (0.0, "flat"), (0.49, "flat"), (-0.49, "flat"),
    (0.5000001, "up"), (-0.51, "down"), (3, "up"), (-3, "down"),
])
def test_direction_with_a_band(change, expected):
    assert direction(change, 0.5) == expected
    assert direction(change, dead_band=0.5) == expected


def test_direction_accepts_an_int_band():
    assert direction(1, 1) == "flat"
    assert direction(2, 1) == "up"
    assert direction(-1.5, 1) == "down"


@pytest.mark.parametrize("change", [None, float("nan"), float("inf"), -float("inf"),
                                    True, False, "1", [1]])
def test_direction_of_something_that_is_not_a_number_is_none(change):
    assert direction(change) is None
    assert direction(change, 0.5) is None


@pytest.mark.parametrize("band", [-0.1, -1, None, float("nan"), float("inf"), True, "0", [0]])
def test_direction_rejects_a_bad_band_whatever_the_change(band):
    for change in (1.0, 0.0, None):
        with pytest.raises(ValueError):
            direction(change, band)


# ---- together: one view, scored -------------------------------------------------------

def test_a_one_month_view_on_the_ten_year_yield():
    # The 10-year yield around a Friday as-of date, as a series file holds it
    # (any order). The horizon ends on Monday 2026-08-03.
    obs = [(D(2026, 8, 4), 4.18), (D(2026, 7, 2), 4.21), (D(2026, 7, 31), 4.12),
           (D(2026, 8, 3), 4.15), (D(2026, 7, 6), 4.25)]
    as_of = D(2026, 7, 3)  # a market holiday: the Thursday's close is the start
    start = last_on_or_before(obs, as_of)
    end_day = horizon_end(as_of, "1m")
    end = last_on_or_before(obs, end_day)
    assert start == (D(2026, 7, 2), 4.21)
    assert end_day == D(2026, 8, 3)
    assert first_on_or_after(obs, end_day) is not None  # the view can be scored
    assert end == (D(2026, 8, 3), 4.15)
    change = level_change(start[1], end[1], "bp")
    assert change == pytest.approx(-6.0)
    assert direction(change) == "down"
    assert direction(change, dead_band=10) == "flat"
