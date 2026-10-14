import copy
import json
import random

import pytest
from tools.stats import quantile

from researchos.__main__ import main
from researchos.compare import bootstrap_ci, compare, load_report, merge_reports, paired


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
    {"n": 0}, {"result": {"n": 0}}, {"result": None}, {"result": "cases"}, {"result": 3},
    {"cases": {"a": {}}}, {"cases": None}, {"result": {"cases": "a"}}, "cases", 3, None, True,
    # v9: in every form the cases must be a list of objects
    [1, 2], ["a"], [None], [True], [[]], [rec("a", 0.1), 3], [rec("a", 0.1), [rec("b", 0.2)]],
    {"result": [1, 2]}, {"result": [rec("a", 0.1), None]}, {"cases": [rec("a", 0.1), "b"]},
    {"result": {"cases": [0.1]}},
])
def test_load_report_rejects_other_json(tmp_path, data):
    path = write_json(tmp_path / "r.json", data)
    with pytest.raises(ValueError, match="r.json"):  # the message names the file
        load_report(path)


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


# load_report: training output (v9)

TRAIN = [rec("a", 0.2, split="train", lessons_rejected=0),
         {"case_id": "b", "split": "train", "skipped": "identifiable"},
         rec("c", 0.4, split="train", lessons_rejected=1)]


def test_load_report_reads_train_output(tmp_path):
    # what `train` prints: a list of records, bare or wrapped as {"result": [...], ...}
    assert load_report(write_json(tmp_path / "train.json", TRAIN)) == {"cases": TRAIN}
    wrapped = {"result": TRAIN, "argv": ["train"]}
    assert load_report(write_json(tmp_path / "wrapped.json", wrapped)) == {"cases": TRAIN}
    assert load_report(write_json(tmp_path / "empty.json", [])) == {"cases": []}
    assert load_report(write_json(tmp_path / "empty_wrapped.json", {"result": []})) == {
        "cases": []}
    utf16 = tmp_path / "utf16.json"
    utf16.write_bytes(json.dumps(TRAIN).encode("utf-16"))  # as PowerShell's `>` writes it
    assert load_report(utf16) == {"cases": TRAIN}


def test_load_report_top_level_cases_still_come_first(tmp_path):
    rep = {"cases": [rec("a", 0.1)], "result": TRAIN}
    assert load_report(write_json(tmp_path / "both.json", rep)) == rep


# merge_reports

def test_merge_reports_joins_the_cases_in_order():
    first = report(rec("a", 0.1), skipped("b"))
    train = {"cases": TRAIN}
    last = report(rec("z", 0.3))
    assert merge_reports([first, train, last]) == {
        "cases": [rec("a", 0.1), skipped("b"), *TRAIN, rec("z", 0.3)]}
    assert merge_reports([last, first]) == {"cases": [rec("z", 0.3), rec("a", 0.1), skipped("b")]}
    assert merge_reports([first]) == {"cases": [rec("a", 0.1), skipped("b")]}
    assert merge_reports(iter([first, last])) == {"cases": [*first["cases"], *last["cases"]]}


def test_merge_reports_without_reports_or_cases():
    assert merge_reports([]) == {"cases": []}
    assert merge_reports([report(), {"cases": []}]) == {"cases": []}


def test_merge_reports_leaves_the_reports_alone():
    first = report(rec("a", 0.1, brier_by_perspective={"growth": 0.2}))
    second = {"cases": [rec("b", 0.3)]}
    before = copy.deepcopy([first, second])
    merged = merge_reports([first, second])
    assert [first, second] == before
    merged["cases"][0]["brier_judge"] = 0.9
    merged["cases"][0]["brier_by_perspective"]["growth"] = 0.9
    merged["cases"].append(rec("c", 0.5))
    assert [first, second] == before


def test_merge_reports_rejects_what_is_not_a_report():
    for reports in ([report(), {"n": 1}], [TRAIN], [None], [{"cases": "a"}]):
        with pytest.raises(ValueError):
            merge_reports(reports)


def test_compare_counts_a_case_scored_in_two_merged_reports_as_scored_twice():
    side_a = merge_reports([report(rec("a", 0.1)), {"cases": [rec("a", 0.2, split="train")]}])
    with pytest.raises(ValueError, match="twice"):
        compare(side_a, report(rec("a", 0.3)))
    # skipped in one report and scored in the other: scored once
    side_a = merge_reports([report(skipped("a")), {"cases": [rec("a", 0.2, split="train")]}])
    assert compare(side_a, report(rec("a", 0.3)))["n"] == 1


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


