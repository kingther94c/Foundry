"""Acceptance tests for tools v2, written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "v2: values read from files" section of SPEC.md.
"""

import datetime

import pytest

from tools.dates import parse_iso_date
from tools.stats import is_number


@pytest.mark.parametrize("value, expected", [
    ("2026-07-14", datetime.date(2026, 7, 14)),
    ("  2026-07-14\n", datetime.date(2026, 7, 14)),
    ("\t2026-02-28 ", datetime.date(2026, 2, 28)),
    (datetime.datetime(2026, 7, 14, 9, 30), datetime.date(2026, 7, 14)),
    (datetime.date(2026, 7, 14), datetime.date(2026, 7, 14)),
    ("July 14, 2026", None), ("", None), ("   ", None), ("2026-13-01", None), ("2026-02-30", None),
    (None, None), (20260714, None), (2026.0, None), (True, None), (["2026-07-14"], None),
    ({"date": "2026-07-14"}, None),
])
def test_parse_iso_date(value, expected):
    got = parse_iso_date(value)
    assert got == expected
    if expected is not None:
        assert type(got) is datetime.date


def test_parse_iso_date_returns_the_same_date():
    d = datetime.date(2026, 7, 14)
    assert parse_iso_date(d) is d


@pytest.mark.parametrize("value, expected", [
    (0, True), (1, True), (-7, True), (10 ** 400, True), (-(10 ** 400), True),
    (0.0, True), (-2.5, True), (1e308, True), (5e-324, True),
    (True, False), (False, False), (float("nan"), False), (float("inf"), False), (float("-inf"), False),
    (None, False), ("1", False), ("nan", False), ([1], False), ({"x": 1}, False), (1j, False),
])
def test_is_number(value, expected):
    assert is_number(value) is expected
