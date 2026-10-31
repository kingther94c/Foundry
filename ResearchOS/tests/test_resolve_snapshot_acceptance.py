"""Acceptance tests for resolving a snapshot (v25), written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "Resolving a snapshot (v25)" section of SPEC.md. The expected changes were
worked out by hand from the series below, whose values are exact in binary where it matters.
"""

from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path

import pytest
from tools.finance import level_change

from researchos import cases, snapshot
from researchos.__main__ import main
from researchos.cases import Target, TargetOutcome

AS_OF = "2026-10-30"
MAIN = "macro-2026-10-30"
ODD = "odd-2026-10-30"
KEYS = ["DGS10_1w", "DGS10_1m", "DGS10_3m", "SP500_1w", "SP500_1m", "VIXCLS_1w"]

SERIES_1 = {
    "DGS10": [("2026-10-29", 4.0625), ("2026-11-05", 3.9375), ("2026-11-06", 3.875),
              ("2026-11-09", 3.75), ("2026-11-27", 4.25)],
    "SP500": [("2026-11-09", 7000.0), ("2026-10-28", 6800.0), ("2026-11-05", 6970.0)],
    "VIXCLS": [("2026-10-29", 20.0), ("2026-11-06", 20.0), ("2026-11-09", 21.0)],
}
SERIES_2 = {
    "DGS10": [("2026-10-29", 4.0625), ("2026-11-05", 3.9375), ("2026-11-06", 9.0),
              ("2026-11-09", 3.75), ("2026-11-27", 4.25), ("2026-12-01", 4.5)],
    "SP500": [("2026-10-28", 6800.0), ("2026-11-05", 6970.0), ("2026-11-09", 7000.0),
              ("2026-11-30", 7140.0), ("2026-12-02", 7200.0)],
    "VIXCLS": [("2026-10-29", 20.0), ("2026-11-06", 30.0), ("2026-11-09", 21.0)],
}


def main_case():
    return cases.macro_case(MAIN, AS_OF, [
        Target("DGS10", "10-year Treasury yield", "bp", ["1w", "1m", "3m"], "2026-10-29", 4.0,
               {"1w": -2.0}),
        Target("SP500", "S&P 500", "return", ["1w", "1m"], "2026-10-28", 6800.0),
        Target("VIXCLS", "VIX", "points", ["1w"], "2026-10-29", 20.0),
    ], [cases.Document("dashboard", "Markets as of 2026-10-30", "a table")])


def odd_case():
    return cases.macro_case(ODD, AS_OF, [
        Target("WTI", "WTI crude", "return", ["1w"], "2026-10-29", -37.63),
        Target("LATE", "Late file", "bp", ["1w"], "2026-10-29", 4.0),
        Target("GAP", "Gap", "bp", ["1w"], "2026-10-29", 4.0),
        Target("SAME", "Same day", "bp", ["1w"], "2026-10-29", 4.0),
        Target("NOFILE", "No file", "points", ["1w", "1m"], "2026-10-29", 1.0),
    ], [cases.Document("dashboard", "Markets as of 2026-10-30", "a table")])


ODD_SERIES = {
    "WTI": [("2026-10-29", -37.63), ("2026-11-06", 10.0), ("2026-11-09", 11.0)],
    "LATE": [("2026-11-20", 4.5)],
    "GAP": [("2026-10-20", 3.0), ("2026-11-20", 4.5)],
    "SAME": [("2026-10-29", 4.5), ("2026-11-20", 4.75)],
}


def save(root, case):
    cases.save_case(root, case)
    cases.save_resolution(root, case.case_id, cases.resolution_manifest(case))
    return case


def write_series(directory, data):
    directory.mkdir(parents=True, exist_ok=True)
    for name, pairs in data.items():
        (directory / f"{name}.json").write_text(json.dumps([list(p) for p in pairs]),
                                               encoding="utf-8")
    return directory


def snapshot_files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*"))
            if p.is_file()}


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "cases"
    save(r, main_case())
    save(r, odd_case())
    return r


@pytest.fixture
def series_dir(tmp_path):
    return write_series(tmp_path / "series", {**SERIES_1, **ODD_SERIES})


# --- the first resolution ---------------------------------------------------------------------

