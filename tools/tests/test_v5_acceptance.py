"""Acceptance tests for SPEC.md "v5: independent evidence" (logit, sigmoid, pool_log_odds)."""

import math

import pytest

from tools.stats import logit, pool_log_odds, sigmoid


def L(p):
    return math.log(p / (1 - p))


def S(x):
    return 1 / (1 + math.exp(-x))


# ---- logit -----------------------------------------------------------------

@pytest.mark.parametrize("p", [0.01, 0.2, 0.37, 0.6, 0.9, 0.99, 1e-5, 1 - 1e-5])
def test_logit_values(p):
    assert logit(p) == pytest.approx(L(p), rel=1e-12, abs=1e-12)


def test_logit_half_is_exactly_zero():
    assert logit(0.5) == 0.0
    assert isinstance(logit(0.5), float)


def test_logit_clamps_to_eps():
    assert logit(0.0) == pytest.approx(L(1e-6))
    assert logit(1.0) == pytest.approx(L(1 - 1e-6))
    assert logit(0) == pytest.approx(L(1e-6))
    assert logit(1) == pytest.approx(L(1 - 1e-6))
    assert logit(1e-9) == pytest.approx(L(1e-6))
    assert logit(0.0, eps=0.01) == pytest.approx(L(0.01))
    assert logit(1.0, eps=0.01) == pytest.approx(L(0.99))
    assert logit(0.3, eps=0.01) == pytest.approx(L(0.3))
    assert logit(0.995, eps=0.01) == pytest.approx(L(0.99))


def test_logit_is_antisymmetric():
    for p in (0.1, 0.25, 0.4):
        assert logit(p) == pytest.approx(-logit(1 - p))


@pytest.mark.parametrize("bad", [None, "0.5", True, False, float("nan"), float("inf"),
                                 -float("inf"), -0.1, 1.1, -1, 2, [0.5]])
def test_logit_rejects_bad_p(bad):
    with pytest.raises(ValueError):
        logit(bad)


@pytest.mark.parametrize("eps", [0, 0.0, 0.5, 0.7, -0.1, None, float("nan"), True, "0.1"])
def test_logit_rejects_bad_eps(eps):
    with pytest.raises(ValueError):
        logit(0.3, eps=eps)


# ---- sigmoid ---------------------------------------------------------------

def test_sigmoid_zero_is_exactly_half():
    assert sigmoid(0) == 0.5
    assert sigmoid(0.0) == 0.5


@pytest.mark.parametrize("x", [-30.0, -5, -1.5, -0.01, 0.01, 1, 2.5, 7, 30.0])
def test_sigmoid_values(x):
    assert sigmoid(x) == pytest.approx(S(x), rel=1e-12, abs=1e-300)


def test_sigmoid_never_overflows():
    assert sigmoid(-1000.0) == pytest.approx(0.0, abs=1e-300)
    assert sigmoid(-1000) >= 0.0
    assert sigmoid(1000.0) == 1.0
    assert sigmoid(-1e308) == 0.0
    assert sigmoid(1e308) == 1.0
    assert sigmoid(-800) > 0.0 or sigmoid(-800) == 0.0


def test_sigmoid_huge_ints():
    assert sigmoid(10 ** 400) == 1.0
    assert sigmoid(-(10 ** 400)) == 0.0
    assert isinstance(sigmoid(10 ** 400), float)


def test_sigmoid_is_symmetric():
    for x in (0.3, 2.0, 11.0):
        assert sigmoid(-x) == pytest.approx(1 - sigmoid(x))


def test_sigmoid_inverts_logit():
    for p in (0.02, 0.3, 0.5, 0.77, 0.98):
        assert sigmoid(logit(p)) == pytest.approx(p)


@pytest.mark.parametrize("bad", [None, "1", True, False, float("nan"), float("inf"),
                                 -float("inf"), [1.0]])
def test_sigmoid_rejects_non_numbers(bad):
    with pytest.raises(ValueError):
        sigmoid(bad)


# ---- pool_log_odds ---------------------------------------------------------

def test_pool_spec_examples():
    assert pool_log_odds({"a": 0.5, "b": 0.5}) == 0.5
    assert pool_log_odds({"a": 0.6, "b": 0.6}) == pytest.approx(0.6923076923076923)
    assert pool_log_odds({"a": 0.7, "b": 0.3}) == pytest.approx(0.5)
    assert pool_log_odds({"a": 0.8, "b": 0.6}, {"a": 3, "b": 1}) == pytest.approx(
        S(1.5 * L(0.8) + 0.5 * L(0.6)))
    assert pool_log_odds({"a": None}) is None


def test_pool_single_key_is_its_probability():
    for p in (0.1, 0.42, 0.9):
        assert pool_log_odds({"x": p}) == pytest.approx(p)
        assert pool_log_odds({"x": p}, {"x": 7.5}) == pytest.approx(p)


