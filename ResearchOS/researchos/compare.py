"""Paired comparison of two evaluation reports (SPEC.md "researchos.compare").

Five or fifteen cases cannot settle much, so the comparison carries a
percentile-bootstrap interval for the mean per-case difference instead of a
bare difference of means. Lower Brier is better throughout: `diff = b - a`,
so a negative diff means report `b` did better on that case.

A report is what `evaluate` returns or what `train` prints (a list of
records); `merge_reports` joins several into one side of a comparison (v9),
e.g. walk-forward training's one `train` output per case.
"""

from __future__ import annotations

import copy
import math
import random

from tools.stats import quantile

from researchos.jsonio import read_json


_NOT_A_REPORT = "not an evaluation report or training output"


def _json_kind(value) -> str:
    """How a JSON value is named in an error message: "a number", "null", ..."""
    if value is None:
        return "null"
    for types, kind in ((bool, "a boolean"), (dict, "an object"), (list, "a list"),
                        (str, "a string"), ((int, float), "a number")):
        if isinstance(value, types):
            return kind
    return type(value).__name__


def load_report(path) -> dict:
    """Read an evaluation report or training output.

    Accepted forms: the dict `evaluate` returns (it holds "cases"), returned
    as it is; the list of records `train` prints, returned as
    {"cases": <that list>}; either one wrapped as {"result": <it>, ...}. In
    every form the cases must be a list of objects. Raises ValueError naming
    path if the file is not JSON (read with read_json, so a byte order mark or
    UTF-16 is fine) or not one of these forms; OSError (e.g.
    FileNotFoundError) if it cannot be read at all.
    """
    data = read_json(path)
    inner = data.get("result") if isinstance(data, dict) else None
    if isinstance(data, dict) and "cases" in data:
        report = data
    elif isinstance(data, list):
        report = {"cases": data}
    elif isinstance(inner, dict) and "cases" in inner:
        report = inner
    elif isinstance(inner, list):
        report = {"cases": inner}
    else:
        raise ValueError(f'{path}: {_NOT_A_REPORT} (no "cases" and no list of records, '
                         f'at the top level or under "result")')
    cases = report["cases"]
    if not isinstance(cases, list):
        raise ValueError(f'{path}: {_NOT_A_REPORT} ("cases" is {_json_kind(cases)}, '
                         f'not a list)')
    for i, record in enumerate(cases):
        if not isinstance(record, dict):
            raise ValueError(f"{path}: {_NOT_A_REPORT} (case record {i} is "
                             f"{_json_kind(record)}, not an object)")
    return report


def merge_reports(reports) -> dict:
    """{"cases": [...]} holding the cases of every report in turn, in order.

    {"cases": []} for no reports. Each report is a dict holding a list
    "cases" (as load_report returns it), else ValueError. The records are
    copied, so the reports passed in are not modified, neither here nor
    through later changes to the result. A case scored in two of the reports
    is scored twice in the result, which `paired` rejects.
    """
    cases: list = []
    for i, report in enumerate(reports):
        if not isinstance(report, dict) or not isinstance(report.get("cases"), list):
            raise ValueError(f'report {i}: not a report (no list of "cases")')
        cases.extend(copy.deepcopy(report["cases"]))
    return {"cases": cases}


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
