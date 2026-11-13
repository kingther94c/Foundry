"""Acceptance tests for researchos.compare v32, written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "Resampling whole cases (v32)" section of SPEC.md.
"""

from __future__ import annotations

import copy
import json
import random

import pytest

from researchos import compare as cmp
from researchos.__main__ import main
from tools.stats import quantile


def key(snapshot, k, judge, weighted=None):
    r = {"case_id": f"{snapshot}/{k}", "case": snapshot, "key": k, "brier_judge": judge}
    if weighted is not None:
        r["brier_weighted"] = weighted
    return r


def earnings(case_id, judge):
    return {"case_id": case_id, "split": "eval", "brier_judge": judge}


A = {"cases": [key("s1", "ust10y_1w", 0.25), key("s1", "dxy_1w", 0.20),
               key("s2", "ust10y_1w", 0.30), key("s3", "dxy_1m", 0.10),
               key("s2", "dxy_1w", 0.22)]}
B = {"cases": [key("s2", "dxy_1w", 0.20), key("s1", "dxy_1w", 0.24),
               key("s1", "ust10y_1w", 0.15), key("s3", "dxy_1m", 0.12),
               key("s2", "ust10y_1w", 0.31)]}


def reference_grouped(groups, resamples, seed, level=0.95):
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        chosen = rng.choices(groups, k=len(groups))
        values = [v for g in chosen for v in g]
        means.append(sum(values) / len(values))
    return (quantile(means, (1 - level) / 2), quantile(means, 1 - (1 - level) / 2))


# --- bootstrap_ci_grouped --------------------------------------------------------


def test_grouped_follows_the_formula():
    groups = [[0.1, -0.2], [0.05], [-0.3, 0.0, 0.4], [0.2]]
    for seed in (0, 1, 17):
        assert cmp.bootstrap_ci_grouped(groups, resamples=500, seed=seed) == \
            reference_grouped(groups, 500, seed)
    assert cmp.bootstrap_ci_grouped(groups, resamples=300, seed=2, level=0.8) == \
        reference_grouped(groups, 300, 2, 0.8)


def test_grouped_defaults():
    groups = [[0.1, 0.3], [-0.2]]
    assert cmp.bootstrap_ci_grouped(groups) == reference_grouped(groups, 10000, 0)


def test_one_value_per_group_is_bootstrap_ci():
    rng = random.Random(5)
    for n in (1, 2, 7, 40):
        diffs = [rng.uniform(-0.3, 0.3) for _ in range(n)]
        for seed in (0, 3):
            assert cmp.bootstrap_ci_grouped([[d] for d in diffs], resamples=400, seed=seed,
                                            level=0.9) == \
                cmp.bootstrap_ci(diffs, resamples=400, seed=seed, level=0.9)


def test_grouped_none_for_no_groups():
    assert cmp.bootstrap_ci_grouped([]) is None
    assert cmp.bootstrap_ci_grouped([], resamples=5, seed=3, level=0.5) is None


def test_a_group_chosen_twice_counts_twice():
    # Two groups of different sizes: a resample of both counts each value once; of
    # the big one twice, its values twice. The mean is over values, not groups.
    groups = [[1.0, 1.0, 1.0], [0.0]]
    low, high = cmp.bootstrap_ci_grouped(groups, resamples=2000, seed=0, level=0.5)
    possible = {0.0, 0.75, 1.0}
    assert low in possible and high in possible
    assert (low, high) == reference_grouped(groups, 2000, 0, 0.5)


@pytest.mark.parametrize("kwargs, needle", [
    ({"resamples": 0}, "resamples"),
    ({"resamples": -3}, "resamples"),
    ({"level": 0}, "level"),
    ({"level": 1}, "level"),
    ({"level": 1.5}, "level"),
    ({"level": float("nan")}, "level"),
])
def test_grouped_rejects_bad_arguments(kwargs, needle):
    with pytest.raises(ValueError) as info:
        cmp.bootstrap_ci_grouped([[0.1]], **kwargs)
    assert needle in str(info.value)


