import math

import pytest

from tools import stats as s


def test_mean_stdev_zscore():
    assert s.mean([1, 2, 3, 4]) == 2.5
    assert s.mean([]) is None
    assert s.mean([1, None]) is None
    assert s.stdev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(math.sqrt(32 / 7))
    assert s.stdev([1]) is None
    assert s.zscore(3, [1, 3, 5]) == 0
    assert s.zscore(5, [1, 3, 5]) == pytest.approx(1.0)
    assert s.zscore(1, [2, 2, 2]) is None
    assert s.zscore(None, [1, 2]) is None


def test_quantile():
    assert s.quantile([3, 1, 2, 4], 0.5) == 2.5
    assert s.quantile([1, 2, 3, 4], 0) == 1
    assert s.quantile([1, 2, 3, 4], 1) == 4
    assert s.quantile([10, 20], 0.25) == 12.5
    assert s.quantile([], 0.5) is None
    with pytest.raises(ValueError):
        s.quantile([1, 2], 1.5)


def test_percentile_rank():
    assert s.percentile_rank(2, [1, 2, 3, 4]) == pytest.approx(0.375)
    assert s.percentile_rank(10, [1, 2]) == 1.0
    assert s.percentile_rank(1, []) is None


def test_winsorize():
    values = [1, 2, 3, 4, 100]
    lo, hi = s.quantile(values, 0.25), s.quantile(values, 0.75)
    assert s.winsorize(values, 0.25, 0.75) == [lo, lo, 3, hi, hi]
    assert s.winsorize([]) == []
    with pytest.raises(ValueError):
        s.winsorize([1, 2], 0.6, 0.4)
    with pytest.raises(ValueError):
        s.winsorize([1, 2], -0.1, 0.5)


def test_brier_and_log_loss():
    assert s.brier_score([1, 0, 0.5], [1, 0, 1]) == pytest.approx(0.25 / 3)
    assert s.brier_score([0.8], [True]) == pytest.approx(0.04)
    expected = -(math.log(0.8) + math.log(0.6)) / 2
    assert s.log_loss([0.8, 0.4], [1, 0]) == pytest.approx(expected)
    assert math.isfinite(s.log_loss([0.0], [1]))
    for fn in (s.brier_score, s.log_loss):
        with pytest.raises(ValueError):
            fn([0.5], [1, 0])
        with pytest.raises(ValueError):
            fn([], [])
        with pytest.raises(ValueError):
            fn([1.2], [1])


def test_directional_accuracy():
    assert s.directional_accuracy([1, -1, 2, 0], [2, 1, 3, 5]) == pytest.approx(2 / 3)
    assert s.directional_accuracy([0, 1], [1, 0]) is None
    with pytest.raises(ValueError):
        s.directional_accuracy([1], [1, 2])


def test_calibration_table():
    table = s.calibration_table([0.05, 0.15, 0.12, 1.0, 0.9], [0, 1, 0, 1, 1], bins=10)
    assert [row["lower"] for row in table] == [0.0, 0.1, 0.9]
    assert table[1] == {
        "lower": 0.1,
        "upper": 0.2,
        "count": 2,
        "mean_prob": pytest.approx(0.135),
        "frequency": 0.5,
    }
    assert table[2]["count"] == 2  # 1.0 lands in the last bin
    # Bin edges are honoured despite float error in p * bins (0.29 * 100 < 29).
    edge = s.calibration_table([0.29], [1], bins=100)
    assert edge[0]["lower"] == 0.29
    with pytest.raises(ValueError):
        s.calibration_table([0.5], [1], bins=0)
    with pytest.raises(ValueError):
        s.calibration_table([0.5], [1, 0])


def test_hedge_update():
    out = s.hedge_update({"a": 0.5, "b": 0.5}, {"a": 1, "c": 5}, eta=1)
    ea = math.exp(-1)
    assert out == {"a": pytest.approx(ea / (ea + 1)), "b": pytest.approx(1 / (ea + 1))}
    assert s.hedge_update({"a": 0, "b": 0}, {}) == {"a": 0.5, "b": 0.5}
    assert s.hedge_update({}, {"a": 1}) == {}
    big = s.hedge_update({"a": 1, "b": 1}, {"a": 5000, "b": 5001})
    assert sum(big.values()) == pytest.approx(1)
    with pytest.raises(ValueError):
        s.hedge_update({"a": -1}, {})


def test_combine_probabilities():
    assert s.combine_probabilities({"a": 0.2, "b": 0.8, "c": 1}, {"a": 1, "b": 3, "d": 5}) == (
        pytest.approx(0.65)
    )
    assert s.combine_probabilities({"a": 0.2}, {"a": 0}) is None
    assert s.combine_probabilities({}, {}) is None
