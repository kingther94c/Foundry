"""researchos.blend (SPEC.md v11): replaying combination rules over scored records."""

import copy
import json
import math
import statistics

import pytest
from tools.stats import combine_probabilities, hedge_update

from conftest import VIEW_PROBS, make_case, make_outcome, make_roles

from researchos.__main__ import main
from researchos.blend import RULES, perspective_probs, replay
from researchos.cases import save_case
from researchos.loop import evaluate, score, train
from researchos.perspectives import PERSPECTIVES, Analysis, View


def ps(*values, **named):
    """Seven probabilities in PERSPECTIVES order, or 0.5 everywhere but the named ones."""
    if values:
        assert len(values) == len(PERSPECTIVES)
        return dict(zip(PERSPECTIVES, values))
    out = {name: 0.5 for name in PERSPECTIVES}
    out.update(named)
    return out


def rec(case_id, event_date, up, probs, split="eval"):
    """A scored record as score() + train/evaluate write it."""
    y = 1.0 if up else 0.0
    return {"case_id": case_id, "split": split, "event_date": event_date, "actual_up": up,
            "brier_weighted": 0.2, "brier_judge": 0.2,
            "brier_by_perspective": {name: (p - y) ** 2 for name, p in probs.items()},
            "prob_by_perspective": dict(probs)}


def old(record):
    """The record as scored before v11: no prob_by_perspective."""
    return {k: v for k, v in record.items() if k != "prob_by_perspective"}


C1 = rec("c1", "2026-01-10", True, ps(0.8, 0.7, 0.6, 0.55, 0.5, 0.3, 0.2))
C2 = rec("c2", "2026-02-10", False, ps(0.1, 0.2, 0.3, 0.4, 0.9, 0.9, 0.9))
C3 = rec("c3", "2026-01-25", True, ps(0.6, 0.6, 0.6, 0.4, 0.4, 0.4, 0.5))
SKIPPED = {"case_id": "s1", "split": "train", "event_date": "2026-01-20", "skipped": "identifiable"}


# --- records ----------------------------------------------------------------------

def test_score_keeps_each_perspective_probability():
    views = {"growth": View("bullish", 0.8, "high", []), "risks": View("bearish", 0.3, "low", [])}
    s = score(Analysis("c", views, 0.6, 0.4, ""), make_outcome(ar=-0.01))
    assert s["prob_by_perspective"] == {"growth": 0.8, "risks": 0.3}
    assert perspective_probs(s) == {"growth": 0.8, "risks": 0.3}


def save_cases(tmp_path, specs):
    root = tmp_path / "cases"
    for cid, ar, when in specs:
        case = make_case(cid)
        case.event_date = when
        save_case(root, case, make_outcome(ar=ar))
    return root


def test_evaluate_records_carry_event_date_and_probabilities(tmp_path):
    root = save_cases(tmp_path, [("a-q1", 0.02, "2026-03-02"), ("b-q1", -0.01, "2026-03-05")])
    report = evaluate(root, ["a-q1", "b-q1"], tmp_path / "pb.json", make_roles())
    assert [(r["case_id"], r["event_date"]) for r in report["cases"]] == \
        [("a-q1", "2026-03-02"), ("b-q1", "2026-03-05")]
    assert all(r["prob_by_perspective"] == VIEW_PROBS for r in report["cases"])
    out = replay(report["cases"], "uniform")
    assert out["n"] == 2
    # The uniform rule is evaluate's own brier_uniform.
    assert [c["brier_blend"] for c in out["cases"]] == \
        [pytest.approx(r["brier_uniform"]) for r in report["cases"]]


def test_hedge_replay_of_train_output_reproduces_the_trained_weights(tmp_path):
    specs = [("a-q1", 0.02, "2026-03-02"), ("b-q1", -0.01, "2026-03-02"),
             ("c-q1", -0.03, "2026-03-05"), ("d-q1", 0.01, "2026-03-09")]
    root = save_cases(tmp_path, specs)
    records = train(root, [s[0] for s in specs], tmp_path / "pb.json", make_roles())
    assert all(r["prob_by_perspective"] == VIEW_PROBS for r in records)
    out = replay(records, "hedge", eta=0.5)
    assert [c["case_id"] for c in out["cases"]] == [r["case_id"] for r in records]
    # train forecasts a batch with the weights from before it and updates them with
    # hedge_update(eta=0.5) afterwards, as the hedge rule does.
    assert [c["brier_blend"] for c in out["cases"]] == \
        [pytest.approx(r["brier_weighted"]) for r in records]
    assert len({round(c["prob"], 12) for c in out["cases"]}) > 1  # the weights did move


