"""Acceptance tests for researchos.blend (v11), written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "researchos.blend (v11)" section of SPEC.md.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import replace

import pytest
from test_acceptance import OUTCOME, ScriptedClient, make_case, roles
from tools.stats import combine_probabilities, hedge_update

from researchos import blend, cases, loop
from researchos.compare import compare
from researchos.perspectives import PERSPECTIVES, Analysis, View

P = list(PERSPECTIVES)


def probs_of(*values):
    assert len(values) == len(P)
    return dict(zip(P, values))


def rec(case_id, event_date, up, probs, with_probs=True, **extra):
    y = 1.0 if up else 0.0
    r = {"case_id": case_id, "split": "eval", "event_date": event_date, "actual_up": up,
         "brier_by_perspective": {p: (v - y) ** 2 for p, v in probs.items()}, **extra}
    if with_probs:
        r["prob_by_perspective"] = dict(probs)
    return r


A = probs_of(0.9, 0.6, 0.7, 0.4, 0.5, 0.8, 0.2)
B = probs_of(0.3, 0.55, 0.4, 0.6, 0.5, 0.35, 0.7)
C = probs_of(0.65, 0.5, 0.6, 0.45, 0.7, 0.75, 0.3)


# --- what the records carry ------------------------------------------------------

def test_score_keeps_each_perspectives_probability():
    views = {p: View("bullish", v, "high", []) for p, v in A.items()}
    s = loop.score(Analysis("c", views, 0.6, 0.4, ""), OUTCOME)
    assert s["prob_by_perspective"] == pytest.approx(A)
    assert s["brier_by_perspective"]["growth"] == pytest.approx((0.9 - 1) ** 2)


def test_evaluate_records_carry_the_event_date(tmp_path):
    cases.save_case(tmp_path, replace(make_case("c9"), event_date="2026-08-03"), OUTCOME)
    report = loop.evaluate(tmp_path, ["c9"], tmp_path / "pb.json", roles())
    record = report["cases"][0]
    assert record["event_date"] == "2026-08-03"
    assert set(record["prob_by_perspective"]) == set(P)


def test_perspective_probs_reads_or_derives():
    assert blend.perspective_probs(rec("a", "2026-07-01", True, A)) == pytest.approx(A)
    assert blend.perspective_probs(rec("a", "2026-07-01", True, A, with_probs=False)) == pytest.approx(A)
    assert blend.perspective_probs(rec("b", "2026-07-01", False, B, with_probs=False)) == pytest.approx(B)


# --- replay ----------------------------------------------------------------------

def y_of(r):
    return 1.0 if r["actual_up"] else 0.0


def test_uniform_and_median():
    records = [rec("a", "2026-07-02", True, A), rec("b", "2026-07-01", False, B)]
    out = blend.replay(records, "uniform")
    assert out["rule"] == "uniform" and out["eta"] is None and out["n"] == 2
    assert [c["case_id"] for c in out["cases"]] == ["b", "a"]          # event order
    assert out["cases"][0]["prob"] == pytest.approx(sum(B.values()) / 7)
    assert out["cases"][1]["brier_blend"] == pytest.approx((sum(A.values()) / 7 - 1) ** 2)
    assert out["brier_blend"] == pytest.approx(sum(c["brier_blend"] for c in out["cases"]) / 2)
    assert set(out["cases"][0]) == {"case_id", "event_date", "actual_up", "prob", "brier_blend"}
    med = blend.replay(records, "median")
    assert med["cases"][1]["prob"] == pytest.approx(statistics.median(A.values()))


def expected_hedge(batches, eta):
    weights = {p: 1 / len(P) for p in P}
    out = []
    for batch in batches:
        for r in batch:
            out.append((r["case_id"], combine_probabilities(r["prob_by_perspective"], weights)))
        for r in batch:
            y = y_of(r)
            weights = hedge_update(weights, {p: (r["prob_by_perspective"][p] - y) ** 2 for p in P}, eta)
    return out


def test_hedge_updates_after_each_batch():
    r1, r2 = rec("a", "2026-07-01", True, A), rec("b", "2026-07-01", False, B)
    r3 = rec("c", "2026-07-05", True, C)
    for eta in (0.5, 2.0):
        out = blend.replay([r3, r1, r2], "hedge", eta=eta)
        assert out["eta"] == eta
        got = [(c["case_id"], c["prob"]) for c in out["cases"]]
        want = expected_hedge([[r1, r2], [r3]], eta)
        assert [g[0] for g in got] == ["a", "b", "c"]
        assert [g[1] for g in got] == pytest.approx([w[1] for w in want])
    # a and b share a date: b is forecast with the uniform weights, not after a's update.
    assert out["cases"][1]["prob"] == pytest.approx(sum(B.values()) / 7)
    assert blend.replay([r1], "hedge")["eta"] == 0.5


def test_leader_follows_the_best_perspective_so_far():
    r1 = rec("a", "2026-07-01", True, A)        # growth (0.9) has the lowest Brier on a
    r2 = rec("b", "2026-07-02", False, B)
    r3 = rec("c", "2026-07-03", True, C)
    out = blend.replay([r1, r2, r3], "leader")
    assert out["cases"][0]["prob"] == pytest.approx(sum(A.values()) / 7)   # nothing earlier
    assert out["cases"][1]["prob"] == pytest.approx(B["growth"])
    mean = {p: ((A[p] - 1) ** 2 + (B[p] - 0) ** 2) / 2 for p in P}
    best = min(P, key=lambda p: (mean[p], P.index(p)))
    assert out["cases"][2]["prob"] == pytest.approx(C[best])


def test_leader_ties_go_to_the_first_perspective():
    flat = {p: 0.5 for p in P}
    out = blend.replay([rec("a", "2026-07-01", True, flat), rec("b", "2026-07-02", True, C)], "leader")
    assert out["cases"][1]["prob"] == pytest.approx(C[P[0]])


def test_ties_keep_the_order_given_and_skipped_records_are_ignored():
    skipped = {"case_id": "s", "split": "train", "skipped": "identifiable", "event_date": "2026-07-01"}
    records = [rec("z", "2026-07-01", True, A), skipped, rec("x", "2026-07-01", False, B)]
    out = blend.replay(records, "uniform")
    assert [c["case_id"] for c in out["cases"]] == ["z", "x"] and out["n"] == 2
    empty = blend.replay([skipped], "hedge")
    assert empty["n"] == 0 and empty["brier_blend"] is None and empty["cases"] == []


@pytest.mark.parametrize("bad", [
    {"rule": "mean"}, {"eta": 0.0}, {"eta": -1.0},
])
def test_replay_rejects_bad_settings(bad):
    kwargs = {"rule": "hedge", "eta": 0.5, **bad}
    rule = kwargs.pop("rule")
    with pytest.raises(ValueError):
        blend.replay([rec("a", "2026-07-01", True, A)], rule, **kwargs)


def test_replay_rejects_bad_records():
    undated = rec("undated-case", "2026-07-01", True, A)
    del undated["event_date"]
    for bad in (undated, rec("garbled-date", "July 1", True, A)):
        with pytest.raises(ValueError) as info:
            blend.replay([bad], "uniform")
        assert bad["case_id"] in str(info.value)
    partial = rec("partial-case", "2026-07-01", True, A)
    del partial["prob_by_perspective"]["risks"]
    del partial["brier_by_perspective"]["risks"]
    with pytest.raises(ValueError) as info:
        blend.replay([partial], "uniform")
    assert "partial-case" in str(info.value)
    with pytest.raises(ValueError):
        blend.replay([rec("a", "2026-07-01", True, A), rec("a", "2026-07-02", False, B)], "uniform")


# --- CLI -------------------------------------------------------------------------

def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_cli_blend(tmp_path, capsys):
    from researchos.__main__ import main
    r1, r2, r3 = rec("a", "2026-07-01", True, A), rec("b", "2026-07-02", False, B, with_probs=False), \
        rec("c", "2026-07-03", True, C)
    f1 = write(tmp_path / "train.json", {"result": [r1]})
    f2 = write(tmp_path / "eval.json", {"cases": [r3, r2], "n": 2})
    assert main(["blend", f1, f2, "--rule", "hedge", "--eta", "1.5"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got == json.loads(json.dumps(blend.replay([r1, r3, r2], "hedge", eta=1.5)))
    assert main(["blend", f1, f2]) == 0
    default = json.loads(capsys.readouterr().out)
    assert default["rule"] == "hedge" and default["eta"] == 0.5
    out = tmp_path / "leader.json"
    assert main(["blend", f1, f2, "--rule", "leader", "--out", str(out)]) == 0
    assert capsys.readouterr().out == ""
    leader = json.loads(out.read_text(encoding="utf-8"))
    assert leader["rule"] == "leader"
    # Two replays compare case by case.
    cmp = compare(default, leader, metric="brier_blend", resamples=50)
    assert cmp["n"] == 3


@pytest.mark.parametrize("args", [[], ["{f}", "--rule", "mean"], ["{f}", "--eta", "0"], ["{f}", "--eta", "-2"],
                                  ["{f}", "--eta", "fast"]])
def test_cli_blend_bad_arguments(tmp_path, capsys, args):
    from researchos.__main__ import main
    f = write(tmp_path / "r.json", [rec("a", "2026-07-01", True, A)])
    assert main(["blend"] + [a.format(f=f) for a in args]) == 2
    assert capsys.readouterr().out == ""


def test_cli_blend_unusable_input_exits_1(tmp_path, capsys):
    from researchos.__main__ import main
    undated = rec("undated-case", "2026-07-01", True, A)
    del undated["event_date"]
    for f in (str(tmp_path / "missing.json"), write(tmp_path / "bad.json", {"n": 1}),
              write(tmp_path / "undated.json", [undated])):
        assert main(["blend", f]) == 1
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err.strip()


def test_cli_evaluate_then_blend(tmp_path, capsys):
    from researchos.__main__ import main
    cases.save_case(tmp_path, replace(make_case("c1"), event_date="2026-08-03"), OUTCOME)
    out = tmp_path / "eval.json"
    assert main(["evaluate", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"), "--ids", "c1",
                 "--out", str(out)], client_factory=ScriptedClient) == 0
    assert main(["blend", str(out), "--rule", "uniform"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["n"] == 1 and got["cases"][0]["event_date"] == "2026-08-03"
    assert got["cases"][0]["prob"] == pytest.approx(0.6)       # ScriptedClient's analysts all say 0.6
    assert math.isclose(got["brier_blend"], (0.6 - 1) ** 2)
