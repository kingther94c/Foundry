"""Resolving a snapshot (v25): resolve_cases, resolve_case and the resolve command.

Nothing here touches the network or builds a client: every series is a file written with
save_series, as fetch-series would have stored it. Every outcome below is worked out by
hand from SPEC.md.
"""

import json
from datetime import date

import pytest

from researchos.__main__ import main
from researchos.cases import (
    Case,
    Document,
    Outcome,
    Target,
    TargetOutcome,
    load_outcome,
    load_resolution,
    macro_case,
    save_case,
    save_outcome,
    unresolved_targets,
)
from researchos.jsonio import write_json
from researchos.series import save_series
from researchos.snapshot import resolve_case, resolve_cases

AS_OF = "2026-10-30"  # a Friday
CASE_ID = "macro-2026-10-30"
KEYS = ["DGS10_1w", "DGS10_1m", "SP500_1w", "SP500_1m"]
NOT_A_SERIES = "not a series file"  # what load_series refuses with ValueError


def make_target(target_id, kind, horizons, as_of_date, as_of_value):
    return Target(id=target_id, name=f"The {target_id} level", kind=kind,
                  horizons=list(horizons), as_of_date=as_of_date, as_of_value=as_of_value)


def save_macro(root, case_id, targets, as_of=AS_OF):
    case = macro_case(case_id, as_of, targets,
                      [Document("dashboard", f"Markets as of {as_of}", "| DGS10 | 4.00 |\n")])
    save_case(root, case)
    return case


def write_series(series_dir, series):
    for series_id, observations in series.items():
        save_series(series_dir / f"{series_id}.json", observations)
    return series_dir


def the_case(root):
    # DGS10 starts from the snapshot's own date; SP500 from the day before it.
    return save_macro(root, CASE_ID, [
        make_target("DGS10", "bp", ["1w", "1m"], "2026-10-30", 4.0),
        make_target("SP500", "return", ["1w", "1m"], "2026-10-29", 6400.0),
    ])


# The series as fetched again on 2026-11-09. Both were revised after the snapshot (DGS10
# on 10-30, SP500 on 10-29): the start stays the level the case recorded.
EARLY = {
    # 1w ends 2026-11-06: end 4.25, (4.25 - 4.0) * 100 = +25 bp.
    "DGS10": [(date(2026, 10, 29), 3.9), (date(2026, 10, 30), 4.1), (date(2026, 11, 5), 4.2),
              (date(2026, 11, 6), 4.25), (date(2026, 11, 9), 4.3)],
    # Nothing on 2026-11-06 (a holiday): there is a close after it, so the horizon ends on
    # the last close before it, 6080 on 11-05: 6080 / 6400 - 1 = -5%.
    "SP500": [(date(2026, 10, 29), 6500.0), (date(2026, 11, 5), 6080.0),
              (date(2026, 11, 9), 6200.0)],
}
FIRST_SUMMARY = {"case_id": CASE_ID, "resolved": ["DGS10_1w", "SP500_1w"],
                 "new": ["DGS10_1w", "SP500_1w"], "pending": ["DGS10_1m", "SP500_1m"],
                 "unmeasurable": []}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---- resolve_cases ----------------------------------------------------------------------

