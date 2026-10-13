import pytest

from tools import finance as f


def test_safe_div():
    assert f.safe_div(1, 4) == 0.25
    assert f.safe_div(1, 0) is None
    assert f.safe_div(None, 2) is None
    assert f.safe_div(2, None) is None


def test_growth():
    assert f.growth(110, 100) == pytest.approx(0.1)
    assert f.growth(90, 100) == pytest.approx(-0.1)
    assert f.growth(10, 0) is None
    assert f.growth(10, -5) is None
    assert f.growth(None, 5) is None


def test_cagr():
    assert f.cagr(100, 121, 2) == pytest.approx(0.1)
    assert f.cagr(100, 0, 3) == -1.0
    assert f.cagr(0, 10, 1) is None
    assert f.cagr(10, -1, 1) is None
    assert f.cagr(10, 20, 0) is None
    assert f.cagr(10, None, 1) is None
    assert f.cagr(1, 1e300, 0.001) is None  # overflow is None, not an exception


def test_margin_and_bps():
    assert f.margin(25, 100) == 0.25
    assert f.margin(25, 0) is None
    assert f.change_bps(0.12, 0.10) == pytest.approx(200)
    assert f.change_bps(None, 0.1) is None


def test_free_cash_flow():
    assert f.free_cash_flow(100, 30) == 70
    assert f.free_cash_flow(100, -30) == 70
    assert f.free_cash_flow(None, 30) is None


def test_ttm():
    assert f.ttm([1, 2, 3, 4, 5]) == 14
    assert f.ttm([1, 2, 3]) is None
    assert f.ttm([None, 2, 3, 4, 5]) == 14
    assert f.ttm([1, 2, None, 4]) is None


def test_yoy_growth_series():
    out = f.yoy_growth_series([100, 1, 1, 1, 110, None], lag=4)
    assert out[:4] == [None] * 4
    assert out[4] == pytest.approx(0.1)
    assert out[5] is None
    assert f.yoy_growth_series([1, 2], lag=1) == [None, 1.0]
    with pytest.raises(ValueError):
        f.yoy_growth_series([1, 2], lag=0)


def test_surprise_and_beat_miss():
    assert f.surprise(110, 100) == pytest.approx(0.1)
    assert f.surprise(-0.5, -1) == pytest.approx(0.5)
    assert f.surprise(1, 0) is None
    assert f.beat_miss(105, 100) == "beat"
    assert f.beat_miss(95, 100) == "miss"
    assert f.beat_miss(100.5, 100) == "in_line"
    assert f.beat_miss(101, 100) == "in_line"  # exactly at tolerance
    assert f.beat_miss(99, 100) == "in_line"
    assert f.beat_miss(1, 0) is None
