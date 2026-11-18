"""The secondary endpoints of PLAN.md, scored today against target series fetched today.

    python secondary.py

Run only once both arms are finished (each has sealed or failed every snapshot, or stopped
at its cap), and not before 2026-11-02. Everything it builds goes to scores/<today>/, which
is not committed: the ten target series fetched today, a copy of each arm's sealed
snapshots (case.json and prediction.json; the arms themselves are not touched), resolved
there, and the outputs of score, compare, blend and agreement, which carry series levels.
What it writes to secondary-<today>.json holds no level and is committed:

- per arm, score's summary (n, pending, the Brier scores and accuracies);
- per arm, by horizon and by target, the Brier score of the judge, the weighted and the
  uniform forecast, and of three baselines: a coin (0.25); trend, 0.6 when the key's
  trailing change over the same horizon is above 0, 0.4 when below, 0.5 when it is 0 or
  unknown; mean reversion, the mirror of trend;
- compare --metric brier_judge, holistic (a) against isolated (b), without its per-key rows,
  once resampling keys and once resampling whole snapshots (--resample cases; the PLAN's
  amendment of 2026-11-13);
- every blend rule with --perspectives macro on each arm, without its per-key rows;
- agreement --perspectives macro on each arm;
- the snapshots any of whose calls was served by another model (forecast_arm.py);
- the snapshots not scored: PLAN.md scores no snapshot whose case.json does not match
  inputs.sha256, as a case rebuilt after the first may not. One that does not match in
  either arm is left out of both, so the arms are scored on the same snapshots.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import forecast_arm  # noqa: E402  (puts researchos and tools on the path)
import prepare  # noqa: E402
from researchos.__main__ import main as researchos_main  # noqa: E402

FIRST_SCORING = date(2026, 11, 2)
RULES = ("uniform", "median", "hedge", "leader", "pool")


def _cli(args: list[str]) -> dict:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = researchos_main(args)
    if code:
        raise SystemExit(f"{args[0]} exited {code}: {err.getvalue().strip()}")
    return json.loads(out.getvalue()) if out.getvalue().strip() else {}


def finished(arm: str) -> bool:
    _, _, left = forecast_arm._state(arm)
    return not left or forecast_arm.spent(arm) + forecast_arm.most_per_snapshot() > forecast_arm.CAP


def _trend(record: dict, sign: int) -> float:
    change = record.get("trailing_change")
    if not isinstance(change, (int, float)) or change == 0:
        return 0.5
    return 0.5 + 0.1 * sign * (1 if change > 0 else -1)


def scoring_sets() -> tuple[dict[str, list[str]], list[str]]:
    """Per arm, the sealed snapshots to score; and the snapshots not scored, those sealed
    in an arm whose case.json there does not match inputs.sha256, left out of both arms."""
    digests = {Path(rel).parent.name: digest for rel, digest in forecast_arm._hashes().items()}
    sealed = {arm: forecast_arm._state(arm)[0] for arm in forecast_arm.ARMS}
    not_scored = sorted({case_id for arm, ids in sealed.items() for case_id in ids
                         if not forecast_arm._matches(
                             forecast_arm.arm_dir(arm) / case_id / "case.json",
                             digests[case_id])})
    return ({arm: [c for c in ids if c not in not_scored] for arm, ids in sealed.items()},
            not_scored)


def summarize(records: list[dict]) -> dict:
    def mean(values):
        return sum(values) / len(values)

    ys = [1.0 if r["actual_up"] else 0.0 for r in records]
    return {"n": len(records),
            "judge": mean([r["brier_judge"] for r in records]),
            "weighted": mean([r["brier_weighted"] for r in records]),
            "uniform": mean([r["brier_uniform"] for r in records]),
            "coin": 0.25,
            "trend": mean([(_trend(r, 1) - y) ** 2 for r, y in zip(records, ys)]),
            "mean_reversion": mean([(_trend(r, -1) - y) ** 2 for r, y in zip(records, ys)])}


def table(records: list[dict]) -> dict:
    out = {"all": summarize(records) if records else None}
    for field in ("horizon", "target"):
        groups: dict[str, list[dict]] = {}
        for record in records:
            groups.setdefault(record[field], []).append(record)
        out[f"by_{field}"] = {key: summarize(group) for key, group in sorted(groups.items())}
    return out


def main() -> int:
    today = datetime.now(timezone.utc).date()
    if today < FIRST_SCORING:
        raise SystemExit(f"the first scoring is on {FIRST_SCORING} (PLAN.md)")
    unfinished = [arm for arm in forecast_arm.ARMS if not finished(arm)]
    if unfinished:
        raise SystemExit(f"not finished: {', '.join(unfinished)}; run forecast_arm.py all first")
    work = HERE / "scores" / today.isoformat()
    if work.exists():
        raise SystemExit(f"{work} exists: one scoring a day")
    series = work / "series"
    series.mkdir(parents=True)
    config = json.loads(prepare.CONFIG.read_text(encoding="utf-8"))
    for target in config["targets"]:
        prepare.fetch_fred(target["id"], folder=series)

    to_score, not_scored = scoring_sets()
    summary = {"scored_on": today.isoformat(), "arms": {},
               "served_by_another_model": sorted(set().union(
                   *(forecast_arm.fallbacks(arm) for arm in forecast_arm.ARMS))),
               "not_scored": {"snapshots": not_scored,
                              "reason": "case.json does not match inputs.sha256 (PLAN.md)"}}
    scores = {}
    for arm in forecast_arm.ARMS:
        sealed = to_score[arm]
        root = work / f"arm-{arm}"
        for case_id in sealed:
            (root / case_id).mkdir(parents=True)
            for name in ("case.json", "prediction.json"):
                shutil.copy(forecast_arm.arm_dir(arm) / case_id / name, root / case_id / name)
        ids = ",".join(sealed)
        _cli(["resolve", "--root", str(root), "--ids", ids, "--series-dir", str(series)])
        scores[arm] = work / f"score_{arm}.json"
        _cli(["score", "--root", str(root), "--ids", ids, "--out", str(scores[arm])])
        report = json.loads(scores[arm].read_text(encoding="utf-8"))
        summary["arms"][arm] = {
            "snapshots": len(sealed),
            "score": {k: (len(v) if k == "pending" else v) for k, v in report.items()
                      if k != "cases"},
            "brier": table(report["cases"]),
            "blend": {},
            "agreement": _cli(["agreement", str(scores[arm]), "--perspectives", "macro"]),
        }
        for rule in RULES:
            blended = _cli(["blend", str(scores[arm]), "--rule", rule, "--perspectives", "macro"])
            summary["arms"][arm]["blend"][rule] = {k: v for k, v in blended.items()
                                                   if k != "cases"}
    compared = _cli(["compare", "-a", str(scores["holistic"]), "-b", str(scores["isolated"]),
                     "--metric", "brier_judge"])
    summary["compare_brier_judge_holistic_a_isolated_b"] = {k: v for k, v in compared.items()
                                                            if k != "cases"}
    by_snapshot = _cli(["compare", "-a", str(scores["holistic"]), "-b", str(scores["isolated"]),
                        "--metric", "brier_judge", "--resample", "cases"])
    summary["compare_brier_judge_holistic_a_isolated_b_by_snapshot"] = {
        k: v for k, v in by_snapshot.items() if k != "cases"}
    path = HERE / f"secondary-{today.isoformat()}.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    counts = [f"{arm} {entry['score']['n']} keys" for arm, entry in summary["arms"].items()]
    print(f"wrote {path.name}: scored {', '.join(counts)}")
    if not_scored:
        print(f"not scored, as not matching inputs.sha256: {', '.join(not_scored)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
