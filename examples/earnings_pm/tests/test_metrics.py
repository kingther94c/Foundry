"""Tests for earnings_pm.metrics. All figures are synthetic, hand-written."""

import json
import math

import pytest

from earnings_pm.metrics import (
    METRICS_TOOL,
    Period,
    compute_metrics,
    period_from_dict,
    run_metrics_tool,
)

# Synthetic figures, not from any real company.
CURRENT = Period(
    label="Q3 2026",
    revenue=1_200.0,
    gross_profit=720.0,
    operating_income=300.0,
    net_income=240.0,
    diluted_eps=1.20,
    operating_cash_flow=400.0,
    capex=100.0,
)
PRIOR_YEAR = Period(
    label="Q3 2025",
    revenue=1_000.0,
    gross_profit=550.0,
    operating_income=200.0,
    net_income=150.0,
    diluted_eps=0.75,
    operating_cash_flow=300.0,
    capex=50.0,
)
PRIOR_QUARTER = Period(label="Q2 2026", revenue=1_100.0)


def approx(x):
    return pytest.approx(x, rel=1e-12, abs=1e-9)


def test_period_metrics():
    cur = compute_metrics(CURRENT)["current"]
    assert cur["label"] == "Q3 2026"
    assert cur["gross_margin"] == approx(0.60)
    assert cur["operating_margin"] == approx(0.25)
    assert cur["net_margin"] == approx(0.20)
    assert cur["free_cash_flow"] == approx(300.0)
    assert cur["fcf_margin"] == approx(0.25)


def test_only_current_leaves_comparisons_none():
    result = compute_metrics(CURRENT)
    assert set(result) == {"current", "prior_year", "prior_quarter", "yoy", "qoq"}
    assert result["prior_year"] is None
    assert result["prior_quarter"] is None
    assert result["yoy"] is None
    assert result["qoq"] is None


def test_yoy_growth_and_margin_bps():
    result = compute_metrics(CURRENT, prior_year=PRIOR_YEAR)
    yoy = result["yoy"]
    assert yoy["revenue_growth"] == approx(0.20)
    assert yoy["operating_income_growth"] == approx(0.50)
    assert yoy["net_income_growth"] == approx(0.60)
    assert yoy["diluted_eps_growth"] == approx(0.60)
    assert yoy["free_cash_flow_growth"] == approx(0.20)  # 300 vs 250
    assert yoy["gross_margin_change_bps"] == approx(500.0)  # 60% vs 55%
    assert yoy["operating_margin_change_bps"] == approx(500.0)  # 25% vs 20%
    assert yoy["net_margin_change_bps"] == approx(500.0)  # 20% vs 15%
    assert yoy["fcf_margin_change_bps"] == approx(0.0)  # 25% vs 25%
    assert result["prior_year"]["gross_margin"] == approx(0.55)


def test_qoq_revenue_growth():
    result = compute_metrics(CURRENT, prior_quarter=PRIOR_QUARTER)
    assert result["qoq"] == {"revenue_growth": approx(100.0 / 1_100.0)}
    assert result["prior_quarter"]["gross_margin"] is None
    assert result["yoy"] is None


@pytest.mark.parametrize("base", [0.0, -50.0])
def test_growth_on_zero_or_negative_base_is_none(base):
    prior = Period(label="Q3 2025", revenue=1_000.0, net_income=base, operating_income=base)
    cur = Period(label="Q3 2026", revenue=1_000.0, net_income=100.0, operating_income=100.0)
    yoy = compute_metrics(cur, prior_year=prior)["yoy"]
    assert yoy["net_income_growth"] is None
    assert yoy["operating_income_growth"] is None


def test_growth_to_a_loss_from_positive_base_is_negative():
    prior = Period(label="p", net_income=100.0)
    cur = Period(label="c", net_income=-50.0)
    assert compute_metrics(cur, prior_year=prior)["yoy"]["net_income_growth"] == approx(-1.5)


def test_zero_revenue_gives_none_margins_not_zero_or_error():
    cur = compute_metrics(
        Period(label="c", revenue=0.0, gross_profit=10.0, operating_cash_flow=5.0, capex=1.0)
    )["current"]
    assert cur["gross_margin"] is None
    assert cur["operating_margin"] is None
    assert cur["net_margin"] is None
    assert cur["fcf_margin"] is None
    assert cur["free_cash_flow"] == approx(4.0)


