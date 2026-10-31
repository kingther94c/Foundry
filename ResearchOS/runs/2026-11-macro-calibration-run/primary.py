"""The primary endpoints of PLAN.md, from the sealed forecasts alone: no outcome is read.

    python primary.py [--out primary.json]

Reads <arm>/<case>/prediction.json for the two arms (arm-holistic, arm-isolated) and
reports, per arm: which snapshots and keys were sealed; the mean pairwise correlation of
the ten perspectives' probabilities over every (snapshot, key) forecast; each
perspective's standard deviation and how often it leaves 0.50; how often the judge's
probability leaves [0.45, 0.55], by horizon. The difference in mean correlation (isolated
minus holistic) gets a 95% percentile interval from 2,000 resamples of the snapshots sealed
in both arms (a cluster bootstrap: a snapshot's keys move together), random.Random(0).
"""

from __future__ import annotations

import argparse
import json
import math
import random
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = {"holistic": HERE / "arm-holistic", "isolated": HERE / "arm-isolated"}
RESAMPLES = 2000
NEUTRAL = (0.45, 0.55)


def load(root: Path) -> dict[str, dict]:
    """case id -> its sealed forecast, for every case folder holding one."""
    found = {}
    for path in sorted(root.glob("*/prediction.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        found[path.parent.name] = data
    return found


def rows_of(forecast: dict) -> list[dict]:
    """One row per key: each perspective's probability, the judge's, the key's horizon."""
    rows = []
    for key in forecast["keys"]:
        rows.append({"key": key, "horizon": key.rsplit("_", 1)[1],
                     "probs": {name: forecast["views"][name]["probs"][key]
                               for name in forecast["perspectives"]},
                     "judge": forecast["prob_judge"][key]})
    return rows


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None  # a perspective that never moves has no correlation
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def mean_correlation(rows: list[dict], names: list[str]) -> float | None:
    values = []
    for a, b in combinations(names, 2):
        r = _pearson([row["probs"][a] for row in rows], [row["probs"][b] for row in rows])
        if r is not None:
            values.append(r)
    return sum(values) / len(values) if values else None


def spread(rows: list[dict], names: list[str]) -> dict:
    out = {}
    for name in names:
        ps = [row["probs"][name] for row in rows]
        mean = sum(ps) / len(ps)
        out[name] = {"sd_prob": math.sqrt(sum((p - mean) ** 2 for p in ps) / len(ps)),
                     "leaves_half": sum(1 for p in ps if p != 0.5) / len(ps)}
    return out


def judge_edge(rows: list[dict]) -> dict:
    out = {}
    for horizon in sorted({row["horizon"] for row in rows}):
        js = [row["judge"] for row in rows if row["horizon"] == horizon]
        out[horizon] = sum(1 for p in js if not NEUTRAL[0] <= p <= NEUTRAL[1]) / len(js)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out")
    args = p.parse_args()
    sealed = {arm: load(root) for arm, root in ARMS.items()}
    both = sorted(set(sealed["holistic"]) & set(sealed["isolated"]))
    report = {"snapshots_in_both_arms": len(both), "arms": {}}
    names = None
    for arm, forecasts in sealed.items():
        rows = [row for case_id in both for row in rows_of(forecasts[case_id])]
        if names is None and both:
            names = list(forecasts[both[0]]["perspectives"])
        report["arms"][arm] = {
            "snapshots_sealed": sorted(forecasts),
            "keys_sealed": sum(len(f["keys"]) for f in forecasts.values()),
            "mean_correlation": mean_correlation(rows, names) if rows else None,
            "perspectives": spread(rows, names) if rows else {},
            "judge_outside_neutral": judge_edge(rows) if rows else {},
        }
    if both:
        by_case = {arm: {c: rows_of(sealed[arm][c]) for c in both} for arm in sealed}
        rng = random.Random(0)
        diffs = []
        for _ in range(RESAMPLES):
            pick = [rng.choice(both) for _ in both]
            means = {arm: mean_correlation([r for c in pick for r in by_case[arm][c]], names)
                     for arm in by_case}
            if None not in means.values():
                diffs.append(means["isolated"] - means["holistic"])
        diffs.sort()
        point = (report["arms"]["isolated"]["mean_correlation"]
                 - report["arms"]["holistic"]["mean_correlation"])
        low = diffs[int(0.025 * len(diffs))] if diffs else None
        high = diffs[min(int(0.975 * len(diffs)), len(diffs) - 1)] if diffs else None
        report["difference_isolated_minus_holistic"] = {
            "point": point, "ci95": [low, high], "resamples_used": len(diffs),
            "isolated_disagrees_more": high is not None and high < 0}
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