def test_each_horizon_that_has_ended_resolves_from_the_level_the_case_recorded(tmp_path):
    root = tmp_path / "cases"
    case = the_case(root)
    series_dir = write_series(tmp_path / "series", EARLY)

    assert resolve_cases(root, [CASE_ID], series_dir=series_dir) == [FIRST_SUMMARY]

    written = read(root / CASE_ID / "outcome.json")
    assert list(written) == ["abnormal_return_1d", "stock_return_1d", "market_return_1d",
                             "notes", "sources", "targets"]
    assert (written["abnormal_return_1d"], written["notes"], written["sources"]) == (None, "", [])
    assert list(written["targets"]) == ["DGS10_1w", "SP500_1w"]
    assert written["targets"]["DGS10_1w"] == {
        "target": "DGS10", "horizon": "1w", "kind": "bp", "start_date": "2026-10-30",
        "start": 4.0, "end_date": "2026-11-06", "end": 4.25, "change": 25.0, "up": True}
    sp500 = written["targets"]["SP500_1w"]
    assert list(sp500) == ["target", "horizon", "kind", "start_date", "start", "end_date",
                           "end", "change", "up"]
    assert {name: value for name, value in sp500.items() if name != "change"} == {
        "target": "SP500", "horizon": "1w", "kind": "return", "start_date": "2026-10-29",
        "start": 6400.0, "end_date": "2026-11-05", "end": 6080.0, "up": False}
    assert sp500["change"] == pytest.approx(-0.05)

    assert load_resolution(root, CASE_ID) == {"as_of": AS_OF, "targets": {
        "DGS10_1w": {"horizon_end": "2026-11-06", "resolved": True},
        "DGS10_1m": {"horizon_end": "2026-11-30", "resolved": False},
        "SP500_1w": {"horizon_end": "2026-11-06", "resolved": True},
        "SP500_1m": {"horizon_end": "2026-11-30", "resolved": False}}}
    assert unresolved_targets(case, load_resolution(root, CASE_ID)) == ["DGS10_1m", "SP500_1m"]


def test_resolving_again_writes_the_same_bytes_and_an_outcome_never_changes(tmp_path):
    root = tmp_path / "cases"
    case = the_case(root)
    series_dir = write_series(tmp_path / "series", EARLY)
    resolve_cases(root, [CASE_ID], series_dir=series_dir)
    files = [root / CASE_ID / name for name in ("outcome.json", "resolution.json")]
    before = [path.read_bytes() for path in files]

    again = resolve_cases(root, [CASE_ID], series_dir=series_dir)

    assert again == [{**FIRST_SUMMARY, "new": []}]
    assert [path.read_bytes() for path in files] == before

    # A month later. DGS10's 11-06 close is revised to 9.99: DGS10_1w keeps its 4.25.
    # 1m ends Monday 2026-11-30: DGS10 4.5 that day, (4.5 - 4.0) * 100 = +50 bp; SP500 has
    # no close that day, so its last before, 6720 on 11-27: 6720 / 6400 - 1 = +5%.
    write_series(series_dir, {
        "DGS10": [(date(2026, 10, 30), 4.1), (date(2026, 11, 6), 9.99),
                  (date(2026, 11, 30), 4.5), (date(2026, 12, 1), 4.6)],
        "SP500": [(date(2026, 11, 5), 6080.0), (date(2026, 11, 27), 6720.0),
                  (date(2026, 12, 1), 6800.0)],
    })

    assert resolve_case(root, CASE_ID, series_dir=series_dir) == {
        "case_id": CASE_ID, "resolved": KEYS, "new": ["DGS10_1m", "SP500_1m"],
        "pending": [], "unmeasurable": []}
    outcome = load_outcome(root, CASE_ID)
    assert list(outcome.targets) == KEYS
    assert outcome.targets["DGS10_1w"] == TargetOutcome(
        "DGS10", "1w", "bp", "2026-10-30", 4.0, "2026-11-06", 4.25, 25.0, True)
    assert outcome.targets["DGS10_1m"] == TargetOutcome(
        "DGS10", "1m", "bp", "2026-10-30", 4.0, "2026-11-30", 4.5, 50.0, True)
    sp500 = outcome.targets["SP500_1m"]
    assert (sp500.start_date, sp500.start, sp500.end_date, sp500.end, sp500.up) == (
        "2026-10-29", 6400.0, "2026-11-27", 6720.0, True)
    assert sp500.change == pytest.approx(0.05)
    assert outcome.targets["SP500_1w"].end_date == "2026-11-05"
    assert unresolved_targets(case, load_resolution(root, CASE_ID)) == []
    assert all(entry["resolved"] for entry in load_resolution(root, CASE_ID)["targets"].values())


