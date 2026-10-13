"""Financial ratios and growth calculations."""

from collections.abc import Sequence

Number = int | float


def safe_div(a: Number | None, b: Number | None) -> float | None:
    """Return a / b; None if either is None or b == 0."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def growth(current: Number | None, base: Number | None) -> float | None:
    """Return (current - base) / base; None if either is None or base <= 0."""
    if current is None or base is None or base <= 0:
        return None
    return (current - base) / base


def cagr(start: Number | None, end: Number | None, years: Number | None) -> float | None:
    """Return the compound annual growth rate; None if any input is None, start <= 0, end < 0, years <= 0, or it overflows."""
    if start is None or end is None or years is None:
        return None
    if start <= 0 or end < 0 or years <= 0:
        return None
    try:
        return (end / start) ** (1 / years) - 1
    except OverflowError:
        return None


def margin(numerator: Number | None, revenue: Number | None) -> float | None:
    """Return numerator / revenue; None if either is None or revenue == 0."""
    return safe_div(numerator, revenue)


def change_bps(current: Number | None, prior: Number | None) -> float | None:
    """Return the change from prior to current in basis points; None if either is None."""
    if current is None or prior is None:
        return None
    return (current - prior) * 10000


def free_cash_flow(operating_cash_flow: Number | None, capex: Number | None) -> Number | None:
    """Return operating_cash_flow - abs(capex); None if either is None."""
    if operating_cash_flow is None or capex is None:
        return None
    return operating_cash_flow - abs(capex)


def ttm(values: Sequence[Number | None]) -> Number | None:
    """Return the sum of the last four values; None if fewer than four or any of them is None."""
    items = list(values)
    if len(items) < 4:
        return None
    last = items[-4:]
    if any(v is None for v in last):
        return None
    return sum(last)


def yoy_growth_series(values: Sequence[Number | None], lag: int = 4) -> list[float | None]:
    """Return growth versus lag periods earlier for each element; an element is None when it cannot be computed."""
    if lag < 1:
        raise ValueError(f"lag must be at least 1, got {lag!r}")
    items = list(values)
    return [growth(items[i], items[i - lag]) if i >= lag else None for i in range(len(items))]


def surprise(actual: Number | None, estimate: Number | None) -> float | None:
    """Return (actual - estimate) / abs(estimate); None if either is None or estimate == 0."""
    if actual is None or estimate is None or estimate == 0:
        return None
    return (actual - estimate) / abs(estimate)


def beat_miss(actual: Number | None, estimate: Number | None, tolerance: Number = 0.01) -> str | None:
    """Return "beat", "miss" or "in_line" by the surprise versus tolerance; None if the surprise is None."""
    s = surprise(actual, estimate)
    if s is None:
        return None
    if s > tolerance:
        return "beat"
    if s < -tolerance:
        return "miss"
    return "in_line"
