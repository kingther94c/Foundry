import collections
import datetime

import pytest

from tools import dates as d

D = datetime.date
FRI = D(2026, 7, 3)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (D(2026, 1, 31), 1, D(2026, 2, 28)),
        (D(2028, 1, 31), 1, D(2028, 2, 29)),
        (D(2026, 11, 30), 3, D(2027, 2, 28)),
        (D(2026, 3, 31), -1, D(2026, 2, 28)),
        (D(2026, 1, 15), -13, D(2024, 12, 15)),
        (D(2026, 5, 15), 0, D(2026, 5, 15)),
        (D(2026, 12, 31), 1, D(2027, 1, 31)),
        (D(2026, 1, 1), -1, D(2025, 12, 1)),
        (D(2026, 8, 31), 1, D(2026, 9, 30)),
        (D(2026, 2, 28), 1, D(2026, 3, 28)),
        (D(2028, 2, 29), 12, D(2029, 2, 28)),
        (D(2028, 2, 29), 48, D(2032, 2, 29)),
        (D(2026, 1, 31), -24, D(2024, 1, 31)),
    ],
)
def test_add_months(start, months, expected):
    result = d.add_months(start, months)
    assert result == expected
    assert type(result) is D


def test_add_months_at_the_edges_of_representable_dates():
    assert d.add_months(D.max, 0) == D.max
    assert d.add_months(D.min, 0) == D.min
    assert d.add_months(D(9999, 11, 30), 1) == D(9999, 12, 30)
    assert d.add_months(D(1, 2, 28), -1) == D(1, 1, 28)


def test_add_months_out_of_range_raises():
    cases = [
        (D(9999, 12, 1), 1),
        (D(1, 1, 31), -1),
        (D(2026, 1, 1), 12 * 8000),
        (D(2026, 1, 1), 10**400),
        (D(2026, 1, 1), -(10**400)),
    ]
    for start, months in cases:
        with pytest.raises(ValueError):
            d.add_months(start, months)


@pytest.mark.parametrize("months", [True, False, 1.0, 1.5, "1", None, [1]])
def test_add_months_rejects_non_int_months(months):
    with pytest.raises(ValueError):
        d.add_months(D(2026, 1, 15), months)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1w", (1, "w")),
        ("3m", (3, "m")),
        (" 12m ", (12, "m")),
        ("10d", (10, "d")),
        ("1y", (1, "y")),
        ("\t2w\n", (2, "w")),
        ("100d", (100, "d")),
        ("90d", (90, "d")),
    ],
)
def test_parse_horizon(text, expected):
    count, unit = d.parse_horizon(text)
    assert (count, unit) == expected
    assert type(count) is int
    assert type(unit) is str


def test_parse_horizon_long_count_and_unicode_whitespace():
    assert d.parse_horizon(chr(0xA0) + "6m" + chr(0x3000)) == (6, "m")
    count, unit = d.parse_horizon("1" + "0" * 5000 + "d")
    assert count == 10**5000
    assert unit == "d"


@pytest.mark.parametrize(
    "text",
    [
        "0d",
        "01w",
        "1W",
        "1 w",
        "-1m",
        "+1m",
        "1.5m",
        "m",
        "1",
        "1mo",
        "",
        "   ",
        "0",
        "00m",
        "1M",
        "1D",
        "1Y",
        "1dw",
        "w1",
        "1_0d",
        "1\nm",
        "1m.",
        chr(0x661) + "m",
        chr(0xFF11) + "m",
    ],
)
def test_parse_horizon_rejects_bad_text(text):
    with pytest.raises(ValueError):
        d.parse_horizon(text)


@pytest.mark.parametrize("value", [None, 1, 1.0, True, b"1m", ["1m"], (1, "m")])
def test_parse_horizon_rejects_non_strings(value):
    with pytest.raises(ValueError):
        d.parse_horizon(value)