def test_missing_inputs_give_none():
    result = compute_metrics(Period(label="c"), prior_year=Period(label="p"))
    assert all(v is None for k, v in result["current"].items() if k != "label")
    assert all(v is None for v in result["yoy"].values())


def test_negative_capex_is_treated_as_outflow():
    cur = Period(label="c", operating_cash_flow=400.0, capex=-100.0)
    assert compute_metrics(cur)["current"]["free_cash_flow"] == approx(300.0)


def test_result_is_json_serializable():
    result = compute_metrics(CURRENT, PRIOR_YEAR, PRIOR_QUARTER)
    text = json.dumps(result, allow_nan=False)
    assert json.loads(text)["yoy"]["revenue_growth"] == approx(0.20)


def test_period_from_dict_roundtrip():
    d = {
        "label": "Q3 2026",
        "revenue": 1200,
        "gross_profit": 720,
        "operating_income": 300,
        "net_income": 240,
        "diluted_eps": 1.2,
        "operating_cash_flow": 400,
        "capex": None,
    }
    p = period_from_dict(d)
    assert p == Period(
        label="Q3 2026",
        revenue=1200.0,
        gross_profit=720.0,
        operating_income=300.0,
        net_income=240.0,
        diluted_eps=1.2,
        operating_cash_flow=400.0,
        capex=None,
    )
    assert isinstance(p.revenue, float)


def test_period_from_dict_missing_fields_are_none():
    assert period_from_dict({"label": "x"}) == Period(label="x")


@pytest.mark.parametrize(
    "bad",
    [
        {"revenue": 1.0},  # no label
        {"label": 3},
        {"label": "x", "revenue": "1000"},
        {"label": "x", "revenue": True},
        {"label": "x", "revenue": math.nan},
        {"label": "x", "ebitda": 1.0},
        "not a dict",
    ],
)
def test_period_from_dict_rejects_malformed(bad):
    with pytest.raises(ValueError):
        period_from_dict(bad)


def test_run_metrics_tool_with_null_priors():
    result = run_metrics_tool(
        {"current": {"label": "c", "revenue": 10, "gross_profit": 4},
         "prior_year": None, "prior_quarter": None}
    )
    assert result["current"]["gross_margin"] == approx(0.4)
    assert result["yoy"] is None and result["qoq"] is None


def test_run_metrics_tool_with_absent_priors_and_figures():
    result = run_metrics_tool({"current": {"label": "c", "revenue": 10, "gross_profit": 4}})
    assert result["current"]["gross_margin"] == approx(0.4)
    assert result["current"]["free_cash_flow"] is None
    assert result["prior_year"] is None and result["prior_quarter"] is None
    assert result["yoy"] is None and result["qoq"] is None


def test_absent_and_null_figures_are_equivalent():
    absent = run_metrics_tool(
        {"current": {"label": "c", "revenue": 10}, "prior_year": {"label": "p", "revenue": 8}}
    )
    null = run_metrics_tool(
        {
            "current": {"label": "c", "revenue": 10, "capex": None, "net_income": None},
            "prior_year": {"label": "p", "revenue": 8, "capex": None},
            "prior_quarter": None,
        }
    )
    assert absent == null
    assert absent["yoy"]["revenue_growth"] == approx(0.25)


def test_metrics_tool_definition():
    assert METRICS_TOOL["name"] == "compute_metrics"
    assert "strict" not in METRICS_TOOL
    schema = METRICS_TOOL["input_schema"]
    assert schema["required"] == ["current"]
    assert schema["additionalProperties"] is False
    for key in ("current", "prior_year", "prior_quarter"):
        period = schema["properties"][key]
        assert period["type"] == "object"
        assert period["required"] == ["label"]
        assert period["additionalProperties"] is False
        props = period["properties"]
        assert props["label"]["type"] == "string"
        for name in ("revenue", "gross_profit", "operating_income", "net_income",
                     "diluted_eps", "operating_cash_flow", "capex"):
            assert props[name]["type"] == "number"