def test_first_resolution(root, series_dir):
    result = snapshot.resolve_case(root, MAIN, series_dir=series_dir)
    assert result == {"case_id": MAIN, "resolved": ["DGS10_1w", "SP500_1w", "VIXCLS_1w"],
                      "new": ["DGS10_1w", "SP500_1w", "VIXCLS_1w"],
                      "pending": ["DGS10_1m", "DGS10_3m", "SP500_1m"], "unmeasurable": []}
    json.dumps(result)


def test_the_outcomes(root, series_dir):
    snapshot.resolve_case(root, MAIN, series_dir=series_dir)
    outcome = cases.load_outcome(root, MAIN)
    assert outcome.abnormal_return_1d is None
    assert list(outcome.targets) == ["DGS10_1w", "SP500_1w", "VIXCLS_1w"]
    # the start is the level the case recorded (4.0), not the series' 4.0625 now
    assert outcome.targets["DGS10_1w"] == TargetOutcome(
        "DGS10", "1w", "bp", "2026-10-29", 4.0, "2026-11-06", 3.875, -12.5, False)
    # nothing on 2026-11-06, something after it: the end is the last before
    sp = outcome.targets["SP500_1w"]
    assert (sp.target, sp.horizon, sp.kind, sp.start_date, sp.start, sp.end_date, sp.end, sp.up) \
        == ("SP500", "1w", "return", "2026-10-28", 6800.0, "2026-11-05", 6970.0, True)
    assert sp.change == level_change(6800.0, 6970.0, "return") == pytest.approx(0.025)
    # no move is not up
    assert outcome.targets["VIXCLS_1w"] == TargetOutcome(
        "VIXCLS", "1w", "points", "2026-10-29", 20.0, "2026-11-06", 20.0, 0.0, False)


def test_the_outcome_file(root, series_dir):
    snapshot.resolve_case(root, MAIN, series_dir=series_dir)
    raw = json.loads((root / MAIN / "outcome.json").read_text(encoding="utf-8"))
    assert list(raw["targets"]) == ["DGS10_1w", "SP500_1w", "VIXCLS_1w"]
    assert raw["targets"]["DGS10_1w"] == {
        "target": "DGS10", "horizon": "1w", "kind": "bp", "start_date": "2026-10-29",
        "start": 4.0, "end_date": "2026-11-06", "end": 3.875, "change": -12.5, "up": False}
    assert raw["abnormal_return_1d"] is None and raw["notes"] == "" and raw["sources"] == []


def test_the_manifest(root, series_dir):
    snapshot.resolve_case(root, MAIN, series_dir=series_dir)
    assert cases.load_resolution(root, MAIN) == {"as_of": "2026-10-30", "targets": {
        "DGS10_1w": {"horizon_end": "2026-11-06", "resolved": True},
        "DGS10_1m": {"horizon_end": "2026-11-30", "resolved": False},
        "DGS10_3m": {"horizon_end": "2027-01-30", "resolved": False},
        "SP500_1w": {"horizon_end": "2026-11-06", "resolved": True},
        "SP500_1m": {"horizon_end": "2026-11-30", "resolved": False},
        "VIXCLS_1w": {"horizon_end": "2026-11-06", "resolved": True}}}
    case = cases.load_case(root, MAIN)
    assert cases.unresolved_targets(case, cases.load_resolution(root, MAIN)) == \
        ["DGS10_1m", "DGS10_3m", "SP500_1m"]


def test_the_case_file_is_not_changed(root, series_dir):
    before = (root / MAIN / "case.json").read_bytes()
    snapshot.resolve_case(root, MAIN, series_dir=series_dir)
    assert (root / MAIN / "case.json").read_bytes() == before


def test_resolving_again_writes_the_same_bytes(root, series_dir):
    snapshot.resolve_cases(root, [MAIN, ODD], series_dir=series_dir)
    first = snapshot_files(root)
    again = snapshot.resolve_cases(root, [MAIN, ODD], series_dir=series_dir)
    assert snapshot_files(root) == first
    assert [r["new"] for r in again] == [[], []]
    assert again[0]["resolved"] == ["DGS10_1w", "SP500_1w", "VIXCLS_1w"]


# --- later -----------------------------------------------------------------------------------