@pytest.mark.parametrize(
    ("n", "unit", "expected"),
    [
        (10, "d", D(2026, 7, 13)),
        (-3, "d", D(2026, 6, 30)),
        (0, "d", FRI),
        (2, "w", D(2026, 7, 17)),
        (-1, "w", D(2026, 6, 26)),
        (0, "w", FRI),
        (1, "m", D(2026, 8, 3)),
        (-7, "m", D(2025, 12, 3)),
        (0, "m", FRI),
        (1, "y", D(2027, 7, 3)),
        (-2, "y", D(2024, 7, 3)),
        (0, "y", FRI),
    ],
)
def test_shift_date(n, unit, expected):
    assert d.shift_date(FRI, n, unit) == expected


def test_shift_date_months_and_years_clip_to_month_end():
    assert d.shift_date(D(2028, 2, 29), 1, "y") == D(2029, 2, 28)
    assert d.shift_date(D(2028, 2, 29), -1, "y") == D(2027, 2, 28)
    assert d.shift_date(D(2028, 2, 29), 4, "y") == D(2032, 2, 29)
    assert d.shift_date(D(2026, 1, 31), 1, "m") == D(2026, 2, 28)
    assert d.shift_date(D(2026, 5, 31), -3, "m") == D(2026, 2, 28)


@pytest.mark.parametrize("n", [True, False, 1.0, 1.5, "1", None])
def test_shift_date_rejects_non_int_n(n):
    for unit in ("d", "w", "m", "y"):
        with pytest.raises(ValueError):
            d.shift_date(FRI, n, unit)


@pytest.mark.parametrize(
    "unit", ["D", "W", "M", "Y", "day", "days", "mo", "", " d", "d ", None, 1, ["d"]]
)
def test_shift_date_rejects_unknown_unit(unit):
    with pytest.raises(ValueError):
        d.shift_date(FRI, 1, unit)


def test_shift_date_out_of_range_raises():
    assert d.shift_date(D.max, 0, "d") == D.max
    assert d.shift_date(D(9999, 12, 24), 1, "w") == D.max
    assert d.shift_date(D.min, 0, "w") == D.min
    cases = [
        (D.max, 1, "d"),
        (D.min, -1, "d"),
        (D(9999, 12, 25), 1, "w"),
        (D(1, 1, 7), -1, "w"),
        (D(9999, 12, 1), 1, "m"),
        (D(9999, 1, 1), 1, "y"),
        (D(1, 12, 31), -1, "y"),
        (FRI, 10**9, "d"),
        (FRI, 10**400, "d"),
        (FRI, -(10**400), "w"),
        (FRI, 10**400, "m"),
        (FRI, 10**400, "y"),
    ]
    for start, n, unit in cases:
        with pytest.raises(ValueError):
            d.shift_date(start, n, unit)


def test_horizon_end_spec_examples():
    assert FRI.weekday() == 4
    assert d.horizon_end(FRI, "1w") == D(2026, 7, 10)
    assert d.horizon_end(FRI, "10d") == D(2026, 7, 13)
    assert d.horizon_end(FRI, "1m") == D(2026, 8, 3)
    assert d.horizon_end(FRI, "1m").weekday() == 0
    assert d.horizon_end(FRI, "3m") == D(2026, 10, 3)
    assert d.horizon_end(FRI, "3m").weekday() == 5
    assert d.horizon_end(FRI, "1y") == D(2027, 7, 3)
    assert d.horizon_end(FRI, " 12m ") == D(2027, 7, 3)
    assert d.horizon_end(D(2026, 1, 31), "1m") == D(2026, 2, 28)
    assert d.horizon_end(D(2028, 2, 29), "1y") == D(2029, 2, 28)


@pytest.mark.parametrize("horizon", ["0d", "1W", "-1m", "1 m", "", None, 7])
def test_horizon_end_rejects_bad_horizons(horizon):
    with pytest.raises(ValueError):
        d.horizon_end(FRI, horizon)


def test_horizon_end_out_of_range_raises():
    with pytest.raises(ValueError):
        d.horizon_end(D(9999, 6, 1), "1y")
    with pytest.raises(ValueError):
        d.horizon_end(D(9999, 12, 31), "1d")


OBS = [
    (D(2026, 7, 6), 4.30),
    (D(2026, 7, 2), 4.21),
    (D(2026, 7, 10), 4.40),
    (D(2026, 7, 3), 4.25),
]


