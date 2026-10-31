"""Macro cases: as-of snapshots, per-target outcomes (SPEC.md v21)."""

import builtins
import datetime
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from conftest import make_case, make_outcome
from test_sources import cli_client, cli_files

from researchos.__main__ import main
from researchos.cases import (CASE_FILE, DOMAINS, KINDS, OUTCOME_FILE, RESOLUTION_FILE, Case,
                              Document, Outcome, Target, TargetOutcome, case_to_dict,
                              check_targets, load_case, load_outcome, load_resolution,
                              macro_case, outcome_to_dict,
                              resolution_manifest, save_case, save_outcome, save_resolution,
                              target_from_dict, target_key, target_keys,
                              target_outcome_from_dict, unresolved_targets)
from researchos.loop import require_after_cutoff, single_domain
from researchos.masking import MaskedCase, unmasked


def dgs10(**changes):
    target = Target(id="DGS10", name="10-year Treasury yield", kind="bp",
                    horizons=["1w", "1m"], as_of_date="2026-10-30", as_of_value=4.12,
                    trailing={"1w": -3.0, "1m": None})
    return replace(target, **changes)


def spx(**changes):
    target = Target(id="SP500", name="S&P 500", kind="return", horizons=["1m"],
                    as_of_date="2026-10-29", as_of_value=6012.5)
    return replace(target, **changes)


def make_macro(case_id="macro-2026-10-30", as_of="2026-10-30", targets=None):
    documents = [Document(kind="markets", title="Markets at the close",
                          text="Yields rose 5bp on the week.", source="https://example.com/m")]
    return macro_case(case_id, as_of, [dgs10(), spx()] if targets is None else targets,
                      documents)


def target_outcome(**changes):
    outcome = TargetOutcome(target="DGS10", horizon="1w", kind="bp", start_date="2026-10-30",
                            start=4.12, end_date="2026-11-06", end=4.2, change=8.0, up=True)
    return replace(outcome, **changes)