# CLI: several files a side (v9)

def test_cli_compare_sides_of_several_files(tmp_path, capsys):
    # walk-forward training writes one train output per case; held-out cases come from evaluate
    a1 = write_json(tmp_path / "a1.json", [rec("x", 0.30, split="train")])
    a2 = write_json(tmp_path / "a2.json", {"result": [skipped("y")], "argv": ["train"]})
    a3 = write_json(tmp_path / "a3.json", A)
    b1 = write_json(tmp_path / "b1.json", {"result": B})
    b2 = tmp_path / "b2.json"
    b2.write_bytes(json.dumps([rec("x", 0.10), rec("y", 0.2)]).encode("utf-16"))
    side_a = {"cases": [rec("x", 0.30, split="train"), skipped("y"), *A["cases"]]}
    side_b = {"cases": [*B["cases"], rec("x", 0.10), rec("y", 0.2)]}
    code = main(["compare", "-a", str(a1), "-a", str(a2), "-b", str(b1), "-a", str(a3),
                 "-b", str(b2), "--resamples", "300", "--seed", "5"], client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    result = json.loads(captured.out)
    assert result == compare(side_a, side_b, resamples=300, seed=5)
    assert [c["case_id"] for c in result["cases"]] == ["x", "a", "b", "c", "e"]
    # a side's files are merged in the order given
    assert main(["compare", "-a", str(a3), "-a", str(a1), "-b", str(b2), "-b", str(b1),
                 "--resamples", "300", "--seed", "5"], client_factory=no_client) == 0
    result = json.loads(capsys.readouterr().out)
    side_a = {"cases": [*A["cases"], rec("x", 0.30, split="train")]}
    side_b = {"cases": [rec("x", 0.10), rec("y", 0.2), *B["cases"]]}
    assert result == compare(side_a, side_b, resamples=300, seed=5)
    assert [c["case_id"] for c in result["cases"]] == ["a", "b", "c", "e", "x"]


def test_cli_compare_sides_metric_and_out(tmp_path, capsys):
    a1 = write_json(tmp_path / "a1.json", report(rec("x", judge=0.1, weighted=0.4)))
    a2 = write_json(tmp_path / "a2.json", [rec("y", judge=0.2, weighted=0.3, split="train")])
    b = report(rec("x", judge=0.3, weighted=0.1), rec("y", judge=0.2, weighted=0.1))
    pb = write_json(tmp_path / "b.json", b)
    side_a = {"cases": [rec("x", judge=0.1, weighted=0.4),
                        rec("y", judge=0.2, weighted=0.3, split="train")]}
    out = tmp_path / "comparison.json"
    code = main(["compare", "--metric", "brier_weighted", "-a", str(a1), "-b", str(pb),
                 "-a", str(a2), "--resamples", "200", "--seed", "2", "--out", str(out)],
                client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == ""
    expected = compare(side_a, b, metric="brier_weighted", resamples=200, seed=2)
    assert expected["n"] == 2
    assert json.loads(out.read_text(encoding="utf-8")) == expected
    # one file a side with -a/-b is the same comparison as A.json B.json
    assert main(["compare", "-a", str(a1), "-b", str(pb)], client_factory=no_client) == 0
    by_option = json.loads(capsys.readouterr().out)
    assert main(["compare", str(a1), str(pb)], client_factory=no_client) == 0
    assert json.loads(capsys.readouterr().out) == by_option


def test_cli_compare_two_files_with_options_between_them(tmp_path, capsys):
    pa = write_json(tmp_path / "a.json", A)
    pb = write_json(tmp_path / "b.json", B)
    expected = compare(A, B, resamples=300, seed=5)
    for argv in (["compare", str(pa), "--seed", "5", str(pb), "--resamples", "300"],
                 ["compare", "--seed", "5", str(pa), "--resamples", "300", str(pb)],
                 ["compare", "--seed", "5", "--resamples", "300", str(pa), str(pb)]):
        assert main(argv, client_factory=no_client) == 0, argv
        assert json.loads(capsys.readouterr().out) == expected


def test_cli_compare_train_output_against_evaluate(tmp_path, capsys):
    held_out = report(rec("a", 0.1), skipped("b"), rec("c", 0.5))
    pa = write_json(tmp_path / "train.json", TRAIN)
    pb = write_json(tmp_path / "eval.json", {"result": held_out, "argv": ["evaluate"]})
    assert main(["compare", str(pa), str(pb), "--resamples", "100"],
                client_factory=no_client) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == compare({"cases": TRAIN}, held_out, resamples=100)
    assert [c["case_id"] for c in result["cases"]] == ["a", "c"]


def test_cli_compare_sides_bad_arguments_exit_2(tmp_path, capsys):
    pa = str(write_json(tmp_path / "a.json", A))
    pb = str(write_json(tmp_path / "b.json", B))
    for argv in (
        # positional files together with -a/-b
        [pa, pb, "-a", pa, "-b", pb], [pa, "-a", pa, "-b", pb], ["-a", pa, "-b", pb, pa],
        ["-a", pa, pa, "-b", pb], [pa, pb, "-a", pa], [pa, pb, "-b", pb],
        # only one of -a and -b
        ["-a", pa], ["-a", pa, "-a", pb], ["-b", pb], ["-b", pa, "-b", pb],
        # -a or -b without a file
        ["-a", pa, "-b"], ["-a", "-b", pb],
        # a number of positional files other than two
        [], [pa], [pa, pb, pa], [pa, "--seed", "1", pb, pa],
        [pa, "--seed", "1", pb, "--seed", "2", pa],
        # the other options are still checked with -a/-b
        ["-a", pa, "-b", pb, "--metric", "accuracy_judge"],
        ["-a", pa, "-b", pb, "--resamples", "0"], ["-a", pa, "-b", pb, "--seed", "x"],
        ["-a", pa, "-b", pb, "--out"],
        # unknown options, after, between or before the files
        ["-a", pa, "-b", pb, "--bogus"], [pa, "--bogus", pb], ["--bogus", pa, pb],
    ):
        code = main(["compare", *argv], client_factory=no_client)
        captured = capsys.readouterr()
        assert code == 2, argv
        assert captured.out == ""
        assert captured.err


def test_cli_compare_sides_unusable_files_exit_1(tmp_path, capsys):
    good = write_json(tmp_path / "good.json", A)
    other = write_json(tmp_path / "other.json", B)
    again = write_json(tmp_path / "again.json", [rec("c", 0.6, split="train")])  # c is in A too
    numbers = write_json(tmp_path / "numbers.json", [rec("q", 0.1), 3])
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("[", encoding="utf-8")
    missing = tmp_path / "missing.json"
    for argv in (
        # a case scored in two files of the same side is a case scored twice
        ["-a", good, "-a", again, "-b", other], ["-a", again, "-a", good, "-b", other],
        ["-a", good, "-b", other, "-b", other], ["-a", good, "-a", good, "-b", other],
        ["-a", other, "-b", again, "-b", good],
        # files that cannot be read or are not reports
        ["-a", good, "-b", other, "-b", missing], ["-a", missing, "-b", other],
        ["-a", good, "-a", numbers, "-b", other], ["-a", good, "-b", bad_json],
        ["-a", good, "-b", tmp_path], [good, numbers],
    ):
        code = main(["compare", *map(str, argv)], client_factory=no_client)
        captured = capsys.readouterr()
        assert code == 1, argv
        assert captured.out == ""
        assert captured.err.startswith("error:")
    main(["compare", "-a", str(good), "-a", str(again), "-b", str(other)],
         client_factory=no_client)
    assert "'c'" in capsys.readouterr().err


def test_cli_compare_case_skipped_in_one_file_and_scored_in_another(tmp_path, capsys):
    # skipped records carry no score, so the case is scored once on that side
    skipped_c = write_json(tmp_path / "skipped.json", [skipped("c"), rec("new", 0.2)])
    good = write_json(tmp_path / "good.json", A)
    other = write_json(tmp_path / "other.json", B)
    assert main(["compare", "-a", str(skipped_c), "-a", str(good), "-b", str(other),
                 "--resamples", "100"], client_factory=no_client) == 0
    result = json.loads(capsys.readouterr().out)
    assert [c["case_id"] for c in result["cases"]] == ["a", "b", "c", "e"]
