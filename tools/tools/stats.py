"""Descriptive statistics and forecast scoring."""

import math
from collections.abc import Mapping, Sequence
from fractions import Fraction

Number = int | float


def mean(values: Sequence[Number | None]) -> float | None:
    """Return the arithmetic mean; None if empty or any value is None."""
    items = list(values)
    if not items or any(v is None for v in items):
        return None
    return sum(items) / len(items)


def stdev(values: Sequence[Number | None]) -> float | None:
    """Return the sample standard deviation (n - 1); None if fewer than two values or any is None."""
    items = list(values)
    if len(items) < 2 or any(v is None for v in items):
        return None
    avg = sum(items) / len(items)
    return math.sqrt(sum((v - avg) * (v - avg) for v in items) / (len(items) - 1))


def zscore(x: Number | None, values: Sequence[Number | None]) -> float | None:
    """Return (x - mean) / stdev; None if x is None or the stdev is None or 0."""
    if x is None:
        return None
    sd = stdev(values)
    if sd is None or sd == 0:
        return None
    return (x - mean(values)) / sd


def quantile(values: Sequence[Number | None], q: Number | None) -> float | None:
    """Return the q-quantile by linear interpolation; None if values is empty, q is None, or any value is None."""
    if q is None:
        return None
    if not 0 <= q <= 1:
        raise ValueError(f"q must be within [0, 1], got {q!r}")
    items = list(values)
    if not items or any(v is None for v in items):
        return None
    s = sorted(items)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def percentile_rank(x: Number | None, values: Sequence[Number | None]) -> float | None:
    """Return the mid-rank of x among values; None if values is empty, x is None, or any value is None."""
    items = list(values)
    if x is None or not items or any(v is None for v in items):
        return None
    below = sum(1 for v in items if v < x)
    equal = sum(1 for v in items if v == x)
    return (below + equal / 2) / len(items)


def winsorize(
    values: Sequence[Number | None], lower: Number = 0.05, upper: Number = 0.95
) -> list[Number] | None:
    """Return the values clipped to their lower and upper quantiles; None if any value is None."""
    if not 0 <= lower <= upper <= 1:
        raise ValueError(f"need 0 <= lower <= upper <= 1, got lower={lower!r}, upper={upper!r}")
    items = list(values)
    if not items:
        return []
    if any(v is None for v in items):
        return None
    lo = quantile(items, lower)
    hi = quantile(items, upper)
    return [min(max(v, lo), hi) for v in items]


def _check_forecasts(probs: Sequence[Number | None], outcomes: Sequence[Number | None]) -> bool:
    """Validate probability/outcome inputs; return False if any element is None."""
    if len(probs) != len(outcomes):
        raise ValueError(f"probs and outcomes differ in length: {len(probs)} != {len(outcomes)}")
    if not probs:
        raise ValueError("probs and outcomes must not be empty")
    if any(p is None for p in probs) or any(o is None for o in outcomes):
        return False
    for p in probs:
        if not 0 <= p <= 1:
            raise ValueError(f"probabilities must be within [0, 1], got {p!r}")
    return True


def brier_score(probs: Sequence[Number | None], outcomes: Sequence[Number | None]) -> float | None:
    """Return the mean squared error of the probabilities; None if any probability or outcome is None."""
    probs, outcomes = list(probs), list(outcomes)
    if not _check_forecasts(probs, outcomes):
        return None
    return sum((p - o) * (p - o) for p, o in zip(probs, outcomes)) / len(probs)


def log_loss(
    probs: Sequence[Number | None], outcomes: Sequence[Number | None], eps: float = 1e-15
) -> float | None:
    """Return the mean log loss with probabilities clipped to [eps, 1 - eps]; None if any probability or outcome is None."""
    probs, outcomes = list(probs), list(outcomes)
    if not _check_forecasts(probs, outcomes):
        return None
    total = 0.0
    for p, o in zip(probs, outcomes):
        p = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(probs)


def directional_accuracy(
    predicted: Sequence[Number | None], actual: Sequence[Number | None]
) -> float | None:
    """Return the fraction of pairs (both non-zero, non-None) whose signs agree; None if no such pair exists."""
    predicted, actual = list(predicted), list(actual)
    if len(predicted) != len(actual):
        raise ValueError(
            f"predicted and actual differ in length: {len(predicted)} != {len(actual)}"
        )
    pairs = [
        (p, a)
        for p, a in zip(predicted, actual)
        if p is not None and a is not None and p != 0 and a != 0
    ]
    if not pairs:
        return None
    agree = sum(1 for p, a in pairs if (p > 0) == (a > 0))
    return agree / len(pairs)


def _bin_index(p: Number, bins: int) -> int:
    """Return the bin i with i/bins <= p < (i+1)/bins, the last bin also holding 1.0."""
    i = min(int(p * bins), bins - 1)
    # Correct for floating-point error in p * bins so bins match their stated edges.
    while i > 0 and p < i / bins:
        i -= 1
    while i < bins - 1 and p >= (i + 1) / bins:
        i += 1
    return i


