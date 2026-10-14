import json
import random

import pytest
from tools.stats import quantile

from researchos.__main__ import main
from researchos.compare import bootstrap_ci, compare, load_report, paired


def rec(case_id, judge=None, weighted=None, **extra):
    record = {"case_id": case_id, "split": "eval", **extra}
    if judge is not None:
        record["brier_judge"] = judge
    if weighted is not None:
        record["brier_weighted"] = weighted
    return record


def skipped(case_id):
    return {"case_id": case_id, "split": "eval", "skipped": "identifiable"}


def report(*records):
    return {"cases": list(records), "n": len(records)}


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def no_client():
    raise AssertionError("compare must not build a client")


A = report(rec("a", 0.30), rec("b", 0.10), rec("c", 0.20), skipped("d"), rec("e", 0.40),
           rec("only_a", 0.5))
B = report(rec("e", 0.10), rec("only_b", 0.3), rec("c", 0.20), rec("b", 0.25), rec("a", 0.05),
           rec("d", 0.5))


# load_report

def test_load_report_plain_and_wrapped(tmp_path):
    rep = report(rec("a", 0.1), skipped("b"))
    assert load_report(write_json(tmp_path / "plain.json", rep)) == rep
    assert load_report(str(tmp_path / "plain.json")) == rep
    wrapped = {"result": rep, "argv": ["evaluate"], "when": "2026-10-01"}
    assert load_report(write_json(tmp_path / "wrapped.json", wrapped)) == rep


@pytest.mark.parametrize("data", [
    [], [rep for rep in [report()]], {"n": 0}, {"result": {"n": 0}}, {"result": [report()]},
    {"result": None}, {"cases": {"a": {}}}, "cases", 3, None,
])
def test_load_report_rejects_other_json(tmp_path, data):
    with pytest.raises(ValueError):
        load_report(write_json(tmp_path / "r.json", data))


def test_load_report_rejects_bad_json_and_reports_missing_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_report(bad)
    cp1252 = tmp_path / "cp1252.json"
    cp1252.write_bytes(json.dumps(report(rec("Soci\xe9t\xe9", 0.1)),
                                  ensure_ascii=False).encode("cp1252"))
    with pytest.raises(ValueError, match="cp1252.json"):
        load_report(cp1252)
    with pytest.raises(FileNotFoundError):
        load_report(tmp_path / "nope.json")


def test_load_report_reads_utf16_and_byte_order_marks(tmp_path):
    # What Windows PowerShell 5.1 writes with `>`, and with Out-File -Encoding utf8 (v8).
    rep = report(rec("a", 0.1), skipped("b"))
    utf16 = tmp_path / "utf16.json"
    utf16.write_bytes(json.dumps(rep).encode("utf-16"))
    assert load_report(utf16) == rep
    bom = tmp_path / "bom.json"
    bom.write_bytes(json.dumps({"result": rep}).encode("utf-8-sig"))
    assert load_report(bom) == rep


# paired

def test_paired_follows_a_and_keeps_cases_scored_in_both():
    assert paired(A, B) == [("a", 0.30, 0.05), ("b", 0.10, 0.25), ("c", 0.20, 0.20),
                            ("e", 0.40, 0.10)]
    assert [p[0] for p in paired(B, A)] == ["e", "c", "b", "a"]


def test_paired_needs_the_metric_as_a_number():
    a = report({"case_id": "t", "brier_judge": True}, {"case_id": "f", "brier_judge": False},
               {"case_id": "n", "brier_judge": None}, {"case_id": "s", "brier_judge": "0.1"},
               {"case_id": "nan", "brier_judge": float("nan")}, skipped("k"),
               {"case_id": "i", "brier_judge": 0}, rec("x", 0.25))
    b = report(*(rec(cid, 0.5) for cid in ("t", "f", "n", "s", "nan", "k", "i", "x")))
    assert paired(a, b) == [("i", 0.0, 0.5), ("x", 0.25, 0.5)]
    assert [p[0] for p in paired(b, a)] == ["i", "x"]


def test_paired_uses_the_metric_given():
    a = report(rec("a", judge=0.1, weighted=0.2), rec("b", judge=0.3))
    b = report(rec("a", judge=0.2, weighted=0.1), rec("b", judge=0.3, weighted=0.0))
    assert paired(a, b, metric="brier_weighted") == [("a", 0.2, 0.1)]
    assert paired(a, b) == [("a", 0.1, 0.2), ("b", 0.3, 0.3)]


def test_paired_rejects_a_case_scored_twice():
    twice = report(rec("a", 0.1), rec("z", 0.3), rec("a", 0.2))
    other = report(rec("a", 0.1))
    with pytest.raises(ValueError):
        paired(twice, other)
    with pytest.raises(ValueError):
        paired(other, twice)
    # only scored records count: a skipped record may share the id of a scored one
    once = report(skipped("a"), rec("a", 0.2), skipped("a"))
    assert paired(once, other) == [("a", 0.2, 0.1)]
    # scored twice on another metric only is not scored twice on this one
    weighted_twice = report(rec("a", judge=0.2, weighted=0.1), rec("a", weighted=0.3))
    assert paired(weighted_twice, other) == [("a", 0.2, 0.1)]


