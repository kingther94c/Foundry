"""The primary endpoint of PLAN.md and the noise floor, from the files in this directory.

Run from ResearchOS/ with its virtual environment:
    python runs/2026-10-perspectives-run/primary.py
"""
import json
import math
import random
import statistics
from itertools import combinations
from pathlib import Path

from researchos.compare import load_report
from researchos.perspectives import PERSPECTIVES

HERE = Path(__file__).parent


def scored(path):
    return {r["case_id"]: r for r in load_report(path)["cases"] if "brier_by_perspective" in r}


def mean_corr(rows):
    """Mean Pearson correlation over the perspective pairs defined on these rows."""
    cols = {p: [r["prob_by_perspective"][p] for r in rows] for p in PERSPECTIVES}
    values = []
    for a, b in combinations(PERSPECTIVES, 2):
        if statistics.pstdev(cols[a]) == 0 or statistics.pstdev(cols[b]) == 0:
            continue
        try:
            values.append(statistics.correlation(cols[a], cols[b]))
        except statistics.StatisticsError:
            continue
    return sum(values) / len(values) if values else None


H = scored(HERE / "eval_holistic.json")
I = scored(HERE / "eval_isolated.json")
ids = [c for c in H if c in I]
h_rows, i_rows = [H[c] for c in ids], [I[c] for c in ids]
rng = random.Random(0)
draws = []
for _ in range(2000):
    pick = [rng.randrange(len(ids)) for _ in ids]
    mh, mi = mean_corr([h_rows[k] for k in pick]), mean_corr([i_rows[k] for k in pick])
    if mh is not None and mi is not None:
        draws.append(mi - mh)
draws.sort()
primary = {"n": len(ids), "mean_corr_holistic": mean_corr(h_rows), "mean_corr_isolated": mean_corr(i_rows),
           "diff_isolated_minus_holistic": mean_corr(i_rows) - mean_corr(h_rows),
           "ci95": [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]],
           "resamples_used": len(draws)}


def judge_prob(r):  # the judge's probability, recovered from its Brier score
    b = r["brier_judge"]
    return 1 - math.sqrt(b) if r["actual_up"] else math.sqrt(b)


old = scored(HERE.parent / "2026-10-smallbank-run" / "eval_untrained.json")
both = [c for c in old if c in H]
noise = {"n": len(both),
         "judge_corr": statistics.correlation([judge_prob(old[c]) for c in both],
                                              [judge_prob(H[c]) for c in both]),
         "judge_mean_abs_diff": sum(abs(judge_prob(old[c]) - judge_prob(H[c])) for c in both) / len(both),
         "perspective_corr": {p: statistics.correlation([old[c]["prob_by_perspective"][p] for c in both],
                                                        [H[c]["prob_by_perspective"][p] for c in both])
                              for p in PERSPECTIVES}}
print(json.dumps({"primary": primary, "noise_floor": noise}, indent=1))