def test_a_horizon_ending_on_a_weekend_waits_and_may_end_on_the_start_date(tmp_path):
    root = tmp_path / "cases"
    case = save_macro(root, "vix", [make_target("VIXCLS", "points", ["1d"], AS_OF, 20.0)])
    series_dir = tmp_path / "series"
    # The 1d horizon from Friday 2026-10-30 ends on Saturday 2026-10-31: nothing on or after
    # it yet, so a later observation could still fall inside it.
    write_series(series_dir, {"VIXCLS": [(date(2026, 10, 29), 19.0), (date(2026, 10, 30), 20.0)]})

    assert resolve_case(root, "vix", series_dir=series_dir) == {
        "case_id": "vix", "resolved": [], "new": [], "pending": ["VIXCLS_1d"],
        "unmeasurable": []}
    assert not (root / "vix" / "outcome.json").exists()
    assert load_resolution(root, "vix") == {
        "as_of": AS_OF, "targets": {"VIXCLS_1d": {"horizon_end": "2026-10-31", "resolved": False}}}

    # Monday's close shows nothing was observed inside the horizon after its start: it ends
    # on the start's own date, with a change of 0 (not up).
    write_series(series_dir, {"VIXCLS": [(date(2026, 10, 30), 20.0), (date(2026, 11, 2), 22.0)]})

    assert resolve_case(root, "vix", series_dir=series_dir)["new"] == ["VIXCLS_1d"]
    assert load_outcome(root, "vix").targets == {"VIXCLS_1d": TargetOutcome(
        "VIXCLS", "1d", "points", AS_OF, 20.0, AS_OF, 20.0, 0.0, False)}
    assert unresolved_targets(case, load_resolution(root, "vix")) == []


def test_unmeasurable_keys_and_a_series_with_no_file(tmp_path):
    root = tmp_path / "cases"
    case = save_macro(root, "edges", [
        make_target("GONE", "bp", ["1w"], AS_OF, 4.0),
        make_target("EARLY", "bp", ["1d"], "2026-10-29", 4.0),
        make_target("ZERO", "return", ["1w"], AS_OF, 0.0),
        make_target("NEG", "return", ["1w"], AS_OF, -2.0),
        make_target("SPREAD", "points", ["1w"], AS_OF, -0.5),
        make_target("NOFILE", "bp", ["1w"], AS_OF, 4.0),
    ])
    series_dir = write_series(tmp_path / "series", {
        # The file now starts after the horizon's end (2026-11-06): no end inside it.
        "GONE": [(date(2026, 11, 9), 4.3)],
        # 1d ends 2026-10-31; the last observation by then, 10-28, is before the start 10-29.
        "EARLY": [(date(2026, 10, 28), 3.9), (date(2026, 11, 2), 4.2)],
        # A return from a level of zero, or below it, is None.
        "ZERO": [(date(2026, 10, 30), 0.0), (date(2026, 11, 6), 1.0)],
        "NEG": [(date(2026, 10, 30), -2.0), (date(2026, 11, 6), -1.0)],
        # Points from a level below zero are fine: -0.25 - (-0.5) = +0.25.
        "SPREAD": [(date(2026, 10, 30), -0.5), (date(2026, 11, 6), -0.25)],
    })

    assert resolve_case(root, "edges", series_dir=series_dir) == {
        "case_id": "edges", "resolved": ["SPREAD_1w"], "new": ["SPREAD_1w"],
        "pending": ["NOFILE_1w"],
        "unmeasurable": ["GONE_1w", "EARLY_1d", "ZERO_1w", "NEG_1w"]}
    assert load_outcome(root, "edges").targets == {"SPREAD_1w": TargetOutcome(
        "SPREAD", "1w", "points", AS_OF, -0.5, "2026-11-06", -0.25, 0.25, True)}
    assert unresolved_targets(case, load_resolution(root, "edges")) == [
        "GONE_1w", "EARLY_1d", "ZERO_1w", "NEG_1w", "NOFILE_1w"]

    # Once the file holds the window again, an unmeasurable key resolves: +50 bp.
    write_series(series_dir, {"GONE": [(date(2026, 10, 30), 4.0), (date(2026, 11, 6), 4.5),
                                       (date(2026, 11, 9), 4.3)]})

    result = resolve_case(root, "edges", series_dir=series_dir)

    assert result["new"] == ["GONE_1w"]
    assert result["resolved"] == ["GONE_1w", "SPREAD_1w"]
    assert result["unmeasurable"] == ["EARLY_1d", "ZERO_1w", "NEG_1w"]
    assert load_outcome(root, "edges").targets["GONE_1w"] == TargetOutcome(
        "GONE", "1w", "bp", AS_OF, 4.0, "2026-11-06", 4.5, 50.0, True)