# bootstrap_ci

def test_bootstrap_ci_is_the_percentile_bootstrap_of_the_mean():
    diffs = [-0.2, 0.05, -0.1, 0.3, -0.15]
    rng = random.Random(7)
    means = []
    for _ in range(500):
        sample = rng.choices(diffs, k=len(diffs))
        means.append(sum(sample) / len(sample))
    expected = (quantile(means, (1 - 0.9) / 2), quantile(means, 1 - (1 - 0.9) / 2))
    assert bootstrap_ci(diffs, resamples=500, seed=7, level=0.9) == pytest.approx(expected)


def test_bootstrap_ci_is_seeded():
    diffs = [-0.2, 0.05, -0.1, 0.3, -0.15, 0.0, -0.05]
    first = bootstrap_ci(diffs, resamples=300, seed=1)
    assert bootstrap_ci(diffs, resamples=300, seed=1) == first
    assert bootstrap_ci(diffs, resamples=300, seed=2) != first
    assert bootstrap_ci(diffs) == bootstrap_ci(diffs, resamples=10000, seed=0, level=0.95)


def test_bootstrap_ci_bounds():
    diffs = [-0.2, 0.05, -0.1, 0.3, -0.15]
    low, high = bootstrap_ci(diffs, resamples=2000)
    assert -0.2 <= low <= high <= 0.3
    narrow = bootstrap_ci(diffs, resamples=2000, level=0.5)
    wide = bootstrap_ci(diffs, resamples=2000, level=0.99)
    assert wide[0] <= low <= narrow[0] <= narrow[1] <= high <= wide[1]
    assert bootstrap_ci([0.1, 0.1, 0.1], resamples=50) == pytest.approx((0.1, 0.1))
    assert bootstrap_ci([-0.25], resamples=1) == (-0.25, -0.25)


def test_bootstrap_ci_without_diffs_and_bad_arguments():
    assert bootstrap_ci([]) is None
    for kwargs in ({"resamples": 0}, {"resamples": -3}, {"level": 0}, {"level": 1},
                   {"level": 1.5}, {"level": -0.1}, {"level": float("nan")}):
        with pytest.raises(ValueError):
            bootstrap_ci([0.1, -0.1], **kwargs)
        with pytest.raises(ValueError):
            bootstrap_ci([], **kwargs)


# compare

def test_compare_pairs_cases_and_counts_b_minus_a():
    result = compare(A, B, resamples=400, seed=3)
    assert result["metric"] == "brier_judge"
    assert result["n"] == 4
    assert [(c["case_id"], c["a"], c["b"]) for c in result["cases"]] == [
        ("a", 0.30, 0.05), ("b", 0.10, 0.25), ("c", 0.20, 0.20), ("e", 0.40, 0.10)]
    diffs = [c["diff"] for c in result["cases"]]
    assert diffs == pytest.approx([-0.25, 0.15, 0.0, -0.30])
    assert all(set(c) == {"case_id", "a", "b", "diff"} for c in result["cases"])
    assert result["mean_a"] == pytest.approx(0.25)
    assert result["mean_b"] == pytest.approx(0.15)
    assert result["mean_diff"] == pytest.approx(-0.10)
    assert (result["b_better"], result["a_better"], result["ties"]) == (2, 1, 1)
    assert (result["ci_low"], result["ci_high"]) == bootstrap_ci(diffs, resamples=400, seed=3)
    assert result["ci_low"] <= result["mean_diff"] <= result["ci_high"]
    assert set(result) == {"metric", "n", "mean_a", "mean_b", "mean_diff", "ci_low", "ci_high",
                           "b_better", "a_better", "ties", "cases"}


def test_compare_other_metric_and_defaults():
    a = report(rec("x", judge=0.1, weighted=0.4), rec("y", judge=0.2, weighted=0.3))
    b = report(rec("x", judge=0.3, weighted=0.1), rec("y", judge=0.2, weighted=0.1))
    result = compare(a, b, metric="brier_weighted")
    assert result["metric"] == "brier_weighted"
    assert (result["b_better"], result["a_better"], result["ties"]) == (2, 0, 0)
    diffs = [c["diff"] for c in result["cases"]]
    assert (result["ci_low"], result["ci_high"]) == bootstrap_ci(diffs)
    judged = compare(a, b)
    assert (judged["b_better"], judged["a_better"], judged["ties"]) == (0, 1, 1)


