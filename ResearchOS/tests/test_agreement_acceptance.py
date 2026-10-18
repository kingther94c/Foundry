"""Acceptance tests for "Do the perspectives disagree? (v19)", written from SPEC.md independently
of the code (held back from the agent that built it)."""

from __future__ import annotations

import json
import math

import pytest

from researchos.blend import agreement
from researchos.perspectives import PERSPECTIVES


def rec(case_id, up, probs, event_date="2026-07-30", **extra):
    full = {p: 0.5 for p in PERSPECTIVES}
    full.update(probs)
    y = 1.0 if up else 0.0
    return {"case_id": case_id, "event_date": event_date, "actual_up": up,
            "prob_by_perspective": full,
            "brier_by_perspective": {p: (v - y) ** 2 for p, v in full.items()}, **extra}


def pair(result, a, b):
    return next(p for p in result["pairs"] if p["a"] == a and p["b"] == b)["correlation"]


def test_shape_and_order():
    r = agreement([rec("c1", True, {"growth": 0.7}), rec("c2", False, {"growth": 0.3})])
    assert r["n"] == 2
    assert list(r["perspectives"]) == PERSPECTIVES
    assert set(r["perspectives"]["growth"]) == {"mean_prob", "sd_prob", "brier", "calls", "hits"}
    assert [(p["a"], p["b"]) for p in r["pairs"]] == [(a, b) for i, a in enumerate(PERSPECTIVES)
                                                        for b in PERSPECTIVES[i + 1:]]
    assert len(r["pairs"]) == 21


def test_correlations():
    recs = [rec("c1", True, {"growth": 0.6, "profitability": 0.6, "guidance": 0.4, "cash_flow": 0.55}),
            rec("c2", False, {"growth": 0.4, "profitability": 0.4, "guidance": 0.6, "cash_flow": 0.45}),
            rec("c3", True, {"growth": 0.8, "profitability": 0.8, "guidance": 0.2, "cash_flow": 0.5})]
    r = agreement(recs)
    assert pair(r, "growth", "profitability") == pytest.approx(1.0)
    assert pair(r, "growth", "guidance") == pytest.approx(-1.0)
    xs, ys = [0.6, 0.4, 0.8], [0.55, 0.45, 0.5]
    mx, my = sum(xs) / 3, sum(ys) / 3
    expected = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    assert pair(r, "growth", "cash_flow") == pytest.approx(expected)
    assert pair(r, "growth", "risks") is None             # risks stays at 0.5: no variance
    vals = [p["correlation"] for p in r["pairs"] if p["correlation"] is not None]
    assert len(vals) == 6
    assert r["mean_correlation"] == pytest.approx(sum(vals) / len(vals))
    assert r["min_correlation"] == pytest.approx(min(vals)) == pytest.approx(-1.0)


def test_per_perspective_numbers():
    recs = [rec("c1", True, {"growth": 0.7, "guidance": 0.5}),
            rec("c2", False, {"growth": 0.6, "guidance": 0.5}),
            rec("c3", False, {"growth": 0.2, "guidance": 0.5})]
    g = agreement(recs)["perspectives"]["growth"]
    assert g["mean_prob"] == pytest.approx(0.5)
    assert g["sd_prob"] == pytest.approx(math.sqrt(((0.2) ** 2 + 0.1 ** 2 + 0.3 ** 2) / 3))
    assert g["brier"] == pytest.approx((0.09 + 0.36 + 0.04) / 3)
    assert (g["calls"], g["hits"]) == (3, 2)
    gu = agreement(recs)["perspectives"]["guidance"]
    assert (gu["calls"], gu["hits"], gu["sd_prob"]) == (0, 0, 0)


def test_one_record_and_none():
    r = agreement([rec("c1", True, {"growth": 0.9})])
    assert r["n"] == 1 and all(p["correlation"] is None for p in r["pairs"])
    assert r["mean_correlation"] is None and r["min_correlation"] is None
    assert r["perspectives"]["growth"]["sd_prob"] == 0
    e = agreement([])
    assert e["n"] == 0 and e["mean_correlation"] is None
    assert e["perspectives"]["growth"] == {"mean_prob": None, "sd_prob": None, "brier": None,
                                           "calls": 0, "hits": 0}


def test_unscored_records_are_ignored_and_old_records_derive_probs():
    old = rec("c2", False, {"growth": 0.3})
    del old["prob_by_perspective"]                 # derived from brier_by_perspective
    r = agreement([{"case_id": "c0", "skipped": "identifiable"}, rec("c1", True, {"growth": 0.7}), old])
    assert r["n"] == 2
    assert r["perspectives"]["growth"]["mean_prob"] == pytest.approx(0.5)
    assert r["perspectives"]["growth"]["hits"] == 2


@pytest.mark.parametrize("bad", [
    [rec("c1", True, {}), rec("c1", False, {})],                       # scored twice
    [{**rec("c1", True, {}), "prob_by_perspective": {"growth": 0.5}}],  # missing perspectives
    [rec("c1", "yes", {})],                                             # actual_up not a bool
    [rec("c1", True, {}, event_date="soon")],                           # no ISO event date
])
def test_refuses_what_replay_refuses(bad):
    with pytest.raises(ValueError):
        agreement(bad)


def test_cli(tmp_path, capsys):
    from researchos.__main__ import main
    a = tmp_path / "a.json"; b = tmp_path / "b.json"; out = tmp_path / "out.json"
    a.write_text(json.dumps({"cases": [rec("c1", True, {"growth": 0.7}), rec("c2", False, {"growth": 0.4})]}),
                 encoding="utf-8")
    b.write_text(json.dumps([rec("c3", True, {"growth": 0.6, "risks": 0.3})]), encoding="utf-8")
    assert main(["agreement", str(a), str(b)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["n"] == 3
    assert main(["agreement", str(a), "--out", str(out), str(b)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["n"] == 3
    dup = tmp_path / "dup.json"; dup.write_text(json.dumps([rec("c1", True, {})]), encoding="utf-8")
    assert main(["agreement", str(a), str(dup)]) == 1
    assert "c1" in capsys.readouterr().err
    assert main(["agreement", str(tmp_path / "missing.json")]) == 1
