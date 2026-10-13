"""Price returns, drawdowns and volatility."""

import datetime
import math
from collections.abc import Sequence

Number = int | float


def simple_return(p0: Number | None, p1: Number | None) -> float | None:
    """Return p1 / p0 - 1; None if either is None or p0 <= 0."""
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1


def log_return(p0: Number | None, p1: Number | None) -> float | None:
    """Return ln(p1 / p0); None if either is None or either is <= 0."""
    if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
        return None
    return math.log(p1 / p0)


def compound(returns: Sequence[Number | None]) -> float | None:
    """Return prod(1 + r) - 1 (0.0 when empty); None if any return is None."""
    total = 1.0
    for r in returns:
        if r is None:
            return None
        total *= 1 + r
    return total - 1


def abnormal_return(
    stock_return: Number | None, market_return: Number | None, beta: Number | None = 1.0
) -> float | None:
    """Return stock_return - beta * market_return; None if any input is None."""
    if stock_return is None or market_return is None or beta is None:
        return None
    return stock_return - beta * market_return


def event_return(
    closes: Sequence[tuple[datetime.date, Number | None]],
    event_date: datetime.date,
    after: int = 1,
) -> float | None:
    """Return the simple return from the last close before event_date to the after-th close on or after it; None if either close is missing or after < 1."""
    if after < 1:
        return None
    ordered = sorted(closes, key=lambda pair: pair[0])
    before = [close for day, close in ordered if day < event_date]
    on_or_after = [close for day, close in ordered if day >= event_date]
    if not before or len(on_or_after) < after:
        return None
    return simple_return(before[-1], on_or_after[after - 1])


def max_drawdown(prices: Sequence[Number | None]) -> float | None:
    """Return the largest peak-to-trough decline as a fraction <= 0; None if any price is None."""
    items = list(prices)
    if any(p is None for p in items):
        return None
    for p in items:
        if p <= 0:
            raise ValueError(f"prices must be positive, got {p!r}")
    worst = 0.0
    peak = None
    for p in items:
        if peak is None or p > peak:
            peak = p
            continue
        drawdown = p / peak - 1
        if drawdown < worst:
            worst = drawdown
    return worst


def annualized_volatility(
    returns: Sequence[Number | None], periods_per_year: Number = 252
) -> float | None:
    """Return the sample standard deviation times sqrt(periods_per_year); None if fewer than two returns or any is None."""
    items = list(returns)
    if len(items) < 2 or any(r is None for r in items):
        return None
    avg = sum(items) / len(items)
    variance = sum((r - avg) * (r - avg) for r in items) / (len(items) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)