def test_an_outcome_already_written_is_kept_exactly_and_its_series_not_read(tmp_path):
    root = tmp_path / "cases"
    save_macro(root, CASE_ID, [make_target("DGS10", "bp", ["1w"], AS_OF, 4.0),
                               make_target("SP500", "return", ["1w"], "2026-10-29", 6400.0)])
    # Written earlier, from a series that has changed since.
    kept = TargetOutcome("SP500", "1w", "return", "2026-10-29", 6400.0, "2026-11-06", 6720.0,
                         0.05, True)
    save_outcome(root, CASE_ID, Outcome(notes="resolved by hand",
                                        sources=["https://example.com/spx"],
                                        targets={"SP500_1w": kept}))
    series_dir = write_series(tmp_path / "series", {"DGS10": EARLY["DGS10"]})
    (series_dir / "SP500.json").write_text(NOT_A_SERIES, encoding="utf-8")

    assert resolve_case(root, CASE_ID, series_dir=series_dir) == {
        "case_id": CASE_ID, "resolved": ["DGS10_1w", "SP500_1w"], "new": ["DGS10_1w"],
        "pending": [], "unmeasurable": []}
    outcome = load_outcome(root, CASE_ID)
    assert list(outcome.targets) == ["DGS10_1w", "SP500_1w"]  # the order of the keys
    assert outcome.targets["SP500_1w"] == kept
    assert outcome.targets["DGS10_1w"].change == 25.0
    assert (outcome.abnormal_return_1d, outcome.notes, outcome.sources) == (
        None, "resolved by hand", ["https://example.com/spx"])


def test_a_case_that_fails_its_checks_stops_the_call_with_nothing_written(tmp_path):
    root = tmp_path / "cases"
    the_case(root)
    series_dir = write_series(tmp_path / "series", EARLY)
    save_case(root, Case(case_id="acme-q4", company="Acme Widgets Inc.", ticker="ACMW",
                         aliases=[], fiscal_period="Q4 FY2025", event_date="2026-02-01",
                         documents=[]))
    save_macro(root, "bad-kind", [make_target("DGS10", "bp", ["1w"], AS_OF, 4.0)])
    path = root / "bad-kind" / "case.json"
    data = read(path)
    data["targets"][0]["kind"] = "percent"
    write_json(path, data)
    save_macro(root, "stray", [make_target("DGS10", "bp", ["1w"], AS_OF, 4.0)])
    save_outcome(root, "stray", Outcome(targets={"DGS10_1m": TargetOutcome(
        "DGS10", "1m", "bp", AS_OF, 4.0, "2026-11-30", 4.5, 50.0, True)}))
    save_macro(root, "broken", [make_target("TY", "bp", ["1w"], AS_OF, 4.0)])
    (series_dir / "TY.json").write_text(NOT_A_SERIES, encoding="utf-8")

    with pytest.raises(ValueError, match=CASE_ID):  # every check runs before a series is read
        resolve_cases(root, [CASE_ID, "broken", CASE_ID], series_dir=series_dir)
    with pytest.raises(OSError):
        resolve_cases(root, [CASE_ID, "no-such-case"], series_dir=series_dir)
    with pytest.raises(ValueError, match="acme-q4"):
        resolve_cases(root, ["broken", "acme-q4"], series_dir=series_dir)
    with pytest.raises(ValueError, match="percent"):
        resolve_cases(root, [CASE_ID, "bad-kind"], series_dir=series_dir)
    with pytest.raises(ValueError, match="stray.*DGS10_1m"):
        resolve_cases(root, [CASE_ID, "stray"], series_dir=series_dir)
    with pytest.raises(ValueError):  # a malformed series file passes through
        resolve_cases(root, [CASE_ID, "broken"], series_dir=series_dir)

    for case_id in (CASE_ID, "acme-q4", "bad-kind", "stray", "broken"):
        assert not (root / case_id / "resolution.json").exists()
    for case_id in (CASE_ID, "acme-q4", "bad-kind", "broken"):
        assert not (root / case_id / "outcome.json").exists()