def test_last_on_or_before():
    assert d.last_on_or_before(OBS, D(2026, 7, 4)) == (D(2026, 7, 3), 4.25)
    assert d.last_on_or_before(OBS, D(2026, 7, 5)) == (D(2026, 7, 3), 4.25)
    assert d.last_on_or_before(OBS, D(2026, 7, 6)) == (D(2026, 7, 6), 4.30)
    assert d.last_on_or_before(OBS, D(2026, 7, 9)) == (D(2026, 7, 6), 4.30)
    assert d.last_on_or_before(OBS, D(2030, 1, 1)) == (D(2026, 7, 10), 4.40)
    assert d.last_on_or_before(OBS, D(2026, 7, 2)) == (D(2026, 7, 2), 4.21)
    assert d.last_on_or_before(OBS, D(2026, 7, 1)) is None
    assert d.last_on_or_before([], D(2026, 7, 1)) is None
    assert d.last_on_or_before((), D(2026, 7, 1)) is None


def test_first_on_or_after():
    assert d.first_on_or_after(OBS, D(2026, 7, 4)) == (D(2026, 7, 6), 4.30)
    assert d.first_on_or_after(OBS, D(2026, 7, 3)) == (D(2026, 7, 3), 4.25)
    assert d.first_on_or_after(OBS, D(2026, 7, 7)) == (D(2026, 7, 10), 4.40)
    assert d.first_on_or_after(OBS, D(2026, 7, 10)) == (D(2026, 7, 10), 4.40)
    assert d.first_on_or_after(OBS, D(2000, 1, 1)) == (D(2026, 7, 2), 4.21)
    assert d.first_on_or_after(OBS, D(2026, 7, 11)) is None
    assert d.first_on_or_after([], D(2026, 7, 11)) is None
    assert d.first_on_or_after((), D(2026, 7, 11)) is None


def test_duplicate_dates_the_last_pair_wins():
    obs = [
        (D(2026, 7, 3), "first"),
        (D(2026, 7, 1), "early"),
        (D(2026, 7, 6), "a"),
        (D(2026, 7, 3), "second"),
        (D(2026, 7, 8), "late"),
        (D(2026, 7, 6), "b"),
        (D(2026, 7, 3), "third"),
    ]
    assert d.last_on_or_before(obs, D(2026, 7, 5)) == (D(2026, 7, 3), "third")
    assert d.last_on_or_before(obs, D(2026, 7, 6)) == (D(2026, 7, 6), "b")
    assert d.last_on_or_before(obs, D(2026, 7, 1)) == (D(2026, 7, 1), "early")
    assert d.first_on_or_after(obs, D(2026, 7, 4)) == (D(2026, 7, 6), "b")
    assert d.first_on_or_after(obs, D(2026, 7, 2)) == (D(2026, 7, 3), "third")
    assert d.first_on_or_after(obs, D(2026, 7, 7)) == (D(2026, 7, 8), "late")


def test_values_are_returned_as_they_are_in_a_new_tuple():
    value = {"close": 4.25}
    pair = (D(2026, 7, 3), value)
    for fn in (d.last_on_or_before, d.first_on_or_after):
        result = fn([pair], D(2026, 7, 3))
        assert result == pair
        assert result is not pair
        assert result[1] is value
        assert type(result) is tuple
    assert d.last_on_or_before([(D(2026, 7, 3), None)], D(2026, 7, 4)) == (D(2026, 7, 3), None)
    assert d.first_on_or_after([(D(2026, 7, 3), None)], D(2026, 7, 2)) == (D(2026, 7, 3), None)


def test_other_kinds_of_pair_give_a_plain_tuple():
    Obs = collections.namedtuple("Obs", "day close")
    for obs in ([[D(2026, 7, 3), 4.25]], [Obs(D(2026, 7, 3), 4.25)]):
        for fn in (d.last_on_or_before, d.first_on_or_after):
            result = fn(obs, D(2026, 7, 3))
            assert result == (D(2026, 7, 3), 4.25)
            assert type(result) is tuple


def test_observations_are_left_as_they_were():
    obs = list(OBS)
    snapshot = list(obs)
    d.last_on_or_before(obs, D(2026, 7, 4))
    d.first_on_or_after(obs, D(2026, 7, 4))
    assert obs == snapshot
