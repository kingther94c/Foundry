"""researchos.blend.agreement (SPEC.md v19): do the perspectives disagree?"""

import copy
import json
import statistics
from itertools import combinations

import pytest

from researchos.__main__ import main
from researchos.blend import agreement
from researchos.perspectives import PERSPECTIVES


def ps(*values):
    assert len(values) == len(PERSPECTIVES)
    return dict(zip(PERSPECTIVES, values))


def rec(case_id, event_date, up, probs):
    y = 1.0 if up else 0.0
    return {"case_id": case_id, "split": "eval", "event_date": event_date, "actual_up": up,
            "brier_judge": 0.2,
            "brier_by_perspective": {name: (p - y) ** 2 for name, p in probs.items()},
            "prob_by_perspective": dict(probs)}


def old(record):
    return {k: v for k, v in record.items() if k != "prob_by_perspective"}


def _without(record, key):
    return {k: v for k, v in record.items() if k != key}


# growth, profitability, guidance, cash_flow, management_tone, expectations_gap, risks
A = rec("a", "2026-03-01", True, ps(0.8, 0.6, 0.5, 0.7, 0.4, 0.5, 0.2))
B = rec("b", "2026-01-01", False, ps(0.4, 0.3, 0.5, 0.6, 0.6, 0.5, 0.9))
C = rec("c", "2026-02-01", True, ps(0.6, 0.8, 0.5, 0.2, 0.5, 0.5, 0.5))
SKIPPED = {"case_id": "s1", "split": "train", "event_date": "2026-01-20",
           "skipped": "identifiable"}


def test_shape_and_order():
    out = agreement([A, B, C])
    assert set(out) == {"n", "perspectives", "pairs", "mean_correlation", "min_correlation"}
    assert out["n"] == 3
    assert list(out["perspectives"]) == list(PERSPECTIVES)
    for stats in out["perspectives"].values():
        assert set(stats) == {"mean_prob", "sd_prob", "brier", "calls", "hits"}
    assert len(out["pairs"]) == 21
    assert [(p["a"], p["b"]) for p in out["pairs"]] == list(combinations(PERSPECTIVES, 2))
    assert all(set(p) == {"a", "b", "correlation"} for p in out["pairs"])


def test_perspective_statistics_by_hand():
    out = agreement([A, B, C])["perspectives"]
    growth = out["growth"]
    assert growth["mean_prob"] == pytest.approx(0.6)
    assert growth["sd_prob"] == pytest.approx(statistics.pstdev([0.8, 0.4, 0.6]))
    assert growth["brier"] == pytest.approx((0.04 + 0.16 + 0.16) / 3)
    assert (growth["calls"], growth["hits"]) == (3, 3)
    # cash_flow: 0.7 up (hit), 0.6 down (miss), 0.2 up (miss).
    assert (out["cash_flow"]["calls"], out["cash_flow"]["hits"]) == (3, 1)
    # management_tone: 0.4 up (miss), 0.6 down (miss), 0.5 is no call.
    assert (out["management_tone"]["calls"], out["management_tone"]["hits"]) == (2, 0)
    # risks: 0.2 up (miss), 0.9 down (miss), 0.5 no call.
    assert (out["risks"]["calls"], out["risks"]["hits"]) == (2, 0)
    guidance = out["guidance"]
    assert guidance == {"mean_prob": pytest.approx(0.5), "sd_prob": 0.0,
                        "brier": pytest.approx(0.25), "calls": 0, "hits": 0}


def test_correlations_by_hand():
    out = agreement([A, B, C])
    corr = {(p["a"], p["b"]): p["correlation"] for p in out["pairs"]}
    g = [0.8, 0.4, 0.6]
    pr = [0.6, 0.3, 0.8]
    r = [0.2, 0.9, 0.5]
    assert corr[("growth", "profitability")] == pytest.approx(statistics.correlation(g, pr))
    assert corr[("growth", "risks")] == pytest.approx(statistics.correlation(g, r))
    assert corr[("growth", "risks")] < 0  # risks moved against growth
    # guidance and expectations_gap never move: every pair with either has no correlation.
    for (a, b), value in corr.items():
        constant = {"guidance", "expectations_gap"} & {a, b}
        assert (value is None) == bool(constant), (a, b)
    found = [v for v in corr.values() if v is not None]
    assert len(found) == 10  # 5 moving perspectives: 10 pairs
    assert out["mean_correlation"] == pytest.approx(statistics.fmean(found))
    assert out["min_correlation"] == min(found)


def test_perfectly_agreeing_perspectives():
    r1 = rec("r1", "2026-01-01", True, ps(0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6))
    r2 = rec("r2", "2026-01-02", False, ps(0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3))
    out = agreement([r1, r2])
    assert all(p["correlation"] == pytest.approx(1.0) for p in out["pairs"])
    assert out["mean_correlation"] == pytest.approx(1.0)
    assert out["min_correlation"] == pytest.approx(1.0)