def test_a_later_resolution_keeps_what_was_resolved(root, series_dir, tmp_path):
    snapshot.resolve_case(root, MAIN, series_dir=series_dir)
    later = write_series(tmp_path / "later", SERIES_2)
    result = snapshot.resolve_case(root, MAIN, series_dir=later)
    assert result == {"case_id": MAIN,
                      "resolved": ["DGS10_1w", "DGS10_1m", "SP500_1w", "SP500_1m", "VIXCLS_1w"],
                      "new": ["DGS10_1m", "SP500_1m"], "pending": ["DGS10_3m"],
                      "unmeasurable": []}
    outcome = cases.load_outcome(root, MAIN)
    assert list(outcome.targets) == ["DGS10_1w", "DGS10_1m", "SP500_1w", "SP500_1m", "VIXCLS_1w"]
    # the 1w outcomes are kept, though the series now says 9.0 and 30.0 for 2026-11-06
    assert outcome.targets["DGS10_1w"].end == 3.875
    assert outcome.targets["VIXCLS_1w"].change == 0.0
    # 2026-11-30 has no observation, 2026-12-01 does: the end is 2026-11-27
    assert outcome.targets["DGS10_1m"] == TargetOutcome(
        "DGS10", "1m", "bp", "2026-10-29", 4.0, "2026-11-27", 4.25, 25.0, True)
    sp = outcome.targets["SP500_1m"]
    assert (sp.end_date, sp.end, sp.up) == ("2026-11-30", 7140.0, True)
    assert sp.change == level_change(6800.0, 7140.0, "return")
    assert cases.unresolved_targets(cases.load_case(root, MAIN),
                                    cases.load_resolution(root, MAIN)) == ["DGS10_3m"]


def test_a_horizon_ending_on_a_saturday_waits_for_the_next_observation(root, tmp_path):
    friday = write_series(tmp_path / "friday", {**SERIES_2,
                                                "DGS10": SERIES_2["DGS10"] + [("2027-01-29", 4.5)]})
    assert snapshot.resolve_case(root, MAIN, series_dir=friday)["pending"] == ["DGS10_3m"]
    monday = write_series(tmp_path / "monday", {
        **SERIES_2, "DGS10": SERIES_2["DGS10"] + [("2027-01-29", 4.5), ("2027-02-01", 4.75)]})
    result = snapshot.resolve_case(root, MAIN, series_dir=monday)
    assert result["new"] == ["DGS10_3m"] and result["pending"] == []
    assert cases.load_outcome(root, MAIN).targets["DGS10_3m"] == TargetOutcome(
        "DGS10", "3m", "bp", "2026-10-29", 4.0, "2027-01-29", 4.5, 50.0, True)


def test_an_outcome_already_written_is_kept_exactly(root, series_dir):
    kept = TargetOutcome("DGS10", "1w", "bp", "2026-10-29", 4.0, "2026-11-06", 1.0, 999.0, True)
    cases.save_outcome(root, MAIN, cases.Outcome(notes="checked by hand", sources=["a"],
                                                 targets={"DGS10_1w": kept}))
    result = snapshot.resolve_case(root, MAIN, series_dir=series_dir)
    assert result["new"] == ["SP500_1w", "VIXCLS_1w"]
    assert result["resolved"] == ["DGS10_1w", "SP500_1w", "VIXCLS_1w"]
    outcome = cases.load_outcome(root, MAIN)
    assert outcome.targets["DGS10_1w"] == kept
    assert (outcome.notes, outcome.sources) == ("checked by hand", ["a"])


# --- pending and unmeasurable ------------------------------------------------------------------

def test_pending_and_unmeasurable(root, series_dir):
    result = snapshot.resolve_case(root, ODD, series_dir=series_dir)
    assert result == {"case_id": ODD, "resolved": ["SAME_1w"], "new": ["SAME_1w"],
                      "pending": ["NOFILE_1w", "NOFILE_1m"],
                      "unmeasurable": ["WTI_1w", "LATE_1w", "GAP_1w"]}
    outcome = cases.load_outcome(root, ODD)
    # the end may be on the start's own date
    assert outcome.targets == {"SAME_1w": TargetOutcome(
        "SAME", "1w", "bp", "2026-10-29", 4.0, "2026-10-29", 4.5, 50.0, True)}
    manifest = cases.load_resolution(root, ODD)
    assert [k for k, v in manifest["targets"].items() if v["resolved"]] == ["SAME_1w"]
    assert cases.unresolved_targets(cases.load_case(root, ODD), manifest) == \
        ["WTI_1w", "LATE_1w", "GAP_1w", "NOFILE_1w", "NOFILE_1m"]


