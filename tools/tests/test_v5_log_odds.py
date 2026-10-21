import math

import pytest

from tools import stats as s


def test_logit_basic_and_exact_half():
    assert s.logit(0.5) == 0.0
    assert s.logit(0.8) == pytest.approx(math.log(4))
    assert s.logit(0.2) == pytest.approx(-math.log(4))
    assert s.logit(1) == pytest.approx(math.log((1 - 1e-6) / 1e-6))
    assert s.logit(0) == pytest.approx(-math.log((1 - 1e-6) / 1e-6))
    assert s.logit(0, eps=0.1) == pytest.approx(math.log(0.1 / 0.9))
    assert isinstance(s.logit(1), float)


@pytest.mark.parametrize("p", [-0.1, 1.1, 10**400, None, True, False, "0.5", math.nan, math.inf])
def test_logit_rejects_bad_p(p):
    with pytest.raises(ValueError):
        s.logit(p)


@pytest.mark.parametrize("eps", [0, 0.5, -1e-6, 1, None, True, "0.1", math.nan, math.inf])
def test_logit_rejects_bad_eps(eps):
    with pytest.raises(ValueError):
        s.logit(0.5, eps)


def test_sigmoid_values():
    assert s.sigmoid(0) == 0.5
    assert s.sigmoid(0.0) == 0.5
    assert s.sigmoid(-0.0) == 0.5
    assert s.sigmoid(math.log(4)) == pytest.approx(0.8)
    assert s.sigmoid(-math.log(4)) == pytest.approx(0.2)
    assert s.sigmoid(s.logit(0.3)) == pytest.approx(0.3)


def test_sigmoid_does_not_overflow():
    assert s.sigmoid(1000) == 1.0
    assert s.sigmoid(-1000) == 0.0
    assert s.sigmoid(1e308) == 1.0
    assert s.sigmoid(-1e308) == 0.0
    assert s.sigmoid(10**400) == 1.0
    assert s.sigmoid(-(10**400)) == 0.0
    assert 0.0 < s.sigmoid(-700) < 1e-300


@pytest.mark.parametrize("x", [None, True, "1", math.nan, math.inf, -math.inf, [1]])
def test_sigmoid_rejects_non_numbers(x):
    with pytest.raises(ValueError):
        s.sigmoid(x)


def test_pool_spec_examples():
    assert s.pool_log_odds({"a": 0.5, "b": 0.5}) == 0.5
    assert s.pool_log_odds({"a": 0.6, "b": 0.6}) == pytest.approx(0.6923, abs=1e-4)
    assert s.pool_log_odds({"a": 0.7, "b": 0.3}) == pytest.approx(0.5)
    expected = s.sigmoid(1.5 * s.logit(0.8) + 0.5 * s.logit(0.6))
    assert s.pool_log_odds({"a": 0.8, "b": 0.6}, {"a": 3, "b": 1}) == expected
    assert s.pool_log_odds({"a": None}) is None


def test_pool_without_weights_skips_non_numbers():
    probs = {"a": 0.8, "b": None, "c": True, "d": math.nan, "e": "0.9"}
    assert s.pool_log_odds(probs) == pytest.approx(0.8)
    assert s.pool_log_odds({}) is None


def test_pool_single_and_clipping():
    assert s.pool_log_odds({"a": 0.3}) == pytest.approx(0.3)
    assert s.pool_log_odds({"a": 1.0}) == pytest.approx(1 - 1e-6)
    assert s.pool_log_odds({"a": 1.0, "b": 0.0}) == pytest.approx(0.5)
    assert s.pool_log_odds({"a": 1.0}, eps=0.1) == pytest.approx(0.9)


def test_pool_equal_weights_give_plain_sum():
    plain = s.pool_log_odds({"a": 0.8, "b": 0.6})
    assert s.pool_log_odds({"a": 0.8, "b": 0.6}, {"a": 7, "b": 7}) == pytest.approx(plain)
    assert s.pool_log_odds({"a": 0.8, "b": 0.6}, {"a": 0.01, "b": 0.01}) == pytest.approx(plain)


def test_pool_weights_leave_out_keys():
    probs = {"a": 0.8, "b": 0.6, "c": 0.9, "d": 0.1, "e": 0.2, "f": 0.3, "g": None}
    weights = {"a": 2, "b": 0, "c": -1, "d": None, "e": True, "f": math.nan, "g": 5, "z": 9}
    assert s.pool_log_odds(probs, weights) == pytest.approx(0.8)
    assert s.pool_log_odds({"a": 0.8}, {}) is None
    assert s.pool_log_odds({"a": 0.8}, {"a": 0}) is None


def test_pool_weights_scaled_to_average_one():
    probs = {"a": 0.9, "b": 0.4, "c": 0.7}
    weights = {"a": 1, "b": 2, "c": 5}
    m, total = 3, 8
    expected = s.sigmoid(sum(m * weights[k] / total * s.logit(probs[k]) for k in probs))
    assert s.pool_log_odds(probs, weights) == pytest.approx(expected)


def test_pool_huge_weights_do_not_overflow():
    probs = {"a": 0.8, "b": 0.6}
    plain = s.pool_log_odds(probs)
    assert s.pool_log_odds(probs, {"a": 10**400, "b": 10**400}) == pytest.approx(plain)
    assert s.pool_log_odds(probs, {"a": 1e308, "b": 1e308}) == pytest.approx(plain)
    mixed = s.pool_log_odds(probs, {"a": 10**400, "b": 1.0})
    assert mixed == pytest.approx(s.sigmoid(2 * s.logit(0.8)))


def test_pool_raises_on_bad_probability_or_eps():
    with pytest.raises(ValueError):
        s.pool_log_odds({"a": 1.5})
    with pytest.raises(ValueError):
        s.pool_log_odds({"a": -0.1, "b": 0.5}, {"a": 1, "b": 1})
    with pytest.raises(ValueError):
        s.pool_log_odds({"a": 0.5}, eps=0)
    with pytest.raises(ValueError):
        s.pool_log_odds({"a": 0.5}, eps=0.5)
    with pytest.raises(ValueError):
        s.pool_log_odds({"a": 0.5}, eps="x")


def test_pool_is_pure():
    probs = {"a": 0.8, "b": 0.6}
    weights = {"a": 3, "b": 1}
    s.pool_log_odds(probs, weights)
    assert probs == {"a": 0.8, "b": 0.6}
    assert weights == {"a": 3, "b": 1}
