"""Paired comparison of two evaluation reports (SPEC.md "researchos.compare").

Five or fifteen cases cannot settle much, so the comparison carries a
percentile-bootstrap interval for the mean per-case difference instead of a
bare difference of means. Lower Brier is better throughout: `diff = b - a`,
so a negative diff means report `b` did better on that case.
"""

from __future__ import annotations

import math
import random

from tools.stats import quantile

from researchos.jsonio import read_json


def load_report(path) -> dict:
    """Read the dict `evaluate` returns, bare or wrapped as {"result": <that dict>, ...}.

    Returns the dict holding "cases". Raises ValueError if the file is not
    JSON (read with read_json, so a byte order mark or UTF-16 is fine) or
    holds neither form; OSError (e.g. FileNotFoundError) if it cannot be read
    at all.
    """
    data = read_json(path)
    if isinstance(data, dict) and "cases" in data:
        report = data
    elif (isinstance(data, dict) and isinstance(data.get("result"), dict)
          and "cases" in data["result"]):
        report = data["result"]
    else:
        raise ValueError(f'{path}: not an evaluation report (no "cases" at the top level '
                         f'or under "result")')
    if not isinstance(report["cases"], list):
        raise ValueError(f'{path}: "cases" is not a list')
    return report


def _is_number(value) -> bool:
    """A finite int or float. A bool is not a number here, and nan/inf are no Brier score."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _scored(report: dict, metric: str, name: str) -> list[tuple[str, float]]:
    """(case_id, value) for each record scored on `metric`, in the report's order."""
    scored: list[tuple[str, float]] = []
    seen: set[str] = set()
    for record in report["cases"]:
        if not isinstance(record, dict) or not _is_number(record.get(metric)):
            continue  # a skipped record has no score
        case_id = record.get("case_id")
        if not isinstance(case_id, str):
            raise ValueError(f"report {name}: a record scored on {metric} has no case_id")
        if case_id in seen:
            raise ValueError(f"report {name}: case {case_id!r} is scored twice")
        seen.add(case_id)
        scored.append((case_id, float(record[metric])))
    return scored


def paired(a: dict, b: dict, metric: str = "brier_judge") -> list[tuple[str, float, float]]:
    """(case_id, a_value, b_value) for every case scored in both reports, in a["cases"] order.

    Raises ValueError if a case id appears twice among one report's scored records.
    """
    scored_a = _scored(a, metric, "a")
    scored_b = dict(_scored(b, metric, "b"))
    return [(case_id, value, scored_b[case_id])
            for case_id, value in scored_a if case_id in scored_b]


def bootstrap_ci(diffs, resamples=10000, seed=0, level=0.95) -> tuple[float, float] | None:
    """Percentile bootstrap interval for the mean of `diffs`; None when there are none."""
    if resamples < 1:
        raise ValueError(f"resamples must be at least 1, got {resamples!r}")
    if not 0 < level < 1:  # also rejects nan
        raise ValueError(f"level must be within (0, 1), got {level!r}")
    diffs = list(diffs)
    if not diffs:
        return None
    rng = random.Random(seed)
    k = len(diffs)
    means = []
    for _ in range(resamples):
        sample = rng.choices(diffs, k=k)
        means.append(sum(sample) / k)
    return (quantile(means, (1 - level) / 2), quantile(means, 1 - (1 - level) / 2))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def compare(a: dict, b: dict, metric: str = "brier_judge", resamples: int = 10000,
            seed: int = 0) -> dict:
    """Compare report `b` with report `a` case by case on a Brier metric (lower is better)."""
    if not isinstance(metric, str) or not metric.startswith("brier_"):
        raise ValueError(f"metric must be a Brier score (start with 'brier_'), got {metric!r}")
    pairs = paired(a, b, metric)
    cases = [{"case_id": case_id, "a": va, "b": vb, "diff": vb - va}
             for case_id, va, vb in pairs]
    diffs = [c["diff"] for c in cases]
    ci = bootstrap_ci(diffs, resamples=resamples, seed=seed)
    return {
        "metric": metric,
        "n": len(cases),
        "mean_a": _mean([c["a"] for c in cases]),
        "mean_b": _mean([c["b"] for c in cases]),
        "mean_diff": _mean(diffs),
        "ci_low": ci[0] if ci is not None else None,
        "ci_high": ci[1] if ci is not None else None,
        "b_better": sum(1 for d in diffs if d < 0),
        "a_better": sum(1 for d in diffs if d > 0),
        "ties": sum(1 for d in diffs if d == 0),
        "cases": cases,
    }