def test_an_unmeasurable_key_resolves_once_the_file_holds_the_window(root, series_dir):
    snapshot.resolve_case(root, ODD, series_dir=series_dir)
    write_series(series_dir, {"LATE": [("2026-10-29", 4.0), ("2026-11-06", 4.25),
                                       ("2026-11-20", 4.5)]})
    result = snapshot.resolve_case(root, ODD, series_dir=series_dir)
    assert result["new"] == ["LATE_1w"]
    assert result["unmeasurable"] == ["WTI_1w", "GAP_1w"]
    assert cases.load_outcome(root, ODD).targets["LATE_1w"].change == 25.0


def test_nothing_resolved_writes_no_outcome(root, tmp_path):
    empty = tmp_path / "empty"
    before = (root / MAIN / "resolution.json").read_bytes()
    result = snapshot.resolve_case(root, MAIN, series_dir=empty)
    assert result == {"case_id": MAIN, "resolved": [], "new": [], "pending": KEYS,
                      "unmeasurable": []}
    assert not (root / MAIN / "outcome.json").exists()
    assert (root / MAIN / "resolution.json").read_bytes() == before


def test_a_missing_manifest_is_written(root, tmp_path):
    (root / MAIN / "resolution.json").unlink()
    snapshot.resolve_case(root, MAIN, series_dir=tmp_path / "empty")
    assert cases.load_resolution(root, MAIN) == cases.resolution_manifest(main_case())
    assert not (root / MAIN / "outcome.json").exists()


def test_the_series_is_the_current_file_not_a_vintage(root, tmp_path):
    d = write_series(tmp_path / "s", SERIES_1)
    (d / "DGS10@2026-10-30.json").write_text('[["2026-10-29", 4.0], ["2026-11-09", 1.0]]',
                                             encoding="utf-8")
    snapshot.resolve_case(root, MAIN, series_dir=d)
    assert cases.load_outcome(root, MAIN).targets["DGS10_1w"].end == 3.875


# --- one target, one horizon: the edges ------------------------------------------------------------

EDGES = [
    # as_of, horizon, kind, start, series, state, end_date, end, change, up
    ("2026-01-31", "1m", "bp", 4.0, [("2026-01-30", 4.0), ("2026-02-27", 4.5), ("2026-03-02", 4.625)],
     "resolved", "2026-02-27", 4.5, 50.0, True),  # Jan 31 + 1m = Sat Feb 28
    ("2026-01-31", "1m", "bp", 4.0, [("2026-01-30", 4.0), ("2026-02-27", 4.5)],
     "pending", None, None, None, None),
    ("2026-10-30", "10d", "bp", 4.0, [("2026-10-30", 4.0), ("2026-11-09", 4.25)],
     "resolved", "2026-11-09", 4.25, 25.0, True),  # an observation on the end day itself
    ("2026-10-30", "2w", "bp", 4.0, [("2026-10-30", 4.0), ("2026-11-12", 4.25)],
     "pending", None, None, None, None),
    ("2026-10-30", "1w", "points", 20.0, [("2026-11-06", 19.5), ("2026-11-06", 19.25),
                                          ("2026-11-09", 21.0)],
     "resolved", "2026-11-06", 19.25, -0.75, False),  # of two on one date, the later
    ("2026-10-30", "1w", "points", 20.0, [("2026-11-06", 20.0), ("2026-11-09", 21.0)],
     "resolved", "2026-11-06", 20.0, 0.0, False),
    ("2026-10-30", "1w", "return", 10.0, [("2026-11-06", -5.0), ("2026-11-09", 1.0)],
     "resolved", "2026-11-06", -5.0, -1.5, False),  # a negative end is measurable
    ("2026-10-30", "1w", "return", 0.0, [("2026-11-06", 5.0), ("2026-11-09", 6.0)],
     "unmeasurable", None, None, None, None),
    ("2026-10-30", "1w", "return", -2.5, [("2026-11-06", 5.0), ("2026-11-09", 6.0)],
     "unmeasurable", None, None, None, None),
    ("2026-10-30", "1w", "bp", 4.0, [("2026-10-29", 4.5), ("2026-11-09", 6.0)],
     "unmeasurable", None, None, None, None),  # the end, 2026-10-29, is before the start
    ("2026-10-30", "3m", "bp", 4.0, [("2027-01-30", 4.5)],
     "resolved", "2027-01-30", 4.5, 50.0, True),
    ("2026-10-30", "3m", "bp", 4.0, [("2027-01-29", 4.5)],
     "pending", None, None, None, None),
]


