import datetime
import math

import pytest

from tools import returns as r

D = datetime.date


def test_simple_and_log_return():
    assert r.simple_return(100, 110) == pytest.approx(0.1)
    assert r.simple_return(0, 110) is None
    assert r.simple_return(None, 1) is None
    assert r.log_return(100, 110) == pytest.approx(math.log(1.1))
    assert r.log_return(100, 0) is None
    assert r.log_return(-1, 5) is None


def test_compound():
    assert r.compound([0.1, 0.1]) == pytest.approx(0.21)
    assert r.compound([]) == 0.0
    assert r.compound([0.1, None]) is None


def test_abnormal_return():
    assert r.abnormal_return(0.05, 0.02) == pytest.approx(0.03)
    assert r.abnormal_return(0.05, 0.02, beta=2) == pytest.approx(0.01)
    assert r.abnormal_return(0.05, None) is None


CLOSES = [
    (D(2026, 1, 7), 120),
    (D(2026, 1, 5), 100),
    (D(2026, 1, 8), 90),
    (D(2026, 1, 2), 50),
]


def test_event_return():
    # Base is 2026-01-05 (latest strictly before the event), end the first close on/after.
    assert r.event_return(CLOSES, D(2026, 1, 6)) == pytest.approx(0.2)
    assert r.event_return(CLOSES, D(2026, 1, 7)) == pytest.approx(0.2)
    assert r.event_return(CLOSES, D(2026, 1, 7), after=2) == pytest.approx(-0.1)
    assert r.event_return(CLOSES, D(2026, 1, 7), after=3) is None
    assert r.event_return(CLOSES, D(2026, 1, 2)) is None
    assert r.event_return(CLOSES, D(2026, 1, 7), after=0) is None


def test_max_drawdown():
    assert r.max_drawdown([100, 75]) == pytest.approx(-0.25)
    assert r.max_drawdown([100, 120, 60, 130, 110]) == pytest.approx(-0.5)
    assert r.max_drawdown([1, 2, 3]) == 0.0
    assert r.max_drawdown([5]) == 0.0
    assert r.max_drawdown([]) == 0.0
    with pytest.raises(ValueError):
        r.max_drawdown([100, 0])


def test_annualized_volatility():
    assert r.annualized_volatility([0.01, -0.01]) == pytest.approx(
        math.sqrt(0.0002) * math.sqrt(252)
    )
    assert r.annualized_volatility([0.01, 0.03], periods_per_year=1) == pytest.approx(
        math.sqrt(0.0002)
    )
    assert r.annualized_volatility([0.01]) is None
    assert r.annualized_volatility([0.01, None]) is None