def test_grouped_rejects_an_empty_group_naming_it():
    with pytest.raises(ValueError) as info:
        cmp.bootstrap_ci_grouped([[0.1], [0.2, 0.3], [], [0.4], []])
    assert "2" in str(info.value)


def test_grouped_checks_in_order():
    with pytest.raises(ValueError) as info:
        cmp.bootstrap_ci_grouped([[]], resamples=0, level=2)
    assert "resamples" in str(info.value)
    with pytest.raises(ValueError) as info:
        cmp.bootstrap_ci_grouped([[]], level=2)
    assert "level" in str(info.value)
    # The checks come before the "no groups" answer.
    with pytest.raises(ValueError):
        cmp.bootstrap_ci_grouped([], resamples=0)


def test_grouped_leaves_its_input_alone():
    groups = [[0.1, 0.2], [0.3]]
    before = copy.deepcopy(groups)
    cmp.bootstrap_ci_grouped(groups, resamples=50)
    assert groups == before


# --- compare(resample=...) -------------------------------------------------------


def test_records_is_what_compare_did():
    expected = cmp.compare(A, B, resamples=300, seed=4)
    assert cmp.compare(A, B, resamples=300, seed=4, resample="records") == expected
    assert "resample" not in expected and "groups" not in expected
    assert all(set(c) == {"case_id", "a", "b", "diff"} for c in expected["cases"])


def test_cases_groups_by_snapshot():
    got = cmp.compare(A, B, resamples=700, seed=9, resample="cases")
    plain = cmp.compare(A, B, resamples=700, seed=9)
    for name in ("metric", "n", "mean_a", "mean_b", "mean_diff", "b_better", "a_better", "ties"):
        assert got[name] == plain[name], name
    assert [{k: c[k] for k in ("case_id", "a", "b", "diff")} for c in got["cases"]] == \
        plain["cases"]
    assert [c["group"] for c in got["cases"]] == ["s1", "s1", "s2", "s3", "s2"]
    assert list(got)[-2:] == ["resample", "groups"]
    assert got["resample"] == "cases" and got["groups"] == 3
    diffs = [c["diff"] for c in plain["cases"]]
    groups = [[diffs[0], diffs[1]], [diffs[2], diffs[4]], [diffs[3]]]
    assert (got["ci_low"], got["ci_high"]) == reference_grouped(groups, 700, 9)
    assert list(got["cases"][0]) == ["case_id", "a", "b", "diff", "group"]


def test_cases_on_earnings_records_is_one_group_each():
    a = {"cases": [earnings(f"c{i}", 0.1 + 0.02 * i) for i in range(6)]}
    b = {"cases": [earnings(f"c{i}", 0.3 - 0.03 * i) for i in range(6)]}
    got = cmp.compare(a, b, resamples=500, seed=1, resample="cases")
    plain = cmp.compare(a, b, resamples=500, seed=1)
    assert (got["ci_low"], got["ci_high"]) == (plain["ci_low"], plain["ci_high"])
    assert got["groups"] == 6
    assert [c["group"] for c in got["cases"]] == [f"c{i}" for i in range(6)]


@pytest.mark.parametrize("case_value", [None, "", 7, ["s1"], {"id": "s1"}, True])
def test_a_case_that_is_not_a_non_empty_string_falls_back_to_the_case_id(case_value):
    a = {"cases": [dict(key("s1", "k1", 0.2), case=case_value), key("s1", "k2", 0.3)]}
    b = {"cases": [key("s1", "k1", 0.1), key("s1", "k2", 0.25)]}
    got = cmp.compare(a, b, resamples=50, resample="cases")
    assert [c["group"] for c in got["cases"]] == ["s1/k1", "s1"]
    assert got["groups"] == 2