# --- perspective_probs ------------------------------------------------------------

def test_perspective_probs_prefers_the_recorded_probabilities():
    record = dict(C1, prob_by_perspective={"growth": 0.42})
    assert perspective_probs(record) == {"growth": 0.42}
    assert perspective_probs(C1) == C1["prob_by_perspective"]


@pytest.mark.parametrize("record", [C1, C2], ids=["went-up", "fell"])
def test_perspective_probs_derived_from_brier_scores(record):
    derived = perspective_probs(old(record))
    assert derived.keys() == record["prob_by_perspective"].keys()
    for name, p in record["prob_by_perspective"].items():
        assert derived[name] == pytest.approx(p)


def test_perspective_probs_derivation_formula():
    up = {"actual_up": True, "brier_by_perspective": {"growth": 0.04, "risks": 0.81}}
    down = {"actual_up": False, "brier_by_perspective": {"growth": 0.04, "risks": 0.81}}
    assert perspective_probs(up) == {"growth": pytest.approx(0.8), "risks": pytest.approx(0.1)}
    assert perspective_probs(down) == {"growth": pytest.approx(0.2), "risks": pytest.approx(0.9)}


# --- replay -----------------------------------------------------------------------

def test_rules():
    assert RULES == ("uniform", "median", "hedge", "leader", "pool")  # pool: SPEC.md v20


def test_uniform_and_median():
    u = replay([C1, C2], "uniform")
    assert (u["rule"], u["eta"], u["n"]) == ("uniform", None, 2)
    assert [c["prob"] for c in u["cases"]] == [pytest.approx(3.65 / 7), pytest.approx(3.7 / 7)]
    assert u["cases"][0]["brier_blend"] == pytest.approx((3.65 / 7 - 1) ** 2)
    assert u["brier_blend"] == pytest.approx(((3.65 / 7 - 1) ** 2 + (3.7 / 7) ** 2) / 2)

    m = replay([C1, C2], "median", eta=3.0)
    assert (m["rule"], m["eta"], m["n"]) == ("median", None, 2)
    assert m["cases"] == [
        {"case_id": "c1", "event_date": "2026-01-10", "actual_up": True,
         "prob": pytest.approx(0.55), "brier_blend": pytest.approx(0.2025)},
        {"case_id": "c2", "event_date": "2026-02-10", "actual_up": False,
         "prob": pytest.approx(0.4), "brier_blend": pytest.approx(0.16)},
    ]
    assert m["brier_blend"] == pytest.approx((0.2025 + 0.16) / 2)


def test_hedge_by_hand():
    # After one case where growth said 0.9 and the rest 0.5, and the stock went up, with
    # eta 1 the weights are proportional to exp(-loss): growth exp(-0.01), the others
    # exp(-0.25). A case where only growth says 1.0 is then forecast at growth's weight.
    h1 = rec("h1", "2026-01-05", True, ps(growth=0.9))
    h2 = rec("h2", "2026-01-06", True, ps(1.0, 0, 0, 0, 0, 0, 0))
    out = replay([h1, h2], "hedge", eta=1.0)
    assert (out["rule"], out["eta"], out["n"]) == ("hedge", 1.0, 2)
    expected = math.exp(-0.01) / (math.exp(-0.01) + 6 * math.exp(-0.25))
    assert [c["prob"] for c in out["cases"]] == [pytest.approx(3.9 / 7), pytest.approx(expected)]
    assert out["cases"][1]["brier_blend"] == pytest.approx((expected - 1) ** 2)


def test_hedge_walks_forward_in_event_date_batches():
    a = rec("a", "2026-01-10", True, ps(0.9, 0.8, 0.5, 0.5, 0.5, 0.2, 0.1))
    b = rec("b", "2026-01-10", False, ps(0.2, 0.3, 0.5, 0.5, 0.5, 0.8, 0.9))
    c = rec("c", "2026-03-01", True, ps(0.9, 0.9, 0.5, 0.5, 0.5, 0.1, 0.1))
    out = replay([c, a, b], "hedge")  # out of order; a and b share a date and keep their order
    assert out["eta"] == 0.5
    assert [x["case_id"] for x in out["cases"]] == ["a", "b", "c"]

    def losses(r):
        y = 1.0 if r["actual_up"] else 0.0
        return {p: (r["prob_by_perspective"][p] - y) ** 2 for p in PERSPECTIVES}

    weights = {p: 1 / 7 for p in PERSPECTIVES}
    # a and b are both forecast with the weights from before their batch...
    assert out["cases"][0]["prob"] == pytest.approx(statistics.mean(a["prob_by_perspective"].values()))
    assert out["cases"][1]["prob"] == pytest.approx(statistics.mean(b["prob_by_perspective"].values()))
    # ...and the weights learn from them afterwards, a then b.
    weights = hedge_update(hedge_update(weights, losses(a), 0.5), losses(b), 0.5)
    expected = combine_probabilities(c["prob_by_perspective"], weights)
    assert out["cases"][2]["prob"] == pytest.approx(expected)
    assert abs(expected - 0.5) > 1e-3  # not the uniform mean
    assert out["brier_blend"] == pytest.approx(statistics.mean(x["brier_blend"] for x in out["cases"]))


