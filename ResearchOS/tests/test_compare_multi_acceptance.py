"""Acceptance tests for researchos.compare v9, written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "several files a side, training output (v9)" section of SPEC.md.
"""

from __future__ import annotations

import copy
import json

import pytest

from researchos import compare as cmp


def rec(case_id, judge, weighted=None, split="train"):
    r = {"case_id": case_id, "split": split, "brier_judge": judge}
    if weighted is not None:
        r["brier_weighted"] = weighted
    return r


TRAIN_1 = [rec("c1", 0.20, 0.25)]
TRAIN_2 = [rec("c2", 0.30, 0.10), {"case_id": "c3", "split": "train", "skipped": "identifiable"}]
EVAL = {"n": 1, "brier_judge": 0.4, "cases": [rec("c4", 0.40, 0.30, "eval")]}
BASE_1 = {"n": 2, "cases": [rec("c1", 0.25, 0.20, "eval"), rec("c2", 0.20, 0.20, "eval")]}
BASE_2 = {"n": 2, "cases": [rec("c3", 0.10, 0.10, "eval"), rec("c4", 0.45, 0.35, "eval")]}


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_load_report_reads_train_output(tmp_path):
    assert cmp.load_report(write(tmp_path / "bare.json", TRAIN_2)) == {"cases": TRAIN_2}
    wrapped = write(tmp_path / "wrapped.json", {"result": TRAIN_1, "usage": {"input_tokens": 3}})
    assert cmp.load_report(wrapped) == {"cases": TRAIN_1}
    assert cmp.load_report(write(tmp_path / "empty.json", [])) == {"cases": []}
    assert cmp.load_report(write(tmp_path / "e.json", EVAL)) == EVAL  # unchanged


@pytest.mark.parametrize("content", [
    [rec("c1", 0.2), 3], ["c1"], [None], {"result": [rec("c1", 0.2), "x"]}, {"cases": [1]},
    {"cases": [rec("c1", 0.2), []]}, {"result": {"cases": [None]}}, {"result": 5},
], ids=["number", "string", "null", "wrapped-string", "cases-number", "cases-list", "wrapped-cases-null",
        "result-number"])
def test_load_report_rejects_non_object_cases(tmp_path, content):
    path = write(tmp_path / "odd-report.json", content)
    with pytest.raises(ValueError) as info:
        cmp.load_report(path)
    assert "odd-report.json" in str(info.value)


def test_merge_reports():
    a, b = copy.deepcopy(BASE_1), copy.deepcopy(EVAL)
    merged = cmp.merge_reports([a, {"cases": TRAIN_2}, b])
    assert merged == {"cases": BASE_1["cases"] + TRAIN_2 + EVAL["cases"]}
    assert a == BASE_1 and b == EVAL
    merged["cases"].append({"case_id": "zz"})
    assert a == BASE_1 and b == EVAL
    assert cmp.merge_reports([]) == {"cases": []}
    assert cmp.merge_reports(iter([BASE_2])) == {"cases": BASE_2["cases"]}


def test_compare_on_merged_reports():
    a = cmp.merge_reports([BASE_1, BASE_2])
    b = cmp.merge_reports([{"cases": TRAIN_1}, {"cases": TRAIN_2}, EVAL])
    out = cmp.compare(a, b, resamples=200, seed=1)
    assert [c["case_id"] for c in out["cases"]] == ["c1", "c2", "c4"]
    assert out["n"] == 3 and (out["b_better"], out["a_better"], out["ties"]) == (2, 1, 0)
    with pytest.raises(ValueError):  # c1 scored twice on one side
        cmp.compare(cmp.merge_reports([BASE_1, BASE_1]), b)


def files(tmp_path):
    return {
        "t1": write(tmp_path / "t1.json", {"result": TRAIN_1}),
        "t2": write(tmp_path / "t2.json", TRAIN_2),
        "ev": write(tmp_path / "ev.json", {"result": EVAL}),
        "b1": write(tmp_path / "b1.json", BASE_1),
        "b2": write(tmp_path / "b2.json", {"result": BASE_2}),
    }


def test_cli_several_files_a_side(tmp_path, capsys):
    from researchos.__main__ import main
    f = files(tmp_path)
    argv = ["compare", "-a", f["b1"], "-a", f["b2"], "-b", f["t1"], "-b", f["t2"], "-b", f["ev"],
            "--metric", "brier_weighted", "--resamples", "300", "--seed", "4"]
    assert main(argv) == 0
    got = json.loads(capsys.readouterr().out)
    a = cmp.merge_reports([BASE_1, BASE_2])
    b = cmp.merge_reports([{"cases": TRAIN_1}, {"cases": TRAIN_2}, EVAL])
    assert got == json.loads(json.dumps(cmp.compare(a, b, metric="brier_weighted", resamples=300, seed=4)))
    out = tmp_path / "cmp.json"
    assert main(argv + ["--out", str(out)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text(encoding="utf-8")) == got
    # One file a side through -a/-b is the same as the positional form.
    assert main(["compare", "-a", f["b1"], "-b", f["ev"], "--resamples", "100"]) == 0
    flagged = json.loads(capsys.readouterr().out)
    assert main(["compare", f["b1"], f["ev"], "--resamples", "100"]) == 0
    assert json.loads(capsys.readouterr().out) == flagged


def test_cli_positional_train_output(tmp_path, capsys):
    from researchos.__main__ import main
    f = files(tmp_path)
    assert main(["compare", f["b1"], f["t2"], "--resamples", "100"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert [c["case_id"] for c in got["cases"]] == ["c2"]


def test_cli_case_scored_twice_on_one_side(tmp_path, capsys):
    from researchos.__main__ import main
    f = files(tmp_path)
    assert main(["compare", "-a", f["b1"], "-a", f["b1"], "-b", f["t1"]]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()


@pytest.mark.parametrize("args", [
    ["{b1}", "-a", "{b2}", "-b", "{t1}"],
    ["{b1}", "{t1}", "-b", "{t2}"],
    ["-a", "{b1}"],
    ["-b", "{t1}"],
    ["-a", "{b1}", "-a", "{b2}"],
    ["{b1}"],
    ["{b1}", "{t1}", "{t2}"],
    [],
], ids=["positional-and-a", "positional-and-b", "only-a", "only-b", "two-a-no-b", "one-positional",
        "three-positionals", "nothing"])
def test_cli_bad_arguments(tmp_path, capsys, args):
    from researchos.__main__ import main
    f = files(tmp_path)
    assert main(["compare"] + [a.format(**f) for a in args]) == 2
    assert capsys.readouterr().out == ""


def test_cli_missing_file_on_a_side(tmp_path, capsys):
    from researchos.__main__ import main
    f = files(tmp_path)
    assert main(["compare", "-a", f["b1"], "-a", str(tmp_path / "missing.json"), "-b", f["t1"]]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()