def test_a_record_without_a_case_key_uses_its_case_id():
    a = {"cases": [{"case_id": "x", "brier_judge": 0.2}, key("s1", "k", 0.3)]}
    b = {"cases": [{"case_id": "x", "brier_judge": 0.1}, key("s1", "k", 0.1)]}
    got = cmp.compare(a, b, resamples=50, resample="cases")
    assert [c["group"] for c in got["cases"]] == ["x", "s1"]


def test_the_case_comes_from_a_not_b():
    a = {"cases": [key("s1", "k1", 0.2), key("s1", "k2", 0.3)]}
    b = {"cases": [dict(key("s1", "k1", 0.1), case="other"), dict(key("s1", "k2", 0.2), case="x")]}
    got = cmp.compare(a, b, resamples=50, resample="cases")
    assert [c["group"] for c in got["cases"]] == ["s1", "s1"]
    assert got["groups"] == 1


def test_the_case_comes_from_the_scored_record():
    # An unscored record of the same case_id (a skipped one) does not decide the group.
    a = {"cases": [{"case_id": "s1/k1", "case": "zzz", "skipped": "x"},
                   key("s1", "k1", 0.2), key("s2", "k1", 0.3)]}
    b = {"cases": [key("s1", "k1", 0.1), key("s2", "k1", 0.2)]}
    got = cmp.compare(a, b, resamples=50, resample="cases")
    assert [c["group"] for c in got["cases"]] == ["s1", "s2"]


def test_the_case_is_read_from_the_metric_compared():
    # Scored on brier_weighted only for some records: the pairs (and groups) follow it.
    a = {"cases": [key("s1", "k1", 0.2, 0.3), key("s1", "k2", 0.2), key("s2", "k1", 0.1, 0.2)]}
    b = {"cases": [key("s1", "k1", 0.1, 0.1), key("s1", "k2", 0.3, 0.4), key("s2", "k1", 0.2, 0.1)]}
    got = cmp.compare(a, b, metric="brier_weighted", resamples=60, seed=2, resample="cases")
    assert [c["case_id"] for c in got["cases"]] == ["s1/k1", "s2/k1"]
    assert got["groups"] == 2


def test_groups_in_order_of_first_pair():
    a = {"cases": [key("s3", "k", 0.2), key("s1", "k", 0.2), key("s3", "j", 0.1),
                   key("s2", "k", 0.3), key("s1", "j", 0.4)]}
    b = copy.deepcopy(a)
    for i, r in enumerate(b["cases"]):
        r["brier_judge"] = 0.05 * (i + 1)
    got = cmp.compare(a, b, resamples=400, seed=6, resample="cases")
    diffs = {c["case_id"]: c["diff"] for c in got["cases"]}
    groups = [[diffs["s3/k"], diffs["s3/j"]], [diffs["s1/k"], diffs["s1/j"]], [diffs["s2/k"]]]
    assert (got["ci_low"], got["ci_high"]) == reference_grouped(groups, 400, 6)


def test_cases_with_no_pairs():
    got = cmp.compare({"cases": [key("s1", "k", 0.2)]}, {"cases": []}, resample="cases")
    assert got["n"] == 0 and got["groups"] == 0
    assert got["ci_low"] is None and got["ci_high"] is None
    assert got["resample"] == "cases" and got["cases"] == []


def test_resample_is_keyword_only():
    with pytest.raises(TypeError):
        cmp.compare(A, B, "brier_judge", 100, 0, "cases")


@pytest.mark.parametrize("value", ["record", "Cases", "snapshots", "", None, 1])
def test_other_resample_values_are_refused(value):
    with pytest.raises(ValueError) as info:
        cmp.compare(A, B, resample=value)
    assert repr(value) in str(info.value) or str(value) in str(info.value)


def test_the_metric_is_checked_first():
    with pytest.raises(ValueError) as info:
        cmp.compare(A, B, metric="accuracy", resample="nope")
    assert "accuracy" in str(info.value)


def test_resample_is_checked_before_pairing():
    twice = {"cases": [key("s1", "k", 0.2), key("s1", "k", 0.3)]}
    with pytest.raises(ValueError) as info:
        cmp.compare(twice, B, resample="nope")
    assert "nope" in str(info.value)
    with pytest.raises(ValueError) as info:
        cmp.compare(twice, B, resample="cases")
    assert "twice" in str(info.value)


