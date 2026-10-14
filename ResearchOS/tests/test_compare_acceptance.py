"""Hidden acceptance tests for researchos.compare -- never shown to an attempt.

Each case traces to the "researchos.compare" section of SPEC.md.
"""

from __future__ import annotations

import json
import random

import pytest
from tools.stats import quantile

from researchos import compare as cmp


def rec(case_id, judge=None, weighted=None, **extra):
    r = {"case_id": case_id, "split": "eval", **extra}
    if judge is not None:
        r["brier_judge"] = judge
    if weighted is not None:
        r["brier_weighted"] = weighted
    return r


A = {"n": 4, "cases": [rec("c1", 0.20, 0.30), rec("c2", 0.10, 0.10), {"case_id": "c3", "skipped": "identifiable"},
                       rec("c4", 0.40, 0.25), rec("c5", 0.30, 0.30)]}
B = {"n": 4, "cases": [rec("c5", 0.30, 0.20), rec("c4", 0.10, 0.25), rec("c3", 0.50, 0.50), rec("c1", 0.25, 0.20),
                       rec("c9", 0.10, 0.10)]}


def test_load_report_both_forms(tmp_path):
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(A), encoding="utf-8")
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"result": B, "usage": {}}), encoding="utf-8")
    assert cmp.load_report(plain) == A
    assert cmp.load_report(wrapped) == B


@pytest.mark.parametrize("content", [{"result": [1, 2]}, {"n": 3}, [1, 2], {"result": {"n": 1}}])
def test_load_report_rejects(tmp_path, content):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError):
        cmp.load_report(p)


def test_paired_order_and_skips():
    assert cmp.paired(A, B) == [("c1", 0.20, 0.25), ("c4", 0.40, 0.10), ("c5", 0.30, 0.30)]
    assert cmp.paired(A, B, "brier_weighted") == [("c1", 0.30, 0.20), ("c4", 0.25, 0.25), ("c5", 0.30, 0.20)]


def test_paired_ignores_bools_and_missing_metric():
    a = {"cases": [rec("x", True), rec("y", 0.2)]}
    b = {"cases": [rec("x", 0.1), rec("y", None, 0.3)]}
    assert cmp.paired(a, b) == []


def test_paired_duplicate_ids():
    dup = {"cases": [rec("c1", 0.1), rec("c1", 0.2)]}
    with pytest.raises(ValueError):
        cmp.paired(dup, B)
    with pytest.raises(ValueError):
        cmp.paired(B, dup)
    # A duplicate among skipped records is not a scored duplicate.
    ok = {"cases": [{"case_id": "c1", "skipped": "identifiable"}, rec("c1", 0.1)]}
    assert cmp.paired(ok, B) == [("c1", 0.1, 0.25)]


def reference_ci(diffs, resamples, seed, level):
    rng = random.Random(seed)
    means = [sum(s) / len(s) for s in (rng.choices(diffs, k=len(diffs)) for _ in range(resamples))]
    return quantile(means, (1 - level) / 2), quantile(means, 1 - (1 - level) / 2)


def test_bootstrap_ci_matches_the_algorithm():
    diffs = [0.05, -0.30, 0.0, -0.02, 0.11]
    got = cmp.bootstrap_ci(diffs, resamples=2000, seed=7, level=0.9)
    assert got == pytest.approx(reference_ci(diffs, 2000, 7, 0.9))
    assert cmp.bootstrap_ci(diffs) == pytest.approx(reference_ci(diffs, 10000, 0, 0.95))
    assert cmp.bootstrap_ci([]) is None
    lo, hi = cmp.bootstrap_ci([0.1], resamples=50)
    assert lo == pytest.approx(0.1) and hi == pytest.approx(0.1)


@pytest.mark.parametrize("kwargs", [{"resamples": 0}, {"level": 0.0}, {"level": 1.0}, {"level": 1.5}])
def test_bootstrap_ci_rejects(kwargs):
    with pytest.raises(ValueError):
        cmp.bootstrap_ci([0.1, 0.2], **kwargs)


def test_compare():
    out = cmp.compare(A, B, resamples=500, seed=3)
    assert out["metric"] == "brier_judge" and out["n"] == 3
    assert out["mean_a"] == pytest.approx((0.20 + 0.40 + 0.30) / 3)
    assert out["mean_b"] == pytest.approx((0.25 + 0.10 + 0.30) / 3)
    diffs = [0.25 - 0.20, 0.10 - 0.40, 0.30 - 0.30]
    assert out["mean_diff"] == pytest.approx(sum(diffs) / 3)
    lo, hi = reference_ci(diffs, 500, 3, 0.95)
    assert out["ci_low"] == pytest.approx(lo) and out["ci_high"] == pytest.approx(hi)
    assert (out["b_better"], out["a_better"], out["ties"]) == (1, 1, 1)
    assert [c["case_id"] for c in out["cases"]] == ["c1", "c4", "c5"]
    assert out["cases"][1] == {"case_id": "c4", "a": 0.40, "b": 0.10, "diff": pytest.approx(-0.30)}


def test_compare_no_pairs_and_bad_metric():
    out = cmp.compare({"cases": []}, B)
    assert out["n"] == 0 and out["mean_a"] is None and out["mean_diff"] is None
    assert out["ci_low"] is None and out["ci_high"] is None
    assert (out["b_better"], out["a_better"], out["ties"]) == (0, 0, 0) and out["cases"] == []
    with pytest.raises(ValueError):
        cmp.compare(A, B, metric="correct_judge")


def test_cli_compare(tmp_path, capsys):
    from researchos.__main__ import main
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(A), encoding="utf-8")
    b.write_text(json.dumps({"result": B}), encoding="utf-8")
    assert main(["compare", str(a), str(b), "--metric", "brier_weighted", "--resamples", "300",
                 "--seed", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == json.loads(json.dumps(cmp.compare(A, B, metric="brier_weighted", resamples=300, seed=5)))
    assert main(["compare", str(a), str(b), "--metric", "accuracy_judge"]) == 2
    assert main(["compare", str(a), str(tmp_path / "missing.json")]) == 1
    assert capsys.readouterr().err.strip()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"n": 1}), encoding="utf-8")
    assert main(["compare", str(a), str(bad)]) == 1