def test_leader_uses_only_earlier_batches():
    l1 = rec("l1", "2026-04-01", True, ps(growth=0.6, profitability=0.9))
    l2 = rec("l2", "2026-04-01", False, ps(growth=0.1))
    l3 = rec("l3", "2026-04-02", True, ps(0.7, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2))
    out = replay([l1, l2, l3], "leader", eta=9.0)
    assert (out["rule"], out["eta"]) == ("leader", None)
    # No earlier cases for l1 or l2 (same date): the uniform mean. Had l2 learned from l1,
    # profitability (Brier 0.01 vs growth's 0.16) would have led it.
    # After the batch, mean Brier: growth (0.16 + 0.01) / 2, profitability (0.01 + 0.25) / 2,
    # the rest 0.25: growth leads l3.
    assert [c["prob"] for c in out["cases"]] == \
        [pytest.approx(4.0 / 7), pytest.approx(3.1 / 7), pytest.approx(0.7)]


def test_leader_ties_go_to_the_first_perspective():
    t1 = rec("t1", "2026-05-01", True, ps(profitability=0.8, cash_flow=0.8))
    t2 = rec("t2", "2026-05-02", True, ps(profitability=0.35, cash_flow=0.65))
    t3 = rec("t3", "2026-05-03", True, ps(0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4))
    out = replay([t1, t2, t3], "leader")
    # profitability and cash_flow tie after t1; profitability comes first in PERSPECTIVES.
    assert out["cases"][1]["prob"] == pytest.approx(0.35)
    # Over t1 and t2: profitability (0.04 + 0.4225) / 2 vs cash_flow (0.04 + 0.1225) / 2.
    assert out["cases"][2]["prob"] == pytest.approx(0.4)
    out = replay([t1, rec("t4", "2026-05-02", True, ps(growth=0.3))], "leader")
    assert out["cases"][1]["prob"] == pytest.approx(0.5)  # profitability's


def test_unscored_records_are_ignored():
    unscored = {"case_id": "c9", "split": "eval", "brier_judge": 0.3}
    records = [{"case_id": "s0", "skipped": "identifiable"}, C1, SKIPPED, unscored, C2]
    for rule in RULES:
        assert replay(records, rule) == replay([C1, C2], rule)
    assert replay(records)["n"] == 2


@pytest.mark.parametrize("rule", RULES)
def test_records_scored_before_v11_replay_the_same(rule):
    records = [C1, C2, C3]
    new, before = replay(records, rule), replay([old(r) for r in records], rule)
    assert [c["case_id"] for c in before["cases"]] == ["c1", "c3", "c2"]
    assert [c["prob"] for c in before["cases"]] == [pytest.approx(c["prob"]) for c in new["cases"]]
    assert before["brier_blend"] == pytest.approx(new["brier_blend"])


def test_nothing_scored():
    assert replay([]) == {"rule": "hedge", "eta": 0.5, "n": 0, "brier_blend": None, "cases": []}
    assert replay([SKIPPED], "leader") == \
        {"rule": "leader", "eta": None, "n": 0, "brier_blend": None, "cases": []}


def test_records_are_not_modified():
    records = [C2, old(C1), SKIPPED]
    before = copy.deepcopy(records)
    for rule in RULES:
        replay(records, rule)
    assert records == before


def _without(record, key):
    return {k: v for k, v in record.items() if k != key}


@pytest.mark.parametrize("records, kwargs, words", [
    ([C1], {"rule": "best"}, ["best"]),
    ([C1], {"rule": "uniform", "eta": 0}, ["eta"]),
    ([C1], {"eta": -0.5}, ["eta"]),
    ([C1], {"eta": float("nan")}, ["eta"]),
    ([_without(C1, "event_date")], {}, ["c1", "event_date"]),
    ([dict(C1, event_date="Jan 10")], {}, ["c1", "event_date"]),
    ([dict(C1, event_date=None)], {"rule": "median"}, ["c1", "event_date"]),
    ([C2, dict(C1, prob_by_perspective=_without(C1["prob_by_perspective"], "risks"))], {},
     ["c1", "risks"]),
    ([dict(old(C1), brier_by_perspective=_without(C1["brier_by_perspective"], "growth"))],
     {"rule": "uniform"}, ["c1", "growth"]),
    ([dict(C1, actual_up=None)], {}, ["c1"]),
    ([C1, C2, dict(C1, split="train")], {}, ["c1", "twice"]),
], ids=["unknown-rule", "eta-zero", "eta-negative", "eta-nan", "no-event-date",
        "event-date-not-iso", "event-date-null", "missing-probability", "missing-old-brier",
        "no-actual-up", "scored-twice"])