@pytest.mark.parametrize("as_of, h, kind, start, series, state, end_date, end, change, up", EDGES)
def test_edges(tmp_path, as_of, h, kind, start, series, state, end_date, end, change, up):
    root = tmp_path / "cases"
    start_date = as_of
    case = save(root, cases.macro_case("one", as_of, [
        Target("X", "x", kind, [h], start_date, start)], []))
    d = write_series(tmp_path / "series", {"X": series})
    result = snapshot.resolve_case(root, "one", series_dir=d)
    key = f"X_{h}"
    assert {k: result[k] for k in ("resolved", "pending", "unmeasurable")} == {
        "resolved": [key] if state == "resolved" else [],
        "pending": [key] if state == "pending" else [],
        "unmeasurable": [key] if state == "unmeasurable" else []}
    if state == "resolved":
        got = cases.load_outcome(root, "one").targets[key]
        assert (got.start_date, got.start, got.end_date, got.end, got.up) == (
            start_date, start, end_date, end, up)
        assert got.change == pytest.approx(change, abs=1e-12)
        assert got.change == level_change(start, end, kind)
    else:
        assert not (root / "one" / "outcome.json").exists()
    assert cases.load_resolution(root, "one")["targets"][key]["resolved"] is (state == "resolved")
    assert case.case_id == "one"


def test_cases_resolve_independently_in_one_call(root, series_dir, tmp_path):
    third = save(root, cases.macro_case("macro-2026-11-06", "2026-11-06", [
        Target("SP500", "S&P 500", "return", ["1w"], "2026-11-05", 6970.0)], []))
    result = snapshot.resolve_cases(root, [third.case_id, MAIN], series_dir=series_dir)
    assert [r["case_id"] for r in result] == [third.case_id, MAIN]
    # 2026-11-06 + 1w = 2026-11-13: nothing that late yet
    assert result[0] == {"case_id": third.case_id, "resolved": [], "new": [],
                         "pending": ["SP500_1w"], "unmeasurable": []}
    assert result[1]["new"] == ["DGS10_1w", "SP500_1w", "VIXCLS_1w"]
    assert not (root / third.case_id / "outcome.json").exists()


# --- never the forecast --------------------------------------------------------------------------

def test_the_forecast_is_never_opened(root, series_dir, monkeypatch):
    (root / MAIN / "prediction.json").write_text("not even json", encoding="utf-8")
    opened = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).name == "prediction.json":
            opened.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    result = snapshot.resolve_cases(root, [MAIN, ODD], series_dir=series_dir)
    monkeypatch.undo()
    assert opened == []
    assert result[0]["new"] == ["DGS10_1w", "SP500_1w", "VIXCLS_1w"]
    assert (root / MAIN / "prediction.json").read_text(encoding="utf-8") == "not even json"


# --- refusals: nothing written ------------------------------------------------------------------

def earnings_case(root, case_id="earn-1"):
    case = cases.Case(case_id, "Acme", "ACME", [], "Q3 FY2026", "2026-10-29",
                      [cases.Document("release", "Q3", "text")])
    cases.save_case(root, case, cases.Outcome(0.03))
    return case


@pytest.mark.parametrize("ids, error, named", [
    ([MAIN, "earn-1"], ValueError, "earn-1"),
    (["earn-1", MAIN], ValueError, "earn-1"),
    ([MAIN, "no-such-case"], OSError, None),
    ([MAIN, ODD, MAIN], ValueError, MAIN),
])
def test_refusals_write_nothing(root, series_dir, ids, error, named):
    earnings_case(root)
    before = snapshot_files(root)
    with pytest.raises(error) as exc:
        snapshot.resolve_cases(root, ids, series_dir=series_dir)
    if named:
        assert named in str(exc.value)
    assert snapshot_files(root) == before


