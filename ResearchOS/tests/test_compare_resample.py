import copy
import json
import random

import pytest
from tools.stats import quantile

from researchos.__main__ import main
from researchos.compare import bootstrap_ci, bootstrap_ci_grouped, compare


def rec(case_id, judge, **extra):
    return {"case_id": case_id, "split": "eval", "brier_judge": judge, **extra}


def report(*records):
    return {"cases": list(records)}


def no_client():
    raise AssertionError("compare must not build a client")


# A macro-like pair of reports: keys of snapshots s1 and s2, plus keys with no usable case.
A = report(rec("s1/k1", 0.30, case="s1"), rec("s2/k1", 0.20, case="s2"),
           rec("s1/k2", 0.10, case="s1"), rec("lone", 0.40), rec("e/k", 0.5, case=""),
           {"case_id": "skip", "skipped": "x", "case": "s9"})
B = report(rec("s1/k1", 0.10), rec("s1/k2", 0.15, case="other"), rec("s2/k1", 0.25),
           rec("lone", 0.10), rec("e/k", 0.3), rec("skip", 0.1))


# bootstrap_ci_grouped

def test_grouped_is_the_bootstrap_of_whole_groups():
    groups = [[0.1, 0.2], [-0.3], [0.05, 0.0, 0.4]]
    rng = random.Random(7)
    means = []
    for _ in range(200):
        values = [v for g in rng.choices(groups, k=3) for v in g]
        means.append(sum(values) / len(values))
    expected = (quantile(means, 0.05), quantile(means, 0.95))
    assert bootstrap_ci_grouped(groups, resamples=200, seed=7, level=0.9) == expected


def test_grouped_with_one_value_per_group_equals_bootstrap_ci():
    diffs = [0.1, -0.2, 0.05, 0.3, -0.1]
    for kwargs in ({}, {"resamples": 300, "seed": 4, "level": 0.8}):
        assert (bootstrap_ci_grouped([[d] for d in diffs], **kwargs)
                == bootstrap_ci(diffs, **kwargs))


def test_grouped_no_groups_and_not_modified():
    assert bootstrap_ci_grouped([]) is None
    groups = [[0.1, 0.2], [0.3]]
    saved = copy.deepcopy(groups)
    bootstrap_ci_grouped(groups, resamples=20)
    assert groups == saved


def test_grouped_errors_in_order():
    with pytest.raises(ValueError):
        bootstrap_ci_grouped([[1.0]], resamples=0)
    with pytest.raises(ValueError):
        bootstrap_ci_grouped([[1.0]], level=1)
    with pytest.raises(ValueError):
        bootstrap_ci_grouped([[1.0]], level=float("nan"))
    with pytest.raises(ValueError, match="group 1"):
        bootstrap_ci_grouped([[1.0], [], []], resamples=5)
    with pytest.raises(ValueError, match="resamples"):
        bootstrap_ci_grouped([[]], resamples=0, level=2)
    with pytest.raises(ValueError, match="level"):
        bootstrap_ci_grouped([[]], level=0)


# compare

def test_records_is_the_default_and_adds_nothing():
    result = compare(A, B, resamples=100)
    assert result == compare(A, B, resamples=100, resample="records")
    assert "resample" not in result and "groups" not in result
    assert all(set(c) == {"case_id", "a", "b", "diff"} for c in result["cases"])


def test_cases_groups_pairs_by_the_case_of_a():
    records = compare(A, B, resamples=100, seed=2)
    result = compare(A, B, resamples=100, seed=2, resample="cases")
    for key in ("metric", "n", "mean_a", "mean_b", "mean_diff", "b_better", "a_better",
                "ties"):
        assert result[key] == records[key]
    assert [c["case_id"] for c in result["cases"]] == [c["case_id"] for c in records["cases"]]
    assert [c["group"] for c in result["cases"]] == ["s1", "s2", "s1", "lone", "e/k"]
    assert [c["diff"] for c in result["cases"]] == [c["diff"] for c in records["cases"]]
    assert list(result)[-2:] == ["resample", "groups"]
    assert list(result["cases"][0]) == ["case_id", "a", "b", "diff", "group"]
    assert result["resample"] == "cases" and result["groups"] == 4
    d = {c["case_id"]: c["diff"] for c in result["cases"]}
    groups = [[d["s1/k1"], d["s1/k2"]], [d["s2/k1"]], [d["lone"]], [d["e/k"]]]
    assert (result["ci_low"], result["ci_high"]) == bootstrap_ci_grouped(groups, 100, 2)


def test_cases_agrees_with_records_when_every_case_is_one_record():
    a = report(rec("x", 0.1), rec("y", 0.4), rec("z", 0.2))
    b = report(rec("x", 0.3), rec("y", 0.1), rec("z", 0.2))
    records = compare(a, b, resamples=200, seed=3)
    cases = compare(a, b, resamples=200, seed=3, resample="cases")
    assert (cases["ci_low"], cases["ci_high"]) == (records["ci_low"], records["ci_high"])
    assert cases["groups"] == 3


def test_cases_without_pairs():
    result = compare(report(), report(), resample="cases")
    assert result["groups"] == 0 and result["ci_low"] is None and result["ci_high"] is None
    assert result["resample"] == "cases" and result["cases"] == []


def test_bad_resample_is_checked_after_metric_and_before_pairing():
    twice = report(rec("a", 0.1), rec("a", 0.2))
    with pytest.raises(ValueError, match="'sometimes'"):
        compare(twice, twice, resample="sometimes")
    with pytest.raises(ValueError, match="metric"):
        compare(twice, twice, metric="accuracy", resample="sometimes")
    with pytest.raises(ValueError, match="scored twice"):
        compare(twice, twice, resample="cases")
    with pytest.raises(ValueError):
        compare(A, B, resample=None)


# CLI

def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_cli_resample_cases_both_forms(tmp_path, capsys):
    pa, pb = write(tmp_path / "a.json", A), write(tmp_path / "b.json", B)
    expected = compare(A, B, resamples=100, seed=1, resample="cases")
    for argv in (["compare", pa, pb], ["compare", "-a", pa, "-b", pb]):
        assert main([*argv, "--resamples", "100", "--seed", "1", "--resample", "cases"],
                    client_factory=no_client) == 0
        assert json.loads(capsys.readouterr().out) == expected


def test_cli_resample_default_and_records(tmp_path, capsys):
    pa, pb = write(tmp_path / "a.json", A), write(tmp_path / "b.json", B)
    expected = compare(A, B, resamples=100)
    assert main(["compare", pa, pb, "--resamples", "100"], client_factory=no_client) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert main(["compare", pa, pb, "--resamples", "100", "--resample", "records"],
                client_factory=no_client) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_cli_resample_bad_value_exits_2(tmp_path, capsys):
    pa, pb = write(tmp_path / "a.json", A), write(tmp_path / "b.json", B)
    assert main(["compare", pa, pb, "--resample", "groups"], client_factory=no_client) == 2
    assert main(["compare", "-a", pa, "-b", pb, "--resample", ""], client_factory=no_client) == 2
    capsys.readouterr()