def calibration_table(
    probs: Sequence[Number | None], outcomes: Sequence[Number | None], bins: int = 10
) -> list[dict[str, float | int]] | None:
    """Return one summary dict per non-empty probability bin; None if any probability or outcome is None."""
    if bins < 1:
        raise ValueError(f"bins must be at least 1, got {bins!r}")
    probs, outcomes = list(probs), list(outcomes)
    if not _check_forecasts(probs, outcomes):
        return None
    groups: dict[int, list[tuple[Number, Number]]] = {}
    for p, o in zip(probs, outcomes):
        groups.setdefault(_bin_index(p, bins), []).append((p, o))
    table = []
    for i in sorted(groups):
        members = groups[i]
        table.append(
            {
                "lower": i / bins,
                "upper": (i + 1) / bins,
                "count": len(members),
                "mean_prob": sum(p for p, _ in members) / len(members),
                "frequency": sum(o for _, o in members) / len(members),
            }
        )
    return table


def hedge_update(
    weights: Mapping[str, Number], losses: Mapping[str, Number | None], eta: Number = 0.5
) -> dict[str, float]:
    """Return the multiplicatively updated, normalized weights; never None."""
    for key, w in weights.items():
        if w < 0:
            raise ValueError(f"weights must be non-negative, got {w!r} for {key!r}")
    if not weights:
        return {}
    # Work in log space so large losses neither overflow nor underflow to all zeros.
    logs: dict[str, float] = {}
    for key, w in weights.items():
        loss = losses.get(key)
        if loss is None:
            loss = 0
        logs[key] = -math.inf if w == 0 else math.log(w) - eta * loss
    top = max(logs.values())
    if top == -math.inf:
        return {key: 1 / len(weights) for key in weights}
    scaled = {key: math.exp(v - top) for key, v in logs.items()}
    total = sum(scaled.values())
    return {key: v / total for key, v in scaled.items()}


def combine_probabilities(
    probs: Mapping[str, Number | None], weights: Mapping[str, Number | None]
) -> float | None:
    """Return the weighted average of probs over shared keys with positive weight; None if that total weight is 0."""
    total_weight = 0.0
    total = 0.0
    for key, p in probs.items():
        w = weights.get(key)
        if p is None or w is None or w <= 0:
            continue
        total_weight += w
        total += w * p
    if total_weight == 0:
        return None
    return total / total_weight


def is_number(value: object) -> bool:
    """Return True if value is an int (not a bool) or a finite float, else False; never None."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        # An int is always finite; math.isfinite would overflow on a huge one.
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _check_eps(eps: object) -> None:
    """Raise ValueError unless eps is a number with 0 < eps < 0.5."""
    if not is_number(eps) or not 0 < eps < 0.5:
        raise ValueError(f"eps must be a number with 0 < eps < 0.5, got {eps!r}")


def logit(p: Number, eps: Number = 1e-6) -> float:
    """Return the log-odds of p clipped to [eps, 1 - eps]; never None (bad input raises ValueError)."""
    if not is_number(p) or not 0 <= p <= 1:
        raise ValueError(f"p must be a number within [0, 1], got {p!r}")
    _check_eps(eps)
    q = min(max(p, eps), 1 - eps)
    return math.log(q / (1 - q))


def sigmoid(x: Number) -> float:
    """Return 1 / (1 + exp(-x)) without overflow; never None (a non-number raises ValueError)."""
    if not is_number(x):
        raise ValueError(f"x must be a number, got {x!r}")
    try:
        x = float(x)
    except OverflowError:
        # An int too large for a float: the sigmoid has saturated.
        return 1.0 if x > 0 else 0.0
    if x < 0:
        e = math.exp(x)
        return e / (1 + e)
    return 1 / (1 + math.exp(-x))


def pool_log_odds(
    probs: Mapping[str, object],
    weights: Mapping[str, object] | None = None,
    eps: Number = 1e-6,
) -> float | None:
    """Return the sigmoid of the (weighted) sum of log-odds of the usable probabilities; None if no key is used."""
    _check_eps(eps)
    if weights is None:
        used = [p for p in probs.values() if is_number(p)]
        if not used:
            return None
        return sigmoid(sum(logit(p, eps) for p in used))
    pairs = []
    for key, p in probs.items():
        if not is_number(p):
            continue
        w = weights.get(key)
        if not is_number(w) or w <= 0:
            continue
        pairs.append((p, w))
    if not pairs:
        return None
    m = len(pairs)
    coefs: list[float]
    try:
        total = sum(w for _, w in pairs)
        if not math.isfinite(total):
            raise OverflowError("total weight overflows a float")
        coefs = [m * w / total for _, w in pairs]
        if not all(math.isfinite(c) for c in coefs):
            raise OverflowError("coefficient overflows a float")
    except OverflowError:
        # Weights too large for float arithmetic: scale them exactly instead.
        exact_total = sum(Fraction(w) for _, w in pairs)
        coefs = [float(m * Fraction(w) / exact_total) for _, w in pairs]
    return sigmoid(sum(c * logit(p, eps) for c, (p, _) in zip(coefs, pairs)))