def test_an_outcome_key_the_case_does_not_have(root, series_dir):
    stray = TargetOutcome("GOLD", "1w", "return", "2026-10-29", 1.0, "2026-11-06", 1.1, 0.1, True)
    cases.save_outcome(root, MAIN, cases.Outcome(targets={"GOLD_1w": stray}))
    before = snapshot_files(root)
    with pytest.raises(ValueError) as exc:
        snapshot.resolve_cases(root, [ODD, MAIN], series_dir=series_dir)
    assert "GOLD_1w" in str(exc.value) and MAIN in str(exc.value)
    assert snapshot_files(root) == before


def test_a_malformed_series_file_stops_before_anything_is_written(root, series_dir):
    (series_dir / "VIXCLS.json").write_text("not json", encoding="utf-8")
    before = snapshot_files(root)
    with pytest.raises(ValueError):
        snapshot.resolve_cases(root, [ODD, MAIN], series_dir=series_dir)
    assert snapshot_files(root) == before


def test_a_malformed_series_of_a_resolved_key_is_not_read(root, series_dir):
    snapshot.resolve_case(root, ODD, series_dir=series_dir)
    (series_dir / "SAME.json").write_text("not json", encoding="utf-8")
    assert snapshot.resolve_case(root, ODD, series_dir=series_dir)["resolved"] == ["SAME_1w"]


def test_a_case_with_a_bad_date_is_refused(root, series_dir):
    case = main_case()
    case.case_id, case.event_date = "bad-date", "2026-13-01"
    cases.save_case(root, case)
    with pytest.raises(ValueError):
        snapshot.resolve_cases(root, [MAIN, "bad-date"], series_dir=series_dir)
    assert not (root / MAIN / "outcome.json").exists()


# --- CLI ------------------------------------------------------------------------------------------

def no_client():
    raise AssertionError("resolve never builds a client")


def run(argv, capsys):
    code = main(argv, client_factory=no_client)
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_resolve(root, series_dir, capsys):
    code, out, err = run(["resolve", "--root", str(root), "--ids", f" {MAIN} ,, {ODD} ",
                          "--series-dir", str(series_dir)], capsys)
    assert code == 0, err
    data = json.loads(out)
    assert list(data) == ["cases"]
    assert [c["case_id"] for c in data["cases"]] == [MAIN, ODD]
    assert data["cases"][0]["pending"] == ["DGS10_1m", "DGS10_3m", "SP500_1m"]
    assert data["cases"][1]["unmeasurable"] == ["WTI_1w", "LATE_1w", "GAP_1w"]
    assert (root / MAIN / "outcome.json").is_file()


def test_cli_resolve_out(root, series_dir, tmp_path, capsys):
    target = tmp_path / "resolved.json"
    code, out, _ = run(["resolve", "--root", str(root), "--ids", MAIN, "--series-dir",
                        str(series_dir), "--out", str(target)], capsys)
    assert code == 0 and out == ""
    assert json.loads(target.read_text(encoding="utf-8"))["cases"][0]["case_id"] == MAIN


@pytest.mark.parametrize("argv", [
    ["--ids", MAIN],
    ["--ids", " , ", "--series-dir", "s"],
    ["--series-dir", "s"],
])
def test_cli_resolve_bad_arguments(root, capsys, argv):
    code, _, err = run(["resolve", "--root", str(root)] + argv, capsys)
    assert code == 2 and err.strip()


@pytest.mark.parametrize("ids", ["no-such-case", f"{MAIN},earn-1"])
def test_cli_resolve_failures(root, series_dir, capsys, ids):
    earnings_case(root)
    before = snapshot_files(root)
    code, out, err = run(["resolve", "--root", str(root), "--ids", ids, "--series-dir",
                          str(series_dir)], capsys)
    assert code == 1 and out == "" and err.strip()
    assert snapshot_files(root) == before


def test_snapshot_module_rules():
    text = Path(snapshot.__file__).read_text(encoding="utf-8")
    text.encode("ascii")
    for word in ("anthropic", "fromisoformat", "isfinite"):
        assert word not in text, word