def test_the_forecast_is_never_opened(tmp_path):
    root = tmp_path / "cases"
    the_case(root)
    (root / CASE_ID / "prediction.json").mkdir()  # a prediction.json no one could open
    series_dir = write_series(tmp_path / "series", EARLY)

    assert resolve_case(root, CASE_ID, series_dir=series_dir) == FIRST_SUMMARY


def test_several_cases_resolve_in_the_order_given(tmp_path):
    root = tmp_path / "cases"
    the_case(root)
    save_macro(root, "vix", [make_target("VIXCLS", "points", ["1d"], AS_OF, 20.0)])
    series_dir = write_series(tmp_path / "series", EARLY)

    result = resolve_cases(root, ["vix", CASE_ID], series_dir=series_dir)

    assert result == [{"case_id": "vix", "resolved": [], "new": [], "pending": ["VIXCLS_1d"],
                       "unmeasurable": []}, FIRST_SUMMARY]
    assert not (root / "vix" / "outcome.json").exists()
    assert (root / "vix" / "resolution.json").exists()


# ---- the resolve command ----------------------------------------------------------------

def no_client():
    raise AssertionError("resolve built a client")


def test_the_resolve_command_reads_ids_as_train_does_and_builds_no_client(tmp_path, capsys):
    root = tmp_path / "cases"
    the_case(root)
    save_macro(root, "vix", [make_target("VIXCLS", "points", ["1d"], AS_OF, 20.0)])
    series_dir = write_series(tmp_path / "series", EARLY)
    argv = ["resolve", "--root", str(root), "--ids", f" {CASE_ID} ,, vix ,",
            "--series-dir", str(series_dir)]

    assert main(argv, client_factory=no_client) == 0

    vix = {"case_id": "vix", "resolved": [], "new": [], "pending": ["VIXCLS_1d"],
           "unmeasurable": []}
    assert json.loads(capsys.readouterr().out) == {"cases": [FIRST_SUMMARY, vix]}

    out = tmp_path / "resolved.json"
    assert main([*argv, "--out", str(out)], client_factory=no_client) == 0
    assert capsys.readouterr().out == ""
    assert read(out) == {"cases": [{**FIRST_SUMMARY, "new": []}, vix]}


@pytest.mark.parametrize("given", [
    ["--ids", " , ,", "--series-dir", "SERIES"],  # no id left
    ["--ids", CASE_ID],  # no --series-dir
    ["--series-dir", "SERIES"],  # no --ids
])
def test_bad_arguments_exit_2(tmp_path, capsys, given):
    root = tmp_path / "cases"
    the_case(root)
    series_dir = write_series(tmp_path / "series", EARLY)
    argv = ["resolve", "--root", str(root),
            *[str(series_dir) if arg == "SERIES" else arg for arg in given]]

    assert main(argv, client_factory=no_client) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
    assert not (root / CASE_ID / "resolution.json").exists()
    assert main(["resolve", "--ids", CASE_ID, "--series-dir", str(series_dir)],
                client_factory=no_client) == 2  # no --root


@pytest.mark.parametrize("ids", [f"{CASE_ID},no-such-case", f"{CASE_ID},{CASE_ID}"])
def test_a_case_that_fails_its_checks_exits_1_with_nothing_written(tmp_path, capsys, ids):
    root = tmp_path / "cases"
    the_case(root)
    series_dir = write_series(tmp_path / "series", EARLY)

    code = main(["resolve", "--root", str(root), "--ids", ids, "--series-dir", str(series_dir)],
                client_factory=no_client)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert not (root / CASE_ID / "resolution.json").exists()
    assert not (root / CASE_ID / "outcome.json").exists()