def test_pool_adds_independent_evidence():
    probs = {"a": 0.6, "b": 0.7, "c": 0.45, "d": 0.5}
    assert pool_log_odds(probs) == pytest.approx(S(L(0.6) + L(0.7) + L(0.45)))
    assert isinstance(pool_log_odds(probs), float)


def test_pool_empty_and_nothing_usable():
    assert pool_log_odds({}) is None
    assert pool_log_odds({"a": None, "b": "0.6", "c": True, "d": float("nan")}) is None
    assert pool_log_odds({"a": 0.6}, {}) is None
    assert pool_log_odds({"a": 0.6}, {"a": 0}) is None
    assert pool_log_odds({}, {"a": 1}) is None


def test_pool_without_weights_skips_non_numbers():
    probs = {"a": 0.8, "b": None, "c": "x", "d": False, "e": float("nan"), "f": 0.3}
    assert pool_log_odds(probs) == pytest.approx(S(L(0.8) + L(0.3)))


def test_pool_weights_scaled_to_average_one():
    probs = {"a": 0.8, "b": 0.35, "c": 0.6}
    plain = pool_log_odds(probs)
    assert pool_log_odds(probs, {"a": 1, "b": 1, "c": 1}) == pytest.approx(plain)
    assert pool_log_odds(probs, {"a": 0.2, "b": 0.2, "c": 0.2}) == pytest.approx(plain)
    assert pool_log_odds(probs, {"a": 50, "b": 50, "c": 50}) == pytest.approx(plain)
    w = {"a": 2.0, "b": 1.0, "c": 1.0}
    expected = S(3 * (2 / 4) * L(0.8) + 3 * (1 / 4) * L(0.35) + 3 * (1 / 4) * L(0.6))
    assert pool_log_odds(probs, w) == pytest.approx(expected)
    big = {k: v * 1000 for k, v in w.items()}
    assert pool_log_odds(probs, big) == pytest.approx(expected)


def test_pool_weights_leave_out_keys_as_combine_does():
    probs = {"a": 0.8, "b": 0.35, "c": 0.6, "d": 0.9, "e": 0.2, "f": 0.7, "g": None}
    weights = {"a": 1.0, "b": 0, "c": -2.0, "d": None, "e": True, "f": float("nan"),
               "g": 1.0, "z": 4.0}
    # only "a" is used (e has a bool weight; g has no probability; z has no probability)
    assert pool_log_odds(probs, weights) == pytest.approx(0.8)
    weights2 = {"a": 1.0, "f": 3.0}
    expected = S(2 * (1 / 4) * L(0.8) + 2 * (3 / 4) * L(0.7))
    assert pool_log_odds(probs, weights2) == pytest.approx(expected)


def test_pool_clamps_with_eps():
    assert pool_log_odds({"a": 1.0}) == pytest.approx(1 - 1e-6)
    assert pool_log_odds({"a": 0.0}) == pytest.approx(1e-6)
    assert pool_log_odds({"a": 1.0}, eps=0.01) == pytest.approx(0.99)
    assert pool_log_odds({"a": 1, "b": 1}, eps=0.01) == pytest.approx(S(2 * L(0.99)))
    assert pool_log_odds({"a": 0.0, "b": 1.0}) == pytest.approx(0.5)


def test_pool_many_strong_views_do_not_overflow():
    probs = {f"k{i}": 0.999999 for i in range(2000)}
    assert pool_log_odds(probs) == pytest.approx(1.0)
    probs = {f"k{i}": 1e-6 for i in range(2000)}
    assert pool_log_odds(probs) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("bad", [-0.1, 1.5, -3, 2])
def test_pool_rejects_out_of_range_probability(bad):
    with pytest.raises(ValueError):
        pool_log_odds({"a": 0.6, "b": bad})
    with pytest.raises(ValueError):
        pool_log_odds({"a": 0.6, "b": bad}, {"a": 1, "b": 1})


@pytest.mark.parametrize("eps", [0, 0.5, -1, None])
def test_pool_rejects_bad_eps(eps):
    with pytest.raises(ValueError):
        pool_log_odds({"a": 0.6}, eps=eps)


def test_pool_does_not_change_its_inputs():
    probs = {"a": 0.8, "b": 0.35}
    weights = {"a": 2.0, "b": 1.0}
    pool_log_odds(probs, weights)
    assert probs == {"a": 0.8, "b": 0.35} and weights == {"a": 2.0, "b": 1.0}


def test_pool_accepts_any_mapping():
    from types import MappingProxyType
    probs = MappingProxyType({"a": 0.6, "b": 0.6})
    assert pool_log_odds(probs, MappingProxyType({"a": 1, "b": 1})) == pytest.approx(
        0.6923076923076923)


def test_pool_symmetry():
    probs = {"a": 0.8, "b": 0.35, "c": 0.6}
    flipped = {k: 1 - v for k, v in probs.items()}
    assert pool_log_odds(flipped) == pytest.approx(1 - pool_log_odds(probs))
