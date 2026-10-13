"""Acceptance tests for tools/, written from SPEC.md independently of the code.

They were held back from the agent that built the package: it saw only SPEC.md
and FOUNDRY.md, and these decided whether its attempt was accepted.

Every case traces to a sentence of SPEC.md. Float boundaries use exactly
representable values so the test, not floating point, decides.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from tools import dates, finance, returns, stats, textmask

approx = pytest.approx


# --- finance -------------------------------------------------------------------

def test_safe_div():
    assert finance.safe_div(1, 4) == 0.25
    assert finance.safe_div(1, 0) is None
    assert finance.safe_div(None, 4) is None
    assert finance.safe_div(1, None) is None


@pytest.mark.parametrize("cur,base,expected", [
    (110, 100, 0.1), (90, 100, -0.1), (10, 0, None), (10, -5, None),
    (None, 100, None), (100, None, None),
])
def test_growth(cur, base, expected):
    result = finance.growth(cur, base)
    assert result == (approx(expected) if expected is not None else None)


def test_cagr():
    assert finance.cagr(100, 121, 2) == approx(0.1)
    assert finance.cagr(100, 0, 2) == approx(-1.0)
    for args in [(0, 10, 1), (-1, 10, 1), (100, -1, 2), (100, 121, 0), (100, 121, -1),
                 (None, 1, 1), (1, None, 1), (1, 1, None)]:
        assert finance.cagr(*args) is None


def test_uncomputable_results_are_none_not_exceptions():
    # The general rule covers float overflow too: (2.5e9) ** 1e9 cannot be represented.
    assert finance.cagr(1e-9, 2.5, 1e-9) is None


def test_margin_and_change_bps():
    assert finance.margin(25, 100) == 0.25
    assert finance.margin(25, 0) is None
    assert finance.change_bps(0.255, 0.25) == approx(50.0)
    assert finance.change_bps(None, 0.25) is None


def test_free_cash_flow_accepts_either_capex_sign():
    assert finance.free_cash_flow(100, 30) == 70
    assert finance.free_cash_flow(100, -30) == 70
    assert finance.free_cash_flow(None, 30) is None
    assert finance.free_cash_flow(100, None) is None


def test_ttm():
    assert finance.ttm([1, 2, 3, 4, 5]) == 14
    assert finance.ttm([None, 2, 3, 4, 5]) == 14          # None outside the last four
    assert finance.ttm([1, None, 3, 4, 5]) is None
    assert finance.ttm([1, 2, 3]) is None
    assert finance.ttm([]) is None


def test_yoy_growth_series():
    out = finance.yoy_growth_series([100, 100, 100, 100, 110, 90])
    assert out[:4] == [None, None, None, None]
    assert out[4] == approx(0.1) and out[5] == approx(-0.1)
    assert finance.yoy_growth_series([1, 2, 4], lag=1) == [None, approx(1.0), approx(1.0)]
    assert finance.yoy_growth_series([0, 5], lag=1) == [None, None]
    with pytest.raises(ValueError):
        finance.yoy_growth_series([1, 2], lag=0)


def test_surprise_uses_the_magnitude_of_the_estimate():
    assert finance.surprise(1.1, 1.0) == approx(0.1)
    assert finance.surprise(-0.9, -1.0) == approx(0.1)        # a smaller loss beats
    assert finance.surprise(1.0, 0) is None
    assert finance.surprise(None, 1.0) is None


def test_beat_miss_boundaries():
    assert finance.beat_miss(1.25, 1.0, tolerance=0.25) == "in_line"
    assert finance.beat_miss(1.5, 1.0, tolerance=0.25) == "beat"
    assert finance.beat_miss(0.75, 1.0, tolerance=0.25) == "in_line"
    assert finance.beat_miss(0.5, 1.0, tolerance=0.25) == "miss"
    assert finance.beat_miss(1.0, 1.0) == "in_line"
    assert finance.beat_miss(1.0, 0) is None


# --- returns -------------------------------------------------------------------

def test_simple_and_log_return():
    assert returns.simple_return(100, 110) == approx(0.1)
    assert returns.simple_return(0, 110) is None
    assert returns.simple_return(None, 110) is None
    assert returns.log_return(100, 110) == approx(math.log(1.1))
    assert returns.log_return(100, 0) is None
    assert returns.log_return(-1, 10) is None


def test_compound_and_abnormal():
    assert returns.compound([0.1, -0.1]) == approx(-0.01)
    assert returns.compound([]) == 0.0
    assert returns.compound([0.1, None]) is None
    assert returns.abnormal_return(0.05, 0.02, beta=1.5) == approx(0.02)
    assert returns.abnormal_return(0.05, 0.02) == approx(0.03)
    assert returns.abnormal_return(None, 0.02) is None


def _closes():
    # Deliberately out of order.
    return [(date(2026, 7, 31), 105.0), (date(2026, 7, 29), 100.0),
            (date(2026, 7, 30), 102.0), (date(2026, 8, 3), 110.0)]


def test_event_return():
    assert returns.event_return(_closes(), date(2026, 7, 30)) == approx(0.02)
    assert returns.event_return(_closes(), date(2026, 7, 30), after=2) == approx(0.05)
    assert returns.event_return(_closes(), date(2026, 7, 30), after=3) == approx(0.1)
    assert returns.event_return(_closes(), date(2026, 7, 30), after=4) is None
    # A weekend event date: base is Friday's close, end is Monday's.
    assert returns.event_return(_closes(), date(2026, 8, 1)) == approx(110 / 105 - 1)
    assert returns.event_return(_closes(), date(2026, 7, 29)) is None     # no base
    assert returns.event_return(_closes(), date(2026, 7, 30), after=0) is None


def test_max_drawdown():
    assert returns.max_drawdown([100, 120, 90, 130, 117]) == approx(-0.25)
    assert returns.max_drawdown([1, 2, 3]) == 0.0
    assert returns.max_drawdown([100]) == 0.0
    assert returns.max_drawdown([]) == 0.0
    with pytest.raises(ValueError):
        returns.max_drawdown([100, 0])


def test_annualized_volatility():
    rs = [0.01, -0.02, 0.03]
    m = sum(rs) / 3
    sd = math.sqrt(sum((r - m) ** 2 for r in rs) / 2)
    assert returns.annualized_volatility(rs) == approx(sd * math.sqrt(252))
    assert returns.annualized_volatility(rs, periods_per_year=12) == approx(sd * math.sqrt(12))
    assert returns.annualized_volatility([0.01]) is None
    assert returns.annualized_volatility([0.01, None]) is None


# --- stats ---------------------------------------------------------------------

def test_mean_stdev_zscore():
    assert stats.mean([1, 2, 3]) == 2
    assert stats.mean([]) is None
    assert stats.mean([1, None]) is None
    assert stats.stdev([1, 2, 3]) == approx(1.0)
    assert stats.stdev([1]) is None
    assert stats.stdev([1, None, 3]) is None
    assert stats.zscore(3, [1, 2, 3]) == approx(1.0)
    assert stats.zscore(1, [2, 2, 2]) is None
    assert stats.zscore(None, [1, 2, 3]) is None


def test_quantile():
    assert stats.quantile([4, 1, 3, 2], 0.5) == approx(2.5)
    assert stats.quantile([1, 2, 3, 4], 0.0) == 1
    assert stats.quantile([1, 2, 3, 4], 1.0) == 4
    assert stats.quantile([10], 0.3) == 10
    assert stats.quantile([], 0.5) is None
    with pytest.raises(ValueError):
        stats.quantile([1, 2], 1.5)


def test_percentile_rank_is_mid_rank():
    assert stats.percentile_rank(2, [1, 2, 2, 3]) == approx(0.5)
    assert stats.percentile_rank(0, [1, 2]) == 0.0
    assert stats.percentile_rank(5, [1, 2]) == 1.0
    assert stats.percentile_rank(1, []) is None


def test_winsorize():
    assert stats.winsorize([1, 2, 3, 4, 100], 0.0, 0.75) == [1, 2, 3, 4, 4]
    assert stats.winsorize([100, 1, 50], 0.5, 0.5) == [50, 50, 50]
    assert stats.winsorize([], 0.1, 0.9) == []
    with pytest.raises(ValueError):
        stats.winsorize([1, 2], 0.9, 0.1)
    with pytest.raises(ValueError):
        stats.winsorize([1, 2], -0.1, 0.5)


def test_brier_and_log_loss():
    assert stats.brier_score([0.9, 0.2], [1, 0]) == approx(0.025)
    assert stats.brier_score([1.0], [True]) == 0.0
    expected = -(math.log(0.9) + math.log(0.8)) / 2
    assert stats.log_loss([0.9, 0.2], [1, 0]) == approx(expected)
    clipped = stats.log_loss([1.0], [0])
    # p clipped to 1 - eps; 1 - p is then what float arithmetic makes of it.
    assert math.isfinite(clipped) and clipped == approx(-math.log(1 - (1 - 1e-15)), rel=1e-6)
    for fn in (stats.brier_score, stats.log_loss):
        with pytest.raises(ValueError):
            fn([0.5], [1, 0])
        with pytest.raises(ValueError):
            fn([], [])
        with pytest.raises(ValueError):
            fn([1.5], [1])


def test_directional_accuracy():
    assert stats.directional_accuracy([1, -2, 0, 3], [2, -1, 5, -1]) == approx(2 / 3)
    assert stats.directional_accuracy([0, 1], [1, 0]) is None
    with pytest.raises(ValueError):
        stats.directional_accuracy([1], [1, 2])


def test_calibration_table():
    table = stats.calibration_table([0.05, 0.15, 0.95, 1.0], [0, 1, 1, 1], bins=10)
    assert [row["count"] for row in table] == [1, 1, 2]
    first, second, last = table
    assert first["lower"] == approx(0.0) and first["upper"] == approx(0.1)
    assert first["mean_prob"] == approx(0.05) and first["frequency"] == 0
    assert second["lower"] == approx(0.1) and second["frequency"] == 1
    assert last["lower"] == approx(0.9) and last["upper"] == approx(1.0)
    assert last["mean_prob"] == approx(0.975) and last["frequency"] == 1
    assert set(first) == {"lower", "upper", "count", "mean_prob", "frequency"}
    with pytest.raises(ValueError):
        stats.calibration_table([0.5], [1], bins=0)


def test_hedge_update():
    w = stats.hedge_update({"a": 0.5, "b": 0.5}, {"a": 1.0, "b": 0.0}, eta=0.5)
    e = math.exp(-0.5)
    assert w["a"] == approx(e / (1 + e)) and w["b"] == approx(1 / (1 + e))
    assert sum(w.values()) == approx(1.0)
    w = stats.hedge_update({"a": 1.0, "b": 3.0}, {"z": 5.0})       # missing loss = 0
    assert w == {"a": approx(0.25), "b": approx(0.75)}
    assert stats.hedge_update({"a": 0.0, "b": 0.0}, {}) == {"a": approx(0.5), "b": approx(0.5)}
    assert stats.hedge_update({}, {"a": 1.0}) == {}
    with pytest.raises(ValueError):
        stats.hedge_update({"a": -0.1}, {})


def test_hedge_update_does_not_mutate_its_input():
    weights = {"a": 0.5, "b": 0.5}
    stats.hedge_update(weights, {"a": 1.0})
    assert weights == {"a": 0.5, "b": 0.5}


def test_combine_probabilities():
    p = stats.combine_probabilities({"a": 0.8, "b": 0.2, "c": 0.5},
                                    {"a": 0.75, "b": 0.25, "d": 1.0})
    assert p == approx(0.65)
    assert stats.combine_probabilities({"a": 0.8}, {"a": 0.0}) is None
    assert stats.combine_probabilities({}, {}) is None


# --- textmask ------------------------------------------------------------------

def test_redact_whole_words_longest_first_any_case():
    mapping = {"Acme": "Company A", "Acme Cloud": "Product X", "Jane Doe": "the CEO"}
    text = "Acme's Acme Cloud grew; ACME said. Acmeville. Jane Doe spoke."
    assert textmask.redact(text, mapping) == \
        "Company A's Product X grew; Company A said. Acmeville. the CEO spoke."


def test_redact_never_rescans_replacements():
    assert textmask.redact("A B", {"A": "B", "B": "C"}) == "B C"


def test_redact_literal_keys_and_case_sensitivity():
    assert textmask.redact("C++ rocks", {"C++": "Lang X"}) == "Lang X rocks"
    assert textmask.redact("a.b axb", {"a.b": "Q"}) == "Q axb"
    assert textmask.redact("Apple apple", {"apple": "fruit"}, case_sensitive=True) == "Apple fruit"
    assert textmask.redact("x", {"": "boom"}) == "x"
    assert textmask.redact("Acme1 Acme", {"Acme": "Co"}) == "Acme1 Co"


def test_find_terms():
    text = "Globex and acme, again Acme."
    assert textmask.find_terms(text, ["Initech", "Acme", "Globex", "Acme"]) == ["Acme", "Globex"]
    assert textmask.find_terms(text, ["Acme"], case_sensitive=True) == ["Acme"]
    assert textmask.find_terms(text, ["acm"]) == []


@pytest.mark.parametrize("text,expected", [
    ("$90.0 billion", "$45.0 billion"),
    ("$90,007 million", "$45,004 million"),
    ("US$4.81", "US$2.40"),
    ("$1.2bn", "$0.6bn"),
    ("$12K and $3 thousand", "$6K and $2 thousand"),
    ("revenue of $1,000,000 rose 18%", "revenue of $500,000 rose 18%"),
    ("EPS was $2.00 per share", "EPS was $1.00 per share"),
    ("$4 Billion", "$2 Billion"),
    ("$8M", "$4M"),
    ("up 18% to 90 billion units", "up 18% to 90 billion units"),
])
def test_scale_money(text, expected):
    assert textmask.scale_money(text, 0.5) == expected


def test_scale_money_rejects_a_non_positive_factor():
    with pytest.raises(ValueError):
        textmask.scale_money("$1", 0)
    with pytest.raises(ValueError):
        textmask.scale_money("$1", -2)


@pytest.mark.parametrize("text,offset,expected", [
    ("fiscal 2026", -7, "fiscal 2019"),
    ("FY26", -7, "FY19"),
    ("FY2026", -7, "FY2019"),
    ("in 2026, sales", -7, "in 2019, sales"),
    ("$2,026", -7, "$2,026"),
    ("12026", -7, "12026"),
    ("2026.5", -7, "2026.5"),
    ("Q3 FY26 and FY2027 guidance", 3, "Q3 FY29 and FY2030 guidance"),
    ("FY99", 1, "FY00"),
    ("1999 to 2001", 1, "2000 to 2002"),
    ("FY265", -7, "FY265"),
    ("1800 and 2100", 5, "1800 and 2100"),
    ("in 2026.", 1, "in 2027."),
])
def test_shift_years(text, offset, expected):
    assert textmask.shift_years(text, offset) == expected


# --- dates ---------------------------------------------------------------------

@pytest.mark.parametrize("d,month,expected", [
    (date(2026, 7, 15), 6, (2027, 1)),
    (date(2026, 6, 30), 6, (2026, 4)),
    (date(2026, 6, 27), 9, (2026, 3)),
    (date(2026, 5, 1), 12, (2026, 2)),
    (date(2026, 3, 15), 1, (2027, 1)),
    (date(2026, 1, 31), 1, (2026, 4)),
    (date(2026, 12, 31), 12, (2026, 4)),
])
def test_fiscal_quarter(d, month, expected):
    assert dates.fiscal_quarter(d, month) == expected


def test_fiscal_quarter_rejects_bad_month():
    with pytest.raises(ValueError):
        dates.fiscal_quarter(date(2026, 1, 1), 13)


@pytest.mark.parametrize("args,expected", [
    ((2026, 4, 6), date(2026, 6, 30)),
    ((2027, 1, 6), date(2026, 9, 30)),
    ((2026, 3, 9), date(2026, 6, 30)),
    ((2028, 4, 2), date(2028, 2, 29)),
    ((2026, 2, 12), date(2026, 6, 30)),
])
def test_quarter_end(args, expected):
    assert dates.quarter_end(*args) == expected


def test_quarter_end_validation():
    with pytest.raises(ValueError):
        dates.quarter_end(2026, 5, 12)
    with pytest.raises(ValueError):
        dates.quarter_end(2026, 1, 0)


def test_add_business_days():
    fri, mon = date(2026, 10, 9), date(2026, 10, 12)
    assert dates.add_business_days(fri, 1) == mon
    assert dates.add_business_days(fri, 1, holidays=[mon]) == date(2026, 10, 13)
    assert dates.add_business_days(mon, -1) == fri
    assert dates.add_business_days(fri, 5) == date(2026, 10, 16)
    sat = date(2026, 8, 1)
    assert dates.add_business_days(sat, 0) == sat
    assert dates.add_business_days(sat, 1) == date(2026, 8, 3)


def test_business_days_between():
    fri, mon = date(2026, 10, 9), date(2026, 10, 12)
    assert dates.business_days_between(fri, mon) == 1
    assert dates.business_days_between(mon, date(2026, 10, 16)) == 4
    assert dates.business_days_between(date(2026, 10, 16), mon) == -4
    assert dates.business_days_between(mon, mon) == 0
    assert dates.business_days_between(fri, date(2026, 10, 13), holidays=[mon]) == 1
