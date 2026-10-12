"""Pure arithmetic over reported figures: margins, free cash flow, growth.

Conventions (see FOUNDRY.md):
- Money is in the reporting currency's units as reported.
- Ratios are fractions (0.25 == 25%); margin changes are in basis points.
- A division by zero or a missing input yields ``None``, never an exception
  and never 0.
- Growth on a zero or negative base is ``None``: a sign flip from a loss is not
  a meaningful percentage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any

NUMERIC_FIELDS: tuple[str, ...] = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "diluted_eps",
    "operating_cash_flow",
    "capex",
)


@dataclass(frozen=True)
class Period:
    """Raw figures for one reporting period, as read from the report.

    ``capex`` is the cash spent on capital expenditure as a positive number.
    """

    label: str
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    diluted_eps: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None


def _div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _growth(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base <= 0:
        return None
    return (current - base) / base


def _change_bps(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None:
        return None
    return (current - prior) * 10_000


def free_cash_flow(period: Period) -> float | None:
    """Operating cash flow minus capex.

    Capex is expected as a positive amount; a negative value (the sign a cash
    flow statement prints it with) is treated as the same cash outflow.
    """
    if period.operating_cash_flow is None or period.capex is None:
        return None
    return period.operating_cash_flow - abs(period.capex)


def period_metrics(period: Period) -> dict[str, Any]:
    """Margins and free cash flow for one period."""
    fcf = free_cash_flow(period)
    return {
        "label": period.label,
        "gross_margin": _div(period.gross_profit, period.revenue),
        "operating_margin": _div(period.operating_income, period.revenue),
        "net_margin": _div(period.net_income, period.revenue),
        "free_cash_flow": fcf,
        "fcf_margin": _div(fcf, period.revenue),
    }


_MARGINS = ("gross_margin", "operating_margin", "net_margin", "fcf_margin")


def compute_metrics(
    current: Period,
    prior_year: Period | None = None,
    prior_quarter: Period | None = None,
) -> dict[str, Any]:
    """Compute per-period metrics plus year-over-year and quarter-over-quarter
    comparisons. The result is JSON-serializable; absent comparisons are None.
    """
    cur = period_metrics(current)
    py = period_metrics(prior_year) if prior_year is not None else None
    pq = period_metrics(prior_quarter) if prior_quarter is not None else None

    yoy: dict[str, Any] | None = None
    if prior_year is not None and py is not None:
        yoy = {
            "revenue_growth": _growth(current.revenue, prior_year.revenue),
            "operating_income_growth": _growth(
                current.operating_income, prior_year.operating_income
            ),
            "net_income_growth": _growth(current.net_income, prior_year.net_income),
            "diluted_eps_growth": _growth(current.diluted_eps, prior_year.diluted_eps),
            "free_cash_flow_growth": _growth(cur["free_cash_flow"], py["free_cash_flow"]),
        }
        for name in _MARGINS:
            yoy[f"{name}_change_bps"] = _change_bps(cur[name], py[name])

    qoq: dict[str, Any] | None = None
    if prior_quarter is not None:
        qoq = {"revenue_growth": _growth(current.revenue, prior_quarter.revenue)}

    return {
        "current": cur,
        "prior_year": py,
        "prior_quarter": pq,
        "yoy": yoy,
        "qoq": qoq,
    }


def _number(name: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or null, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return float(value)


def period_from_dict(d: dict[str, Any]) -> Period:
    """Build a Period from the compute_metrics tool-input shape.

    A numeric field that is absent or explicitly null becomes None (not given).
    Raises ValueError on malformed input.
    """
    if not isinstance(d, dict):
        raise ValueError(f"period must be an object, got {type(d).__name__}")
    label = d.get("label")
    if not isinstance(label, str):
        raise ValueError(f"label must be a string, got {label!r}")
    known = {f.name for f in fields(Period)}
    unknown = sorted(set(d) - known)
    if unknown:
        raise ValueError(f"unknown period fields: {', '.join(unknown)}")
    values = {name: _number(name, d.get(name)) for name in NUMERIC_FIELDS}
    return Period(label=label, **values)


def run_metrics_tool(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a compute_metrics tool call from its raw input.

    ``prior_year`` / ``prior_quarter`` may be absent or null; both mean not given.
    """
    if not isinstance(tool_input, dict) or "current" not in tool_input:
        raise ValueError("compute_metrics needs a 'current' period")
    current = period_from_dict(tool_input["current"])
    prior_year = tool_input.get("prior_year")
    prior_quarter = tool_input.get("prior_quarter")
    return compute_metrics(
        current,
        period_from_dict(prior_year) if prior_year is not None else None,
        period_from_dict(prior_quarter) if prior_quarter is not None else None,
    )


_FIELD_DESCRIPTIONS = {
    "revenue": "Total revenue for the period.",
    "gross_profit": "Gross profit (revenue minus cost of revenue).",
    "operating_income": "Operating income (loss as a negative number).",
    "net_income": "Net income attributable to the company (loss as negative).",
    "diluted_eps": "Diluted earnings per share.",
    "operating_cash_flow": "Net cash provided by operating activities.",
    "capex": "Capital expenditures as a POSITIVE number (cash spent).",
}

PERIOD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Raw figures for one period, in the reporting currency's units as "
        "reported (e.g. 1250000000, not 1.25 or 1250). Omit a figure the "
        "report does not give."
    ),
    "properties": {
        "label": {"type": "string", "description": "Period label, e.g. 'Q3 FY2026'."},
        **{
            name: {"type": "number", "description": _FIELD_DESCRIPTIONS[name]}
            for name in NUMERIC_FIELDS
        },
    },
    "required": ["label"],
    "additionalProperties": False,
}

# Not strict: the union-type limit across strict tools (16 per request) is
# reserved for submit_memo. run_metrics_tool validates this input itself, so
# optional values are optional properties rather than nullable ones.

METRICS_TOOL: dict[str, Any] = {
    "name": "compute_metrics",
    "description": (
        "Compute margins, free cash flow, year-over-year growth (and margin "
        "changes in basis points) and quarter-over-quarter revenue growth from "
        "raw reported figures. Every growth rate, margin and free-cash-flow "
        "figure in the memo must come from this tool's output. Ratios are "
        "returned as fractions; growth on a zero or negative base is null."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "current": PERIOD_SCHEMA,
            "prior_year": {
                **PERIOD_SCHEMA,
                "description": "Same period one year earlier. Omit if not given.",
            },
            "prior_quarter": {
                **PERIOD_SCHEMA,
                "description": "The immediately preceding quarter. Omit if not given.",
            },
        },
        "required": ["current"],
        "additionalProperties": False,
    },
}