def test_records_in_the_order_given_and_unscored_ignored():
    # Order does not change any statistic, but unscored records must not count.
    out = agreement([SKIPPED, C, {"case_id": "u", "brier_judge": 0.1}, A, B])
    assert out == agreement([A, B, C])


def test_old_records_derive_their_probabilities():
    new, before = agreement([A, B, C]), agreement([old(A), old(B), old(C)])
    assert before["n"] == 3
    for name in PERSPECTIVES:
        for key in ("mean_prob", "sd_prob", "brier"):
            assert before["perspectives"][name][key] == \
                pytest.approx(new["perspectives"][name][key], abs=1e-12)
    assert before["mean_correlation"] == pytest.approx(new["mean_correlation"])


def test_no_records():
    out = agreement([])
    assert out["n"] == 0
    assert out["perspectives"] == {name: {"mean_prob": None, "sd_prob": None, "brier": None,
                                          "calls": 0, "hits": 0} for name in PERSPECTIVES}
    assert len(out["pairs"]) == 21 and all(p["correlation"] is None for p in out["pairs"])
    assert out["mean_correlation"] is None and out["min_correlation"] is None
    assert agreement([SKIPPED]) == out


def test_one_record_has_no_correlations():
    out = agreement([A])
    assert out["n"] == 1
    assert out["perspectives"]["growth"] == {"mean_prob": pytest.approx(0.8), "sd_prob": 0.0,
                                             "brier": pytest.approx(0.04), "calls": 1,
                                             "hits": 1}
    assert all(p["correlation"] is None for p in out["pairs"])
    assert out["mean_correlation"] is None and out["min_correlation"] is None


def test_records_are_not_modified():
    records = [A, old(B), SKIPPED]
    before = copy.deepcopy(records)
    agreement(records)
    assert records == before


@pytest.mark.parametrize("records, words", [
    ([_without(A, "event_date")], ["a", "event_date"]),
    ([dict(A, event_date="Jan 10")], ["a", "event_date"]),
    ([dict(A, event_date=None)], ["a", "event_date"]),
    ([B, dict(A, prob_by_perspective=_without(A["prob_by_perspective"], "risks"))],
     ["a", "risks"]),
    ([dict(old(A), brier_by_perspective=_without(A["brier_by_perspective"], "growth"))],
     ["a", "growth"]),
    ([dict(A, actual_up=None)], ["a"]),
    ([A, B, dict(A, split="train")], ["a", "twice"]),
    ([_without(A, "case_id")], ["case_id"]),
], ids=["no-event-date", "event-date-not-iso", "event-date-null", "missing-probability",
        "missing-old-brier", "no-actual-up", "scored-twice", "no-case-id"])
def test_agreement_rejects(records, words):
    with pytest.raises(ValueError) as info:
        agreement(records)
    for word in words:
        assert word in str(info.value)


# --- CLI --------------------------------------------------------------------------

TRAIN = [A, SKIPPED]
EVAL = {"n": 2, "brier_judge": 0.2, "cases": [B, C]}


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def cli_files(tmp_path):
    return write(tmp_path / "train.json", TRAIN), write(tmp_path / "eval.json", {"result": EVAL})


def as_json(data):
    return json.loads(json.dumps(data))


def test_cli_prints_agreement_of_the_merged_files(tmp_path, capsys):
    train_f, eval_f = cli_files(tmp_path)
    merged = TRAIN + EVAL["cases"]
    assert main(["agreement", train_f, eval_f]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got == as_json(agreement(merged))
    assert got["n"] == 3


def test_cli_file_after_an_option_is_still_a_file(tmp_path, capsys):
    train_f, eval_f = cli_files(tmp_path)
    out = tmp_path / "agreement.json"
    assert main(["agreement", train_f, "--out", str(out), eval_f]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text(encoding="utf-8")) == \
        as_json(agreement(TRAIN + EVAL["cases"]))


def test_cli_out_to_a_directory_fails(tmp_path, capsys):
    train_f, _ = cli_files(tmp_path)
    assert main(["agreement", train_f, "--out", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()


@pytest.mark.parametrize("args", [[], ["{f}", "--bogus"], ["{f}", "--rule", "hedge"]],
                         ids=["no-file", "unknown-option", "blend-option"])
def test_cli_bad_arguments(tmp_path, capsys, args):
    train_f, _ = cli_files(tmp_path)
    assert main(["agreement"] + [a.format(f=train_f) for a in args]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()


def test_cli_unreadable_or_refused(tmp_path, capsys):
    train_f, eval_f = cli_files(tmp_path)
    (tmp_path / "not-json.json").write_text("{nope", encoding="utf-8")
    for args in (
        [train_f, str(tmp_path / "missing.json")],
        [str(tmp_path)],
        [str(tmp_path / "not-json.json")],
        [write(tmp_path / "not-a-report.json", {"n": 3})],
        [train_f, eval_f, train_f],  # a scored twice
        [write(tmp_path / "undated.json", [dict(A, event_date="soon")])],
    ):
        assert main(["agreement", *args]) == 1, args
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err.strip(), args
