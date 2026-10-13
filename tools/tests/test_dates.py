import datetime

import pytest

from tools import dates as d

D = datetime.date


def test_fiscal_quarter():
    assert d.fiscal_quarter(D(2026, 7, 15), 6) == (2027, 1)
    assert d.fiscal_quarter(D(2026, 6, 30), 6) == (2026, 4)
    assert d.fiscal_quarter(D(2026, 6, 27), 9) == (2026, 3)
    assert d.fiscal_quarter(D(2026, 5, 1)) == (2026, 2)
    assert d.fiscal_quarter(D(2026, 12, 31)) == (2026, 4)
    with pytest.raises(ValueError):
        d.fiscal_quarter(D(2026, 5, 1), 13)


def test_quarter_end():
    assert d.quarter_end(2026, 4, 6) == D(2026, 6, 30)
    assert d.quarter_end(2027, 1, 6) == D(2026, 9, 30)
    assert d.quarter_end(2026, 3, 9) == D(2026, 6, 30)
    assert d.quarter_end(2024, 1, 11) == D(2024, 2, 29)
    assert d.quarter_end(2026, 1) == D(2026, 3, 31)
    with pytest.raises(ValueError):
        d.quarter_end(2026, 5)
    with pytest.raises(ValueError):
        d.quarter_end(2026, 1, 0)


def test_quarter_end_round_trips_fiscal_quarter():
    for month in range(1, 13):
        for q in range(1, 5):
            end = d.quarter_end(2026, q, month)
            assert d.fiscal_quarter(end, month) == (2026, q)


def test_add_business_days():
    fri = D(2026, 1, 2)
    assert d.add_business_days(fri, 1) == D(2026, 1, 5)
    assert d.add_business_days(D(2026, 1, 5), -1) == fri
    assert d.add_business_days(D(2026, 1, 3), 0) == D(2026, 1, 3)
    assert d.add_business_days(fri, 1, holidays=[D(2026, 1, 5)]) == D(2026, 1, 6)
    assert d.add_business_days(fri, 5) == D(2026, 1, 9)


def test_business_days_between():
    fri, mon = D(2026, 1, 2), D(2026, 1, 5)
    assert d.business_days_between(fri, mon) == 1
    assert d.business_days_between(mon, fri) == -1
    assert d.business_days_between(fri, fri) == 0
    assert d.business_days_between(fri, D(2026, 1, 16)) == 10
    assert d.business_days_between(fri, D(2026, 1, 16), [D(2026, 1, 12), D(2026, 1, 10)]) == 9


def test_business_days_between_matches_add_business_days():
    start = D(2026, 3, 4)
    holidays = [D(2026, 3, 9), D(2026, 4, 3)]
    for n in range(-40, 41):
        end = d.add_business_days(start, n, holidays)
        assert d.business_days_between(start, end, holidays) == n