def test_compare_without_pairs():
    empty = {"metric": "brier_judge", "n": 0, "mean_a": None, "mean_b": None,
             "mean_diff": None, "ci_low": None, "ci_high": None, "b_better": 0,
             "a_better": 0, "ties": 0, "cases": []}
    assert compare(report(rec("a", 0.1)), report(rec("b", 0.2))) == empty
    assert compare(report(), report(skipped("a"))) == empty


def test_compare_rejects_bad_metric_and_arguments():
    for metric in ("accuracy_judge", "correct_judge", "n", "", "Brier_judge", "brier"):
        with pytest.raises(ValueError):
            compare(A, B, metric=metric)
    with pytest.raises(ValueError):
        compare(A, B, resamples=0)
    with pytest.raises(ValueError):
        compare(report(rec("a", 0.1), rec("a", 0.2)), B)


# CLI

def test_cli_compare_prints_the_comparison(tmp_path, capsys):
    pa = write_json(tmp_path / "a.json", A)
    pb = write_json(tmp_path / "b.json", {"result": B, "argv": ["evaluate"]})
    code = main(["compare", str(pa), str(pb), "--resamples", "300", "--seed", "5"],
                client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == compare(A, B, resamples=300, seed=5)


def test_cli_compare_metric_and_defaults(tmp_path, capsys):
    a = report(rec("x", judge=0.1, weighted=0.4), rec("y", judge=0.2, weighted=0.3))
    b = report(rec("x", judge=0.3, weighted=0.1), rec("y", judge=0.2, weighted=0.1))
    pa = write_json(tmp_path / "a.json", a)
    pb = write_json(tmp_path / "b.json", b)
    assert main(["compare", str(pa), str(pb), "--metric", "brier_weighted"],
                client_factory=no_client) == 0
    assert json.loads(capsys.readouterr().out) == compare(a, b, metric="brier_weighted")
    assert main(["compare", str(pa), str(pb)], client_factory=no_client) == 0
    assert json.loads(capsys.readouterr().out) == compare(a, b)


def test_cli_compare_bad_arguments_exit_2(tmp_path, capsys):
    pa = str(write_json(tmp_path / "a.json", A))
    pb = str(write_json(tmp_path / "b.json", B))
    missing = str(tmp_path / "missing.json")
    for argv in (
        ["compare"],
        ["compare", pa],
        ["compare", pa, pb, "extra"],
        ["compare", pa, pb, "--metric", "accuracy_judge"],
        ["compare", pa, pb, "--metric", "n"],
        ["compare", pa, pb, "--metric", ""],
        ["compare", missing, missing, "--metric", "correct_judge"],
        ["compare", pa, pb, "--resamples", "0"],
        ["compare", pa, pb, "--resamples", "-5"],
        ["compare", pa, pb, "--resamples", "many"],
        ["compare", pa, pb, "--resamples", "2.5"],
        ["compare", pa, pb, "--seed", "x"],
        ["compare", pa, pb, "--out"],
    ):
        assert main(argv, client_factory=no_client) == 2, argv
    assert capsys.readouterr().out == ""


def test_cli_compare_unusable_reports_exit_1(tmp_path, capsys):
    good = write_json(tmp_path / "good.json", A)
    missing = tmp_path / "missing.json"
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    not_report = write_json(tmp_path / "list.json", [1, 2])
    twice = write_json(tmp_path / "twice.json", report(rec("a", 0.1), rec("a", 0.2)))
    for path_a, path_b in ((good, missing), (missing, good), (good, bad_json),
                           (not_report, good), (good, twice), (good, tmp_path)):
        code = main(["compare", str(path_a), str(path_b)], client_factory=no_client)
        captured = capsys.readouterr()
        assert code == 1, (path_a, path_b)
        assert captured.out == ""
        assert captured.err.startswith("error:")


def test_cli_compare_out_writes_the_file_and_prints_nothing(tmp_path, capsys):
    pa = write_json(tmp_path / "a.json", A)
    pb = tmp_path / "b.json"
    pb.write_bytes(json.dumps({"result": B}).encode("utf-16"))  # as PowerShell's `>` writes it
    out = tmp_path / "comparison.json"
    code = main(["compare", str(pa), str(pb), "--resamples", "300", "--seed", "5",
                 "--out", str(out)], client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == ""
    expected = compare(A, B, resamples=300, seed=5)
    assert out.read_bytes() == (json.dumps(expected, indent=2, ensure_ascii=False)
                                + "\n").encode("utf-8")


def test_cli_compare_unwritable_out_exits_1(tmp_path, capsys):
    pa = str(write_json(tmp_path / "a.json", A))
    pb = str(write_json(tmp_path / "b.json", B))
    for out in (tmp_path, tmp_path / "no-such-dir" / "c.json"):
        code = main(["compare", pa, pb, "--resamples", "50", "--out", str(out)],
                    client_factory=no_client)
        captured = capsys.readouterr()
        assert code == 1, out
        assert captured.out == ""
        assert captured.err.startswith("error:") and str(out) in captured.err