def test_compare_leaves_the_reports_alone():
    a, b = copy.deepcopy(A), copy.deepcopy(B)
    cmp.compare(a, b, resamples=50, resample="cases")
    assert a == A and b == B


def test_a_wider_interval_when_a_snapshot_moves_together():
    # Twelve snapshots of ten keys each, every key of a snapshot with the same diff:
    # resampling records counts 120 pieces of evidence, resampling cases twelve.
    rng = random.Random(11)
    a, b = {"cases": []}, {"cases": []}
    for s in range(12):
        shift = rng.uniform(-0.1, 0.1)
        for k in range(10):
            a["cases"].append(key(f"s{s}", f"k{k}", 0.25))
            b["cases"].append(key(f"s{s}", f"k{k}", 0.25 + shift))
    by_record = cmp.compare(a, b, resamples=2000, seed=0)
    by_case = cmp.compare(a, b, resamples=2000, seed=0, resample="cases")
    assert by_case["groups"] == 12
    assert by_case["ci_high"] - by_case["ci_low"] > 2 * (by_record["ci_high"] - by_record["ci_low"])


# --- CLI --------------------------------------------------------------------------


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_cli_resample_cases(tmp_path, capsys):
    fa, fb = write(tmp_path / "a.json", A), write(tmp_path / "b.json", B)
    assert main(["compare", fa, fb, "--resamples", "300", "--seed", "2", "--resample",
                 "cases"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == json.loads(json.dumps(cmp.compare(A, B, resamples=300, seed=2,
                                                    resample="cases")))
    assert main(["compare", "-a", fa, "-b", fb, "--resample", "cases", "--resamples", "300",
                 "--seed", "2"]) == 0
    assert json.loads(capsys.readouterr().out) == out


def test_cli_resample_records_and_default_unchanged(tmp_path, capsys):
    fa, fb = write(tmp_path / "a.json", A), write(tmp_path / "b.json", B)
    assert main(["compare", fa, fb, "--resamples", "200"]) == 0
    default = capsys.readouterr().out
    assert main(["compare", fa, fb, "--resamples", "200", "--resample", "records"]) == 0
    assert capsys.readouterr().out == default
    assert "resample" not in json.loads(default)


def test_cli_several_files_a_side(tmp_path, capsys):
    a1 = write(tmp_path / "a1.json", {"cases": A["cases"][:2]})
    a2 = write(tmp_path / "a2.json", A["cases"][2:])
    fb = write(tmp_path / "b.json", B)
    assert main(["compare", "-a", a1, "-a", a2, "-b", fb, "--resample", "cases",
                 "--resamples", "150"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["groups"] == 3 and out["n"] == 5


def test_cli_out_file(tmp_path, capsys):
    fa, fb = write(tmp_path / "a.json", A), write(tmp_path / "b.json", B)
    out = tmp_path / "out.json"
    assert main(["compare", fa, fb, "--resample", "cases", "--resamples", "100", "--out",
                 str(out)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text(encoding="utf-8"))["resample"] == "cases"


@pytest.mark.parametrize("value", ["record", "snapshots", "CASES", ""])
def test_cli_rejects_other_values(tmp_path, capsys, value):
    fa, fb = write(tmp_path / "a.json", A), write(tmp_path / "b.json", B)
    assert main(["compare", fa, fb, "--resample", value]) == 2
    assert "--resample" in capsys.readouterr().err


def test_cli_case_scored_twice_still_exits_1(tmp_path, capsys):
    twice = write(tmp_path / "twice.json", {"cases": [key("s1", "k", 0.2), key("s1", "k", 0.3)]})
    fb = write(tmp_path / "b.json", B)
    assert main(["compare", twice, fb, "--resample", "cases"]) == 1
    assert "twice" in capsys.readouterr().err