def file_bytes(data):
    """What write_json writes, so a file can be compared byte for byte."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def record_opens(monkeypatch):
    opened = []
    real = {"read_bytes": Path.read_bytes, "read_text": Path.read_text, "open": Path.open}

    def wrap(name):
        def method(self, *a, **k):
            opened.append(self.name)
            return real[name](self, *a, **k)
        return method

    for name in real:
        monkeypatch.setattr(Path, name, wrap(name))
    real_open = builtins.open

    def builtin_open(file, *a, **k):
        opened.append(Path(str(file)).name)
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", builtin_open)
    return opened


# ---- earnings cases are unchanged ------------------------------------------------------

def test_earnings_files_are_byte_for_byte_what_they_were(tmp_path):
    case, outcome = make_case(), make_outcome()
    save_case(tmp_path, case, outcome)
    case_dir = tmp_path / case.case_id
    before_v21_case = {
        "case_id": case.case_id, "company": case.company, "ticker": case.ticker,
        "aliases": case.aliases, "fiscal_period": case.fiscal_period,
        "event_date": case.event_date,
        "documents": [{"kind": d.kind, "title": d.title, "text": d.text, "source": d.source}
                      for d in case.documents],
    }
    before_v21_outcome = {"abnormal_return_1d": 0.031, "stock_return_1d": 0.04,
                          "market_return_1d": 0.009, "notes": outcome.notes,
                          "sources": outcome.sources}
    assert (case_dir / CASE_FILE).read_bytes() == file_bytes(before_v21_case)
    assert (case_dir / OUTCOME_FILE).read_bytes() == file_bytes(before_v21_outcome)
    assert sorted(p.name for p in case_dir.iterdir()) == [CASE_FILE, OUTCOME_FILE]
    assert load_case(tmp_path, case.case_id) == case
    assert load_outcome(tmp_path, case.case_id) == outcome
    save_outcome(tmp_path, "other", outcome)
    assert (tmp_path / "other" / OUTCOME_FILE).read_bytes() == file_bytes(before_v21_outcome)


def test_defaults_keep_the_earnings_meaning():
    assert DOMAINS == ("earnings", "macro")
    assert KINDS == ("bp", "return", "points")
    assert RESOLUTION_FILE == "resolution.json"
    assert Outcome(0.03).abnormal_return_1d == 0.03
    assert Outcome(0.03).targets == {}
    assert Outcome().abnormal_return_1d is None
    case = Case("x", "Acme Corp", "ACME", [], "Q1", "2026-01-01", [])
    assert case.domain == "earnings" and case.targets == []


def test_load_outcome_without_targets_key_reads_as_before(tmp_path):
    case_dir = tmp_path / "x"
    case_dir.mkdir()
    (case_dir / OUTCOME_FILE).write_text(json.dumps({"notes": "no number"}), encoding="utf-8")
    with pytest.raises(KeyError):
        load_outcome(tmp_path, "x")


# ---- files of a macro case -------------------------------------------------------------

def test_macro_case_file_keeps_domain_and_targets(tmp_path):
    case = make_macro()
    save_case(tmp_path, case)
    case_dir = tmp_path / case.case_id
    raw = (case_dir / CASE_FILE).read_bytes()
    assert raw == file_bytes(asdict(case))
    data = json.loads(raw)
    assert list(data) == ["case_id", "company", "ticker", "aliases", "fiscal_period",
                          "event_date", "documents", "domain", "targets"]
    assert data["domain"] == "macro"
    assert data["targets"][0] == {"id": "DGS10", "name": "10-year Treasury yield", "kind": "bp",
                                  "horizons": ["1w", "1m"], "as_of_date": "2026-10-30",
                                  "as_of_value": 4.12, "trailing": {"1w": -3.0, "1m": None}}
    assert not (case_dir / OUTCOME_FILE).exists()
    loaded = load_case(tmp_path, case.case_id)
    assert loaded == case
    assert all(isinstance(t, Target) for t in loaded.targets)


def test_domain_and_targets_are_left_out_only_for_plain_earnings(tmp_path):
    with_targets = replace(make_case("e1"), targets=[dgs10()])
    macro_without_targets = replace(make_case("m1"), domain="macro")
    for case in (with_targets, macro_without_targets):
        save_case(tmp_path, case, make_outcome())
        data = json.loads((tmp_path / case.case_id / CASE_FILE).read_text(encoding="utf-8"))
        assert list(data)[-2:] == ["domain", "targets"]
        assert data["domain"] == case.domain
        assert load_case(tmp_path, case.case_id) == case


def test_save_case_without_outcome_leaves_outcome_file_alone(tmp_path):
    case = make_case()
    save_case(tmp_path, case, make_outcome())
    path = tmp_path / case.case_id / OUTCOME_FILE
    before = path.read_bytes()
    save_case(tmp_path, replace(case, fiscal_period="Q1 FY2026"), None)
    assert path.read_bytes() == before
    assert load_case(tmp_path, case.case_id).fiscal_period == "Q1 FY2026"
    save_case(tmp_path, make_case("fresh"))
    assert not (tmp_path / "fresh" / OUTCOME_FILE).exists()


def test_macro_outcome_round_trip(tmp_path):
    outcome = Outcome(notes="resolved piece by piece",
                      targets={"DGS10_1w": target_outcome(),
                               "SP500_1m": target_outcome(target="SP500", horizon="1m",
                                                          kind="return", change=-0.02,
                                                          up=False)})
    save_outcome(tmp_path, "new-dir", outcome)
    raw = (tmp_path / "new-dir" / OUTCOME_FILE).read_bytes()
    assert raw == file_bytes(asdict(outcome))
    data = json.loads(raw)
    assert list(data) == ["abnormal_return_1d", "stock_return_1d", "market_return_1d", "notes",
                          "sources", "targets"]
    assert list(data["targets"]) == ["DGS10_1w", "SP500_1m"]
    assert list(data["targets"]["DGS10_1w"]) == ["target", "horizon", "kind", "start_date",
                                                 "start", "end_date", "end", "change", "up"]
    loaded = load_outcome(tmp_path, "new-dir")
    assert loaded == outcome
    assert loaded.abnormal_return_1d is None
    assert all(isinstance(v, TargetOutcome) for v in loaded.targets.values())


def test_load_outcome_with_targets_key(tmp_path):
    case_dir = tmp_path / "x"
    case_dir.mkdir()
    entry = asdict(target_outcome(horizon="1m"))
    (case_dir / OUTCOME_FILE).write_text(json.dumps({"targets": {"DGS10_1m": entry}}),
                                         encoding="utf-8")
    outcome = load_outcome(tmp_path, "x")
    assert outcome == Outcome(targets={"DGS10_1m": target_outcome(horizon="1m")})
    assert outcome.abnormal_return_1d is None and outcome.notes == "" and outcome.sources == []
    del entry["up"]
    (case_dir / OUTCOME_FILE).write_text(json.dumps({"targets": {"DGS10_1m": entry}}),
                                         encoding="utf-8")
    with pytest.raises(KeyError):
        load_outcome(tmp_path, "x")


def test_loading_case_and_resolution_never_opens_outcome(tmp_path, monkeypatch):
    case = make_macro()
    save_case(tmp_path, case, Outcome(targets={"DGS10_1w": target_outcome()}))
    save_resolution(tmp_path, case.case_id, resolution_manifest(case, ["DGS10_1w"]))
    opened = record_opens(monkeypatch)
    loaded = load_case(tmp_path, case.case_id)
    manifest = load_resolution(tmp_path, case.case_id)
    monkeypatch.undo()
    assert loaded == case
    assert manifest["targets"]["DGS10_1w"]["resolved"] is True
    assert CASE_FILE in opened and RESOLUTION_FILE in opened
    assert OUTCOME_FILE not in opened


# ---- target_key, check_targets, target_keys -------------------------------------------

def test_target_key():
    assert target_key("DGS10", "1w") == "DGS10_1w"
    assert target_key("SP_500", "12m") == "SP_500_12m"
    for bad_id in ["", "DGS-10", "DGS 10", "DGS10\n", "DGS" + chr(201), "DGS" + chr(0x663),
                   10, None, b"DGS10"]:
        with pytest.raises(ValueError):
            target_key(bad_id, "1w")
    for bad_horizon in [" 1w", "1w ", "1w\n", "1W", "0w", "1q", "", "1 w", 1, None]:
        with pytest.raises(ValueError):
            target_key("DGS10", bad_horizon)


def test_check_targets_accepts_well_formed_targets():
    assert check_targets([dgs10(), spx()]) is None
    # name and trailing are not checked; an int level and a padded ISO date are fine
    check_targets([dgs10(name=123, trailing="anything", as_of_value=4,
                         as_of_date=" 2026-10-30 ")])


@pytest.mark.parametrize("targets, match", [
    ([], "empty"),
    (None, "list of Target"),
    ([dgs10(), "DGS10"], r"position 1 \(targets\[1\]\)"),
    ([dgs10(), dgs10(id="DGS-10")], r"position 1 \(targets\[1\]\)"),
    ([dgs10(id=None)], r"position 0 \(targets\[0\]\)"),
    ([dgs10(), spx(id="DGS10")], "target DGS10: two targets"),
    ([dgs10(), spx(kind="pct")], "target SP500: kind"),
    ([dgs10(), spx(horizons=[])], "target SP500: horizons"),
    ([dgs10(), spx(horizons="1m")], "target SP500: horizons"),
    ([dgs10(), spx(horizons=("1m",))], "target SP500: horizons"),
    ([dgs10(), spx(horizons=["1m", " 3m"])], "target SP500: horizon ' 3m'"),
    ([dgs10(), spx(horizons=["1m", "3m", "1m"])], "target SP500: horizon '1m' is given twice"),
    ([dgs10(), spx(as_of_date="2026-13-01")], "target SP500: as_of_date"),
    ([dgs10(), spx(as_of_date=datetime.date(2026, 10, 29))], "target SP500: as_of_date"),
    ([dgs10(), spx(as_of_value=True)], "target SP500: as_of_value"),
    ([dgs10(), spx(as_of_value=float("nan"))], "target SP500: as_of_value"),
    ([dgs10(), spx(as_of_value="6012.5")], "target SP500: as_of_value"),
])
def test_check_targets_refuses(targets, match):
    with pytest.raises(ValueError, match=match):
        check_targets(targets)


def test_target_keys_order():
    assert target_keys(make_case()) == []
    case = make_macro(targets=[spx(horizons=["3m", "1w"]), dgs10()])
    assert target_keys(case) == ["SP500_3m", "SP500_1w", "DGS10_1w", "DGS10_1m"]
    with pytest.raises(ValueError, match="target SP500"):
        target_keys(replace(case, targets=[spx(), spx()]))


# ---- macro_case and the dict readers ---------------------------------------------------

def test_macro_case_fields():
    documents = [Document("markets", "Markets", "Yields rose.")]
    targets = [dgs10()]
    case = macro_case("m", " 2026-10-30 ", targets, documents)
    assert case == Case(case_id="m", company="", ticker="", aliases=[],
                        fiscal_period="as of 2026-10-30", event_date="2026-10-30",
                        documents=documents, domain="macro", targets=targets)
    assert case.documents is not documents and case.targets is not targets
    basic = macro_case("m", "20261030", iter(targets), iter(documents))
    assert basic.event_date == "2026-10-30" and basic.fiscal_period == "as of 2026-10-30"
    assert basic.targets == targets and basic.documents == documents
    # A target may start from the snapshot's own date or from before it.
    assert macro_case("m", "2026-10-30", [dgs10(), spx()], []).targets[1].as_of_date == \
        "2026-10-29"


@pytest.mark.parametrize("as_of", ["2026-10-31x", "", "2026-02-30", None,
                                   datetime.date(2026, 10, 30), 20261030])
def test_macro_case_refuses_an_as_of_that_is_not_an_iso_date_string(as_of):
    with pytest.raises(ValueError, match="as_of"):
        macro_case("m", as_of, [dgs10()], [])


def test_macro_case_refuses_bad_targets_and_later_starts():
    with pytest.raises(ValueError, match="empty"):
        macro_case("m", "2026-10-30", [], [])
    with pytest.raises(ValueError, match="target SP500"):
        macro_case("m", "2026-10-30", [dgs10(), spx(kind="level")], [])
    with pytest.raises(ValueError, match="target SP500: as_of_date 2026-10-31 is after"):
        macro_case("m", "2026-10-30", [dgs10(), spx(as_of_date="2026-10-31")], [])


def test_target_from_dict():
    data = {"id": "DGS10", "kind": "bp", "horizons": ["1w"], "as_of_date": "2026-10-30",
            "as_of_value": 4.1}
    target = target_from_dict(data)
    assert target == Target("DGS10", "", "bp", ["1w"], "2026-10-30", 4.1, {})
    assert target.horizons is not data["horizons"]
    full = dict(data, name="10-year", trailing={"1w": 2.0})
    named = target_from_dict(full)
    assert named.name == "10-year" and named.trailing == {"1w": 2.0}
    assert named.trailing is not full["trailing"]
    for key in ["id", "kind", "horizons", "as_of_date", "as_of_value"]:
        with pytest.raises(KeyError):
            target_from_dict({k: v for k, v in data.items() if k != key})


def test_target_outcome_from_dict():
    data = asdict(target_outcome())
    assert target_outcome_from_dict(data) == target_outcome()
    for key in list(data):
        with pytest.raises(KeyError):
            target_outcome_from_dict({k: v for k, v in data.items() if k != key})


# ---- the resolution manifest -----------------------------------------------------------

def test_resolution_manifest_matches_the_spec_example():
    case = make_macro(targets=[dgs10()])
    manifest = resolution_manifest(case, resolved=["DGS10_1w"])
    assert manifest == {"as_of": "2026-10-30",
                        "targets": {"DGS10_1w": {"horizon_end": "2026-11-06", "resolved": True},
                                    "DGS10_1m": {"horizon_end": "2026-11-30",
                                                 "resolved": False}}}
    assert list(manifest) == ["as_of", "targets"]
    assert list(manifest["targets"]) == ["DGS10_1w", "DGS10_1m"]
    assert all(list(e) == ["horizon_end", "resolved"] for e in manifest["targets"].values())
    assert manifest["targets"]["DGS10_1w"]["resolved"] is True
    assert manifest["targets"]["DGS10_1m"]["resolved"] is False
    assert "4.12" not in json.dumps(manifest)  # dates and flags, never a level


def test_resolution_manifest_keys_dates_and_flags():
    case = make_macro(targets=[dgs10(), spx(horizons=["3m"])])
    manifest = resolution_manifest(case)
    assert list(manifest["targets"]) == target_keys(case)
    assert all(e["resolved"] is False for e in manifest["targets"].values())
    # The horizon runs from the case's event_date, not from the target's as_of_date.
    assert manifest["targets"]["SP500_3m"] == {"horizon_end": "2027-01-30", "resolved": False}
    flags = resolution_manifest(case, (k for k in ["SP500_3m", "DGS10_1m"]))["targets"]
    assert [flags[k]["resolved"] for k in target_keys(case)] == [False, True, True]
    padded = replace(case, event_date=" 2026-10-30 ")
    assert resolution_manifest(padded)["as_of"] == "2026-10-30"


def test_resolution_manifest_refusals():
    case = make_macro(targets=[dgs10()])
    with pytest.raises(ValueError, match="macro"):
        resolution_manifest(make_case())
    for bad in ["soon", "", None, datetime.date(2026, 10, 30)]:
        with pytest.raises(ValueError, match="event_date"):
            resolution_manifest(replace(case, event_date=bad))
    with pytest.raises(ValueError, match="target DGS10: kind"):
        resolution_manifest(replace(case, targets=[dgs10(kind="pct")]))
    with pytest.raises(ValueError, match="outside the dates Python can represent"):
        resolution_manifest(replace(case, event_date="9999-12-25"))
    with pytest.raises(ValueError, match="SP500_1w"):
        resolution_manifest(case, resolved=["DGS10_1w", "SP500_1w"])


def test_resolution_file_round_trip(tmp_path):
    case = make_macro(targets=[dgs10()])
    manifest = resolution_manifest(case, ["DGS10_1w"])
    assert load_resolution(tmp_path, case.case_id) is None
    save_resolution(tmp_path, case.case_id, manifest)
    path = tmp_path / case.case_id / RESOLUTION_FILE
    assert path.read_bytes() == file_bytes(manifest)
    assert load_resolution(tmp_path, case.case_id) == manifest
    assert not (tmp_path / case.case_id / OUTCOME_FILE).exists()


def test_unresolved_targets():
    case = make_macro(targets=[dgs10(), spx()])
    keys = ["DGS10_1w", "DGS10_1m", "SP500_1m"]
    assert unresolved_targets(case, resolution_manifest(case)) == keys
    assert unresolved_targets(case, resolution_manifest(case, ["DGS10_1m"])) == \
        ["DGS10_1w", "SP500_1m"]
    for manifest in [None, [], "targets", {}, {"targets": None}, {"targets": keys}]:
        assert unresolved_targets(case, manifest) == keys
    manifest = {"targets": {"DGS10_1w": {"resolved": True}, "DGS10_1m": {"resolved": 1},
                            "SP500_1m": {"resolved": "true"}, "GDP_1y": {"resolved": True}}}
    assert unresolved_targets(case, manifest) == ["DGS10_1m", "SP500_1m"]
    manifest = {"targets": {"DGS10_1w": True, "DGS10_1m": {}, "SP500_1m": {"resolved": 1.0}}}
    assert unresolved_targets(case, manifest) == keys
    assert unresolved_targets(make_case(), None) == []
    with pytest.raises(ValueError, match="target SP500"):
        unresolved_targets(replace(case, targets=[spx(), spx()]), None)


# ---- unmasked --------------------------------------------------------------------------

def test_unmasked_gives_the_documents_as_they_are(monkeypatch):
    case = make_macro()
    case.documents.append(Document("calendar", "Next week", "CPI on 2026-11-12; $5 a share.",
                                   "https://example.com/c"))
    opened = record_opens(monkeypatch)
    masked = unmasked(case)
    monkeypatch.undo()
    assert opened == []
    assert masked == MaskedCase(case_id=case.case_id,
                                documents=[Document(d.kind, d.title, d.text, "")
                                           for d in case.documents],
                                status="unmasked", rounds=0, mapping={}, factor=1.0,
                                year_offset=0, final_guesses=[], abstracted=False,
                                redaction_rounds=[])
    assert all(m is not d for m, d in zip(masked.documents, case.documents))
    masked.documents[0].text = "changed"
    masked.documents.append(Document("x", "y", "z"))
    assert case.documents[0].text == "Yields rose 5bp on the week."
    assert len(case.documents) == 2
    assert unmasked(make_case()).documents[0].text == make_case().documents[0].text


# ---- researchos.loop -------------------------------------------------------------------

def test_require_after_cutoff_accepts_a_later_case():
    case = make_macro()  # 2026-10-30
    for cutoff in ["2026-10-29", " 2026-10-29 ", datetime.date(2026, 10, 29),
                   datetime.datetime(2026, 10, 29, 23, 59)]:
        assert require_after_cutoff("m1", case, cutoff) is None


def test_require_after_cutoff_refusals():
    case = make_macro()  # 2026-10-30
    for cutoff in ["2026-10-30", "2026-10-31", datetime.date(2027, 1, 1), None]:
        with pytest.raises(ValueError, match="case m1"):
            require_after_cutoff("m1", case, cutoff)
    for bad in ["soon", "", None, datetime.date(2026, 10, 30), 20261030]:
        with pytest.raises(ValueError, match="case m1"):
            require_after_cutoff("m1", replace(case, event_date=bad), "2026-01-01")
    with pytest.raises(ValueError, match="knowledge_cutoff is not an ISO date"):
        require_after_cutoff("m1", case, "late 2026")
    with pytest.raises(ValueError, match="knowledge_cutoff must be an ISO date string"):
        require_after_cutoff("m1", case, 20261029)


def test_single_domain():
    assert single_domain([]) == "earnings"
    assert single_domain([make_case("a"), make_case("b")]) == "earnings"
    assert single_domain(iter([make_macro("m1"), make_macro("m2")])) == "macro"
    with pytest.raises(ValueError) as info:
        single_domain([make_case("a"), make_macro("m1")])
    assert "'earnings'" in str(info.value) and "'macro'" in str(info.value)
    with pytest.raises(ValueError, match="'crypto'"):
        single_domain([make_macro("m1"), replace(make_case("a"), domain="crypto")])


# ---- the CLI is unchanged --------------------------------------------------------------

EARNINGS_CASE_KEYS = ["case_id", "company", "ticker", "aliases", "fiscal_period", "event_date",
                      "documents"]
EARNINGS_OUTCOME_KEYS = ["abnormal_return_1d", "stock_return_1d", "market_return_1d", "notes",
                         "sources"]


def test_earnings_dicts_have_the_keys_they_had():
    assert list(case_to_dict(make_case())) == EARNINGS_CASE_KEYS
    assert list(outcome_to_dict(make_outcome())) == EARNINGS_OUTCOME_KEYS
    assert list(outcome_to_dict(Outcome(targets={"DGS10_1w": target_outcome()})))[-1] == \
        "targets"


def test_cli_build_prints_and_writes_an_earnings_case_as_before(tmp_path, capsys):
    client = cli_client()
    code = main(cli_files(tmp_path), client_factory=lambda: client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    out = json.loads(captured.out)
    assert list(out["outcome"]) == EARNINGS_OUTCOME_KEYS
    case_dir = tmp_path / "root" / out["case_id"]
    assert list(json.loads((case_dir / CASE_FILE).read_text(encoding="utf-8"))) == \
        EARNINGS_CASE_KEYS
    assert list(json.loads((case_dir / OUTCOME_FILE).read_text(encoding="utf-8"))) == \
        EARNINGS_OUTCOME_KEYS