def test_replay_rejects(records, kwargs, words):
    with pytest.raises(ValueError) as info:
        replay(records, **kwargs)
    for word in words:
        assert word in str(info.value)


# --- CLI --------------------------------------------------------------------------

TRAIN = [C1, SKIPPED]
EVAL = {"n": 2, "brier_judge": 0.2, "cases": [C2, C3]}


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def cli_files(tmp_path):
    return write(tmp_path / "train.json", TRAIN), write(tmp_path / "eval.json", {"result": EVAL})


def as_json(data):
    return json.loads(json.dumps(data))


def test_cli_prints_the_replay_of_the_merged_files(tmp_path, capsys):
    train_f, eval_f = cli_files(tmp_path)
    merged = TRAIN + EVAL["cases"]
    assert main(["blend", train_f, eval_f, "--rule", "leader"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got == as_json(replay(merged, "leader"))
    assert [c["case_id"] for c in got["cases"]] == ["c1", "c3", "c2"]

    assert main(["blend", train_f, eval_f]) == 0  # hedge, eta 0.5
    assert json.loads(capsys.readouterr().out) == as_json(replay(merged, "hedge", eta=0.5))

    # A file after an option is still one of the files.
    assert main(["blend", train_f, "--eta", "2", eval_f]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got == as_json(replay(merged, "hedge", eta=2.0)) and got["eta"] == 2.0


def test_cli_out(tmp_path, capsys):
    train_f, eval_f = cli_files(tmp_path)
    out = tmp_path / "blend.json"
    assert main(["blend", train_f, eval_f, "--rule", "median", "--out", str(out)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text(encoding="utf-8")) == \
        as_json(replay(TRAIN + EVAL["cases"], "median"))
    assert main(["blend", train_f, "--out", str(tmp_path)]) == 1  # a directory
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()


@pytest.mark.parametrize("args", [
    [], ["--rule", "hedge"], ["{f}", "--rule", "best"], ["{f}", "--eta", "0"],
    ["{f}", "--eta", "-1"], ["{f}", "--eta", "abc"], ["{f}", "--eta", "nan"],
    ["{f}", "--eta", "inf"], ["{f}", "--bogus"],
], ids=["no-file", "only-rule", "unknown-rule", "eta-zero", "eta-negative", "eta-text",
        "eta-nan", "eta-inf", "unknown-option"])
def test_cli_bad_arguments(tmp_path, capsys, args):
    train_f, _ = cli_files(tmp_path)
    assert main(["blend"] + [a.format(f=train_f) for a in args]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()


def test_cli_unreadable_rejected_or_refused(tmp_path, capsys):
    train_f, eval_f = cli_files(tmp_path)
    (tmp_path / "not-json.json").write_text("{nope", encoding="utf-8")
    for args in (
        [train_f, str(tmp_path / "missing.json")],
        [str(tmp_path)],
        [str(tmp_path / "not-json.json")],
        [write(tmp_path / "not-a-report.json", {"n": 3})],
        [train_f, eval_f, train_f],  # c1 scored twice
        [write(tmp_path / "undated.json", [dict(C1, event_date="soon")])],
    ):
        assert main(["blend", *args]) == 1, args
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err.strip(), args


def test_two_replays_compare_on_brier_blend(tmp_path, capsys):
    train_f, eval_f = cli_files(tmp_path)
    uniform, hedge = tmp_path / "uniform.json", tmp_path / "hedge.json"
    assert main(["blend", train_f, eval_f, "--rule", "uniform", "--out", str(uniform)]) == 0
    assert main(["blend", train_f, eval_f, "--out", str(hedge)]) == 0
    argv = ["compare", str(uniform), str(hedge), "--metric", "brier_blend", "--resamples", "50"]
    assert main(argv) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["metric"] == "brier_blend" and got["n"] == 3
    assert [c["case_id"] for c in got["cases"]] == ["c1", "c3", "c2"]
    assert got["cases"][0]["diff"] == pytest.approx(0.0)  # both start from uniform weights
