"""Acceptance tests for macro cases (v21), written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "Macro cases: as-of snapshots, per-target outcomes (v21)" section of
SPEC.md. Earnings behaviour is pinned by the other acceptance files, which must all still pass.
"""

from __future__ import annotations

import builtins
import codecs
import json
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

import pytest

from researchos import cases, loop, masking
from researchos.cases import Case, Document, Outcome, Target, TargetOutcome

NL = chr(10)


def target(id="DGS10", horizons=None, kind="bp", as_of_date="2026-10-30", value=4.21, **kw):
    return Target(id=id, name=kw.pop("name", "10-year Treasury yield"), kind=kind,
                  horizons=list(horizons or ["1w", "1m", "3m"]), as_of_date=as_of_date,
                  as_of_value=value, **kw)


def docs():
    return [Document("dashboard", "Markets as of 2026-10-30",
                     "10y 4.21% (+12 bp on the week); S&P 500 6,512.3; revenue $1,200.0 million",
                     "https://example.com/a"),
            Document("statement", "FOMC statement", "The Committee decided to lower the range.",
                     "https://example.com/b")]


def macro(case_id="macro-2026-10-30", as_of="2026-10-30", targets=None):
    if targets is None:
        targets = [target(), target("SP500", ["1w"], "return", "2026-10-30", 6512.3,
                                    name="S&P 500", trailing={"1w": 0.012})]
    return cases.macro_case(case_id, as_of, targets, docs())


def earnings(case_id="acme-q4"):
    return Case(case_id=case_id, company="Acme Widgets Inc.", ticker="ACMW", aliases=["Acme"],
                fiscal_period="Q4 FY2025", event_date="2026-02-01",
                documents=[Document("release", "Acme results", "Revenue grew 12%.", "u")])


def outcome_for(case):
    out = {}
    for t in case.targets:
        for h in t.horizons:
            k = cases.target_key(t.id, h)
            out[k] = TargetOutcome(target=t.id, horizon=h, kind=t.kind, start_date=t.as_of_date,
                                   start=t.as_of_value, end_date="2026-11-06",
                                   end=t.as_of_value + 1, change=1.0, up=True)
    return Outcome(targets=out)


def spy_opens(monkeypatch):
    opened = []
    real_open, real_path_open = builtins.open, Path.open
    real_read_text, real_read_bytes = Path.read_text, Path.read_bytes

    def b_open(file, *a, **k):
        opened.append(Path(str(file)).name)
        return real_open(file, *a, **k)

    def p_open(self, *a, **k):
        opened.append(self.name)
        return real_path_open(self, *a, **k)

    def p_read_text(self, *a, **k):
        opened.append(self.name)
        return real_read_text(self, *a, **k)

    def p_read_bytes(self, *a, **k):
        opened.append(self.name)
        return real_read_bytes(self, *a, **k)

    monkeypatch.setattr(builtins, "open", b_open)
    monkeypatch.setattr(Path, "open", p_open)
    monkeypatch.setattr(Path, "read_text", p_read_text)
    monkeypatch.setattr(Path, "read_bytes", p_read_bytes)
    return opened


# --- constants and data types -----------------------------------------------------------

def test_constants():
    assert cases.DOMAINS == ("earnings", "macro")
    assert cases.KINDS == ("bp", "return", "points")
    assert cases.RESOLUTION_FILE == "resolution.json"


def test_target_fields_and_defaults():
    assert [f.name for f in fields(Target)] == ["id", "name", "kind", "horizons", "as_of_date",
                                               "as_of_value", "trailing"]
    a = Target("DGS10", "10y", "bp", ["1w"], "2026-10-30", 4.21)
    b = Target("DGS2", "2y", "bp", ["1w"], "2026-10-30", 3.5)
    assert a.trailing == {} and b.trailing == {}
    a.trailing["1w"] = 3.0
    assert b.trailing == {}


def test_target_outcome_fields():
    assert [f.name for f in fields(TargetOutcome)] == ["target", "horizon", "kind", "start_date",
                                                      "start", "end_date", "end", "change", "up"]


def test_case_gains_domain_and_targets_at_the_end():
    names = [f.name for f in fields(Case)]
    assert names == ["case_id", "company", "ticker", "aliases", "fiscal_period", "event_date",
                     "documents", "domain", "targets"]
    c = Case("c", "Co", "CO", [], "Q1", "2026-01-02", [])
    assert c.domain == "earnings" and c.targets == []
    d = Case("d", "Co", "CO", [], "Q1", "2026-01-02", [])
    c.targets.append(target())
    assert d.targets == []


def test_outcome_abnormal_return_optional_and_first():
    names = [f.name for f in fields(Outcome)]
    assert names == ["abnormal_return_1d", "stock_return_1d", "market_return_1d", "notes",
                     "sources", "targets"]
    assert Outcome(0.03).abnormal_return_1d == 0.03
    o = Outcome()
    assert o.abnormal_return_1d is None and o.targets == {}
    Outcome().targets["x"] = 1
    assert Outcome().targets == {}


# --- target_key ---------------------------------------------------------------------------

@pytest.mark.parametrize("tid,h,key", [
    ("DGS10", "1w", "DGS10_1w"), ("T10Y2Y", "3m", "T10Y2Y_3m"), ("hy_oas", "12m", "hy_oas_12m"),
    ("X", "10d", "X_10d"), ("_", "1y", "__1y"), ("DGS10_1w", "1m", "DGS10_1w_1m"),
])
def test_target_key(tid, h, key):
    assert cases.target_key(tid, h) == key


@pytest.mark.parametrize("tid", ["", "DGS-10", "DGS 10", "dgs.10", "DGS10" + NL, " DGS10",
                                 chr(0xE9), "A" + chr(0x661), 10, None, b"DGS10", ["DGS10"]])
def test_target_key_rejects_ids(tid):
    with pytest.raises(ValueError):
        cases.target_key(tid, "1w")


@pytest.mark.parametrize("h", [" 1w", "1w ", "1w" + NL, "0d", "01w", "1W", "1mo", "1 w", "",
                               "m", "1", None, 1, (1, "w")])
def test_target_key_rejects_horizons(h):
    with pytest.raises(ValueError):
        cases.target_key("DGS10", h)


# --- check_targets --------------------------------------------------------------------------

def test_check_targets_accepts_valid_lists():
    assert cases.check_targets([target()]) is None
    assert cases.check_targets([target(), target("SP500", ["1w"], "return", value=6512)]) is None
    assert cases.check_targets([target(kind="points", value=-3, as_of_date=" 2026-10-30 ")]) is None
    # name and trailing are not checked
    assert cases.check_targets([target(name=None, trailing="anything")]) is None


def test_check_targets_rejects_an_empty_list():
    with pytest.raises(ValueError):
        cases.check_targets([])


def test_check_targets_rejects_non_targets():
    with pytest.raises(ValueError):
        cases.check_targets([target(), {"id": "SP500"}])
    with pytest.raises(ValueError):
        cases.check_targets([None])


@pytest.mark.parametrize("tid", ["", "DGS-10", "x y", 7, None])
def test_check_targets_rejects_bad_ids(tid):
    with pytest.raises(ValueError):
        cases.check_targets([target(), target(tid)])


def test_check_targets_rejects_a_repeated_id():
    with pytest.raises(ValueError, match="DGS2"):
        cases.check_targets([target("DGS2"), target("DGS10"), target("DGS2", ["1w"])])


@pytest.mark.parametrize("kind", ["bps", "", None, "Return", "level"])
def test_check_targets_rejects_bad_kinds_naming_the_target(kind):
    with pytest.raises(ValueError, match="VIXCLS"):
        cases.check_targets([target(), target("VIXCLS", kind=kind)])


@pytest.mark.parametrize("horizons", [[], "1w", ("1w",), ["1w", "1w"], ["1w", " 1m"], ["1w", 1],
                                      ["1w", "1m", "1m"], None, ["0m"]])
def test_check_targets_rejects_bad_horizons_naming_the_target(horizons):
    t = target("DGS2")
    t.horizons = horizons
    with pytest.raises(ValueError, match="DGS2"):
        cases.check_targets([target(), t])


@pytest.mark.parametrize("day", ["2026-13-01", "2026-10-32", "", "Oct 30", 20261030, None,
                                 date(2026, 10, 30)])
def test_check_targets_rejects_bad_as_of_dates_naming_the_target(day):
    with pytest.raises(ValueError, match="DGS2"):
        cases.check_targets([target(), target("DGS2", as_of_date=day)])


@pytest.mark.parametrize("value", [True, False, "4.21", None, float("nan"), float("inf"),
                                   -float("inf"), [4.21]])
def test_check_targets_rejects_bad_values_naming_the_target(value):
    with pytest.raises(ValueError, match="DGS2"):
        cases.check_targets([target(), target("DGS2", value=value)])


@pytest.mark.parametrize("value", [0, -1, 4, 4.21, -0.35, 10 ** 30])
def test_check_targets_accepts_numbers(value):
    cases.check_targets([target(value=value)])


# --- target_keys ----------------------------------------------------------------------------

def test_target_keys_in_target_then_horizon_order():
    c = macro(targets=[target("SP500", ["1m", "1w"], "return", value=6512),
                       target("DGS10", ["3m", "1w"])])
    assert cases.target_keys(c) == ["SP500_1m", "SP500_1w", "DGS10_3m", "DGS10_1w"]


def test_target_keys_of_an_earnings_case_is_empty():
    assert cases.target_keys(earnings()) == []


def test_target_keys_checks_the_targets():
    c = macro()
    c.targets.append(target("SP500", ["1w"]))
    with pytest.raises(ValueError, match="SP500"):
        cases.target_keys(c)


# --- macro_case -----------------------------------------------------------------------------

def test_macro_case_fields():
    ts = [target(), target("SP500", ["1w"], "return", value=6512.3)]
    ds = docs()
    c = cases.macro_case("macro-2026-10-30", "2026-10-30", ts, ds)
    assert c.case_id == "macro-2026-10-30"
    assert (c.company, c.ticker, c.aliases) == ("", "", [])
    assert c.fiscal_period == "as of 2026-10-30"
    assert c.event_date == "2026-10-30"
    assert c.domain == "macro"
    assert c.targets == ts and c.documents == ds
    ts.append(target("DGS2"))
    ds.pop()
    assert len(c.targets) == 2 and len(c.documents) == 2
    assert cases.identity_terms(c) == []


def test_macro_case_stores_the_canonical_date():
    c = cases.macro_case("m", " 2026-10-30 ", [target()], [])
    assert c.event_date == "2026-10-30" and c.fiscal_period == "as of 2026-10-30"


@pytest.mark.parametrize("as_of", ["2026-10-32", "", "30 Oct 2026", None, date(2026, 10, 30),
                                   20261030])
def test_macro_case_rejects_bad_dates(as_of):
    with pytest.raises(ValueError):
        cases.macro_case("m", as_of, [target()], [])


def test_macro_case_checks_targets():
    with pytest.raises(ValueError):
        cases.macro_case("m", "2026-10-30", [], [])
    with pytest.raises(ValueError, match="SP500"):
        cases.macro_case("m", "2026-10-30", [target(), target("SP500", kind="pct")], [])


def test_macro_case_refuses_a_level_after_the_snapshot():
    with pytest.raises(ValueError, match="SP500"):
        cases.macro_case("m", "2026-10-30",
                         [target(), target("SP500", as_of_date="2026-10-31")], [])
    with pytest.raises(ValueError, match="DGS10"):
        cases.macro_case("m", "2026-10-30", [target(as_of_date=" 2026-11-02")], [])


def test_macro_case_accepts_levels_on_or_before_the_snapshot():
    c = cases.macro_case("m", "2026-07-03",
                         [target(as_of_date="2026-07-02"), target("SP500", as_of_date="2026-07-03")],
                         [])
    assert [t.as_of_date for t in c.targets] == ["2026-07-02", "2026-07-03"]


# --- files ----------------------------------------------------------------------------------

OLD_CASE_KEYS = ["case_id", "company", "ticker", "aliases", "fiscal_period", "event_date",
                 "documents"]
OLD_OUTCOME_KEYS = ["abnormal_return_1d", "stock_return_1d", "market_return_1d", "notes",
                    "sources"]


def test_earnings_files_are_what_they_were(tmp_path):
    c = earnings()
    o = Outcome(0.031, 0.04, 0.009, "n", ["s"])
    cases.save_case(tmp_path, c, o)
    case_raw = (tmp_path / c.case_id / "case.json").read_bytes()
    out_raw = (tmp_path / c.case_id / "outcome.json").read_bytes()
    old_case = {k: v for k, v in asdict(c).items() if k in OLD_CASE_KEYS}
    old_out = {k: v for k, v in asdict(o).items() if k in OLD_OUTCOME_KEYS}
    assert list(json.loads(case_raw)) == OLD_CASE_KEYS
    assert list(json.loads(out_raw)) == OLD_OUTCOME_KEYS
    assert case_raw == (json.dumps(old_case, indent=2, ensure_ascii=False) + NL).encode("utf-8")
    assert out_raw == (json.dumps(old_out, indent=2, ensure_ascii=False) + NL).encode("utf-8")
    assert cases.load_case(tmp_path, c.case_id) == c
    assert cases.load_outcome(tmp_path, c.case_id) == o


def test_non_default_domain_or_targets_keep_both_keys(tmp_path):
    a = earnings("a")
    a.targets = [target()]
    b = earnings("b")
    b.domain = "macro"
    for c in (a, b):
        cases.save_case(tmp_path, c, Outcome(0.01))
        data = json.loads((tmp_path / c.case_id / "case.json").read_text(encoding="utf-8"))
        assert list(data) == OLD_CASE_KEYS + ["domain", "targets"]
        assert cases.load_case(tmp_path, c.case_id) == c


def test_macro_case_round_trip_without_outcome(tmp_path):
    c = macro()
    cases.save_case(tmp_path, c)
    case_dir = tmp_path / c.case_id
    assert sorted(p.name for p in case_dir.iterdir()) == ["case.json"]
    data = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    assert data == asdict(c)
    back = cases.load_case(tmp_path, c.case_id)
    assert back == c
    assert all(isinstance(t, Target) for t in back.targets)
    assert back.targets[1].trailing == {"1w": 0.012}


def test_save_case_without_outcome_leaves_outcome_alone(tmp_path):
    c = macro()
    case_dir = tmp_path / c.case_id
    case_dir.mkdir()
    (case_dir / "outcome.json").write_bytes(b"not json, and not to be touched")
    cases.save_case(tmp_path, c, None)
    assert (case_dir / "outcome.json").read_bytes() == b"not json, and not to be touched"
    assert cases.load_case(tmp_path, c.case_id) == c


def test_save_case_with_outcome_writes_both(tmp_path):
    c = macro()
    o = outcome_for(c)
    cases.save_case(tmp_path, c, o)
    assert cases.load_outcome(tmp_path, c.case_id) == o


def test_save_outcome_and_load_it_back(tmp_path):
    c = macro()
    o = outcome_for(c)
    cases.save_outcome(tmp_path / "new-root", c.case_id, o)
    path = tmp_path / "new-root" / c.case_id / "outcome.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == asdict(o)
    assert data["abnormal_return_1d"] is None
    back = cases.load_outcome(tmp_path / "new-root", c.case_id)
    assert back == o
    assert all(isinstance(v, TargetOutcome) for v in back.targets.values())
    assert list(back.targets) == list(o.targets)


def test_save_outcome_without_targets_has_no_targets_key(tmp_path):
    cases.save_outcome(tmp_path, "e", Outcome(-0.02, notes="x"))
    data = json.loads((tmp_path / "e" / "outcome.json").read_text(encoding="utf-8"))
    assert list(data) == OLD_OUTCOME_KEYS
    assert cases.load_outcome(tmp_path, "e") == Outcome(-0.02, notes="x")


def test_save_outcome_replaces_an_earlier_one(tmp_path):
    c = macro()
    cases.save_case(tmp_path, c)
    first = Outcome(targets={"DGS10_1w": outcome_for(c).targets["DGS10_1w"]})
    cases.save_outcome(tmp_path, c.case_id, first)
    full = outcome_for(c)
    cases.save_outcome(tmp_path, c.case_id, full)
    assert cases.load_outcome(tmp_path, c.case_id) == full


def write(path, data, mark=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mark + json.dumps(data).encode("utf-8"))


def test_load_outcome_without_targets_is_as_before(tmp_path):
    write(tmp_path / "x" / "outcome.json", {"notes": "no return"})
    with pytest.raises(KeyError):
        cases.load_outcome(tmp_path, "x")
    write(tmp_path / "y" / "outcome.json", {"abnormal_return_1d": 0.02})
    assert cases.load_outcome(tmp_path, "y") == Outcome(0.02)


def test_load_outcome_with_targets_defaults_the_return(tmp_path):
    entry = {"target": "VIXCLS", "horizon": "1w", "kind": "points", "start_date": "2026-10-30",
             "start": 17.4, "end_date": "2026-11-06", "end": 16.1, "change": -1.3, "up": False}
    write(tmp_path / "x" / "outcome.json", {"targets": {"VIXCLS_1w": entry}})
    o = cases.load_outcome(tmp_path, "x")
    assert o.abnormal_return_1d is None
    assert o.targets == {"VIXCLS_1w": TargetOutcome(**entry)}
    assert o.notes == "" and o.sources == []


@pytest.mark.parametrize("missing", ["target", "horizon", "kind", "start_date", "start",
                                     "end_date", "end", "change", "up"])
def test_target_outcome_fields_are_required(tmp_path, missing):
    entry = {"target": "VIXCLS", "horizon": "1w", "kind": "points", "start_date": "2026-10-30",
             "start": 17.4, "end_date": "2026-11-06", "end": 16.1, "change": -1.3, "up": False}
    del entry[missing]
    with pytest.raises(KeyError):
        cases.target_outcome_from_dict(entry)
    write(tmp_path / "x" / "outcome.json", {"targets": {"VIXCLS_1w": entry}})
    with pytest.raises(KeyError):
        cases.load_outcome(tmp_path, "x")


def test_target_from_dict_defaults():
    data = {"id": "DGS10", "kind": "bp", "horizons": ["1w"], "as_of_date": "2026-10-30",
            "as_of_value": 4.21}
    t = cases.target_from_dict(data)
    assert t == Target("DGS10", "", "bp", ["1w"], "2026-10-30", 4.21, {})
    full = dict(data, name="10y", trailing={"1w": -3.0})
    t = cases.target_from_dict(full)
    assert t.name == "10y" and t.trailing == {"1w": -3.0}
    t.horizons.append("1m")
    t.trailing["1m"] = 1.0
    assert full["horizons"] == ["1w"] and full["trailing"] == {"1w": -3.0}


@pytest.mark.parametrize("missing", ["id", "kind", "horizons", "as_of_date", "as_of_value"])
def test_target_from_dict_requires(missing):
    data = {"id": "DGS10", "kind": "bp", "horizons": ["1w"], "as_of_date": "2026-10-30",
            "as_of_value": 4.21}
    del data[missing]
    with pytest.raises(KeyError):
        cases.target_from_dict(data)


def test_load_case_of_a_file_from_before_v21(tmp_path):
    data = {k: v for k, v in asdict(earnings()).items() if k in OLD_CASE_KEYS}
    write(tmp_path / "acme-q4" / "case.json", data)
    c = cases.load_case(tmp_path, "acme-q4")
    assert c.domain == "earnings" and c.targets == []
    assert c == earnings()


def test_load_case_reads_a_file_with_a_byte_order_mark(tmp_path):
    c = macro()
    write(tmp_path / c.case_id / "case.json", asdict(c), codecs.BOM_UTF8)
    assert cases.load_case(tmp_path, c.case_id) == c


def test_load_case_never_opens_outcome_or_resolution(tmp_path, monkeypatch):
    c = macro()
    cases.save_case(tmp_path, c, outcome_for(c))
    cases.save_resolution(tmp_path, c.case_id, cases.resolution_manifest(c))
    opened = spy_opens(monkeypatch)
    assert cases.load_case(tmp_path, c.case_id) == c
    monkeypatch.undo()
    assert "case.json" in opened
    assert "outcome.json" not in opened


# --- the resolution manifest ----------------------------------------------------------------

def test_resolution_manifest_shape():
    c = macro()
    m = cases.resolution_manifest(c)
    assert m == {"as_of": "2026-10-30",
                 "targets": {"DGS10_1w": {"horizon_end": "2026-11-06", "resolved": False},
                             "DGS10_1m": {"horizon_end": "2026-11-30", "resolved": False},
                             "DGS10_3m": {"horizon_end": "2027-01-30", "resolved": False},
                             "SP500_1w": {"horizon_end": "2026-11-06", "resolved": False}}}
    assert list(m) == ["as_of", "targets"]
    assert list(m["targets"]) == cases.target_keys(c)
    assert list(m["targets"]["DGS10_1w"]) == ["horizon_end", "resolved"]


def test_resolution_manifest_marks_resolved_keys():
    c = macro()
    m = cases.resolution_manifest(c, ["DGS10_1w", "SP500_1w"])
    assert {k: v["resolved"] for k, v in m["targets"].items()} == {
        "DGS10_1w": True, "DGS10_1m": False, "DGS10_3m": False, "SP500_1w": True}
    assert all(v["resolved"] is True or v["resolved"] is False for v in m["targets"].values())
    assert cases.resolution_manifest(c, {"DGS10_3m"})["targets"]["DGS10_3m"]["resolved"] is True
    gen = (k for k in ["DGS10_1m"])
    assert cases.resolution_manifest(c, gen)["targets"]["DGS10_1m"]["resolved"] is True


def test_resolution_manifest_month_ends_and_holidays():
    c = cases.macro_case("m", "2026-07-03", [target(as_of_date="2026-07-02",
                                                    horizons=["1w", "10d", "1m", "3m", "1y"])], [])
    ends = {k: v["horizon_end"] for k, v in cases.resolution_manifest(c)["targets"].items()}
    assert ends == {"DGS10_1w": "2026-07-10", "DGS10_10d": "2026-07-13",
                    "DGS10_1m": "2026-08-03", "DGS10_3m": "2026-10-03", "DGS10_1y": "2027-07-03"}
    c = cases.macro_case("m", "2026-01-30", [target(as_of_date="2026-01-30", horizons=["1m"])], [])
    assert cases.resolution_manifest(c)["targets"]["DGS10_1m"]["horizon_end"] == "2026-02-28"


def test_resolution_manifest_holds_no_numbers():
    m = cases.resolution_manifest(macro(), ["DGS10_1w"])

    def leaves(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from leaves(v)
        else:
            yield x

    assert all(isinstance(v, (str, bool)) for v in leaves(m))
    text = json.dumps(m)
    assert "4.21" not in text and "6512" not in text


def test_resolution_manifest_rejects_unknown_keys():
    with pytest.raises(ValueError):
        cases.resolution_manifest(macro(), ["DGS10_1y"])
    with pytest.raises(ValueError):
        cases.resolution_manifest(macro(), ["DGS10_1w", "VIX_1w"])


def test_resolution_manifest_only_for_macro_cases():
    with pytest.raises(ValueError):
        cases.resolution_manifest(earnings())
    c = earnings()
    c.targets = [target()]
    with pytest.raises(ValueError):
        cases.resolution_manifest(c)


@pytest.mark.parametrize("day", ["2026-10-32", "", None, date(2026, 10, 30)])
def test_resolution_manifest_needs_an_iso_event_date(day):
    c = macro()
    c.event_date = day
    with pytest.raises(ValueError):
        cases.resolution_manifest(c)


def test_resolution_manifest_canonical_as_of():
    c = macro()
    c.event_date = " 2026-10-30 "
    m = cases.resolution_manifest(c)
    assert m["as_of"] == "2026-10-30"
    assert m["targets"]["DGS10_1w"]["horizon_end"] == "2026-11-06"


def test_resolution_manifest_checks_targets():
    c = macro()
    c.targets[1].kind = "percent"
    with pytest.raises(ValueError, match="SP500"):
        cases.resolution_manifest(c)


def test_resolution_manifest_past_the_last_date():
    c = cases.macro_case("m", "9999-12-01", [target(as_of_date="9999-12-01", horizons=["1m"])], [])
    with pytest.raises(ValueError):
        cases.resolution_manifest(c)


def test_save_and_load_resolution(tmp_path):
    c = macro()
    m = cases.resolution_manifest(c, ["DGS10_1w"])
    assert cases.load_resolution(tmp_path, c.case_id) is None
    cases.save_resolution(tmp_path / "root", c.case_id, m)
    path = tmp_path / "root" / c.case_id / "resolution.json"
    assert json.loads(path.read_text(encoding="utf-8")) == m
    assert cases.load_resolution(tmp_path / "root", c.case_id) == m


def test_load_resolution_reads_a_file_with_a_byte_order_mark(tmp_path):
    m = cases.resolution_manifest(macro(), ["SP500_1w"])
    path = tmp_path / "m" / "resolution.json"
    path.parent.mkdir()
    path.write_bytes(codecs.BOM_UTF16_LE + json.dumps(m).encode("utf-16-le"))
    assert cases.load_resolution(tmp_path, "m") == m


def test_load_resolution_never_opens_outcome(tmp_path, monkeypatch):
    c = macro()
    cases.save_case(tmp_path, c, outcome_for(c))
    cases.save_resolution(tmp_path, c.case_id, cases.resolution_manifest(c))
    opened = spy_opens(monkeypatch)
    assert cases.load_resolution(tmp_path, c.case_id)["as_of"] == "2026-10-30"
    monkeypatch.undo()
    assert "outcome.json" not in opened


# --- unresolved_targets ---------------------------------------------------------------------

def test_unresolved_targets_with_no_manifest():
    c = macro()
    keys = ["DGS10_1w", "DGS10_1m", "DGS10_3m", "SP500_1w"]
    assert cases.unresolved_targets(c, None) == keys
    assert cases.unresolved_targets(c, {}) == keys
    assert cases.unresolved_targets(c, []) == keys
    assert cases.unresolved_targets(c, "resolved") == keys
    assert cases.unresolved_targets(c, {"targets": []}) == keys
    assert cases.unresolved_targets(c, {"targets": None}) == keys


def test_unresolved_targets_from_a_manifest():
    c = macro()
    m = cases.resolution_manifest(c, ["DGS10_1w", "SP500_1w"])
    assert cases.unresolved_targets(c, m) == ["DGS10_1m", "DGS10_3m"]
    m = cases.resolution_manifest(c, cases.target_keys(c))
    assert cases.unresolved_targets(c, m) == []


@pytest.mark.parametrize("flag", ["true", 1, 1.0, "True", None, [True]])
def test_only_true_counts_as_resolved(flag):
    c = macro()
    m = cases.resolution_manifest(c, cases.target_keys(c))
    m["targets"]["DGS10_3m"]["resolved"] = flag
    assert cases.unresolved_targets(c, m) == ["DGS10_3m"]


def test_unresolved_targets_odd_entries():
    c = macro()
    m = cases.resolution_manifest(c, cases.target_keys(c))
    m["targets"]["DGS10_1w"] = True
    m["targets"]["DGS10_1m"] = {"horizon_end": "2026-11-30"}
    del m["targets"]["SP500_1w"]
    m["targets"]["VIX_1w"] = {"resolved": True}
    m["targets"]["DGS10_1y"] = {"resolved": False}
    assert cases.unresolved_targets(c, m) == ["DGS10_1w", "DGS10_1m", "SP500_1w"]


def test_unresolved_targets_of_an_earnings_case():
    assert cases.unresolved_targets(earnings(), None) == []
    assert cases.unresolved_targets(earnings(), {"targets": {}}) == []


def test_unresolved_targets_checks_targets():
    c = macro()
    c.targets[0].horizons = ["1w", "1w"]
    with pytest.raises(ValueError, match="DGS10"):
        cases.unresolved_targets(c, None)


# --- masking.unmasked -----------------------------------------------------------------------

def test_unmasked_keeps_the_documents_as_they_are():
    c = macro()
    m = masking.unmasked(c)
    assert isinstance(m, masking.MaskedCase)
    assert m.case_id == c.case_id
    assert m.status == "unmasked"
    assert (m.rounds, m.mapping, m.factor, m.year_offset) == (0, {}, 1.0, 0)
    assert (m.final_guesses, m.abstracted, m.redaction_rounds) == ([], False, [])
    assert [(d.kind, d.title, d.text, d.source) for d in m.documents] == [
        (d.kind, d.title, d.text, "") for d in c.documents]
    assert "$1,200.0 million" in m.documents[0].text and "2026-10-30" in m.documents[0].title


def test_unmasked_copies():
    c = macro()
    m = masking.unmasked(c)
    m.documents[0].text = "changed"
    m.documents.append(Document("x", "y", "z"))
    assert c.documents[0].text.startswith("10y 4.21%") and len(c.documents) == 2
    assert c.documents[0].source == "https://example.com/a"


def test_unmasked_of_an_earnings_case_hides_nothing():
    c = earnings()
    m = masking.unmasked(c)
    assert m.documents[0].text == "Revenue grew 12%." and m.documents[0].title == "Acme results"
    assert m.status == "unmasked"


def test_unmasked_touches_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def refuse(*a, **k):
        raise AssertionError("no file may be opened")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "write_bytes", refuse)
    monkeypatch.setattr(Path, "write_text", refuse)
    m = masking.unmasked(macro())
    monkeypatch.undo()
    assert m.status == "unmasked"
    assert list(tmp_path.iterdir()) == []


# --- loop.require_after_cutoff ----------------------------------------------------------------

def test_require_after_cutoff_passes_after():
    c = macro()
    assert loop.require_after_cutoff("m1", c, "2026-06-30") is None
    assert loop.require_after_cutoff("m1", c, date(2026, 10, 29)) is None
    assert loop.require_after_cutoff("m1", c, " 2026-06-30 ") is None


@pytest.mark.parametrize("cutoff", ["2026-10-30", date(2026, 10, 30), "2026-12-31"])
def test_require_after_cutoff_refuses_on_or_before(cutoff):
    with pytest.raises(ValueError, match="m1"):
        loop.require_after_cutoff("m1", macro(), cutoff)


def test_require_after_cutoff_needs_a_cutoff():
    with pytest.raises(ValueError, match="m1"):
        loop.require_after_cutoff("m1", macro(), None)


@pytest.mark.parametrize("cutoff", ["June 2026", "2026-06-31", 20260630, 2026.5])
def test_require_after_cutoff_bad_cutoffs(cutoff):
    with pytest.raises(ValueError):
        loop.require_after_cutoff("m1", macro(), cutoff)


@pytest.mark.parametrize("day", ["2026-07-03x", "", None, date(2026, 7, 3), 20260703])
def test_require_after_cutoff_needs_an_iso_event_date(day):
    c = macro()
    c.event_date = day
    with pytest.raises(ValueError, match="m1"):
        loop.require_after_cutoff("m1", c, "2026-06-30")


def test_require_after_cutoff_reads_a_padded_event_date():
    c = macro()
    c.event_date = " 2026-07-03 "
    assert loop.require_after_cutoff("m1", c, "2026-06-30") is None
    with pytest.raises(ValueError, match="m1"):
        loop.require_after_cutoff("m1", c, "2026-07-03")


# --- loop.single_domain --------------------------------------------------------------------

def test_single_domain():
    assert loop.single_domain([]) == "earnings"
    assert loop.single_domain(iter([])) == "earnings"
    assert loop.single_domain([earnings("a"), earnings("b")]) == "earnings"
    assert loop.single_domain([macro("a"), macro("b")]) == "macro"
    assert loop.single_domain(c for c in [macro("a")]) == "macro"


def test_single_domain_refuses_a_mix():
    with pytest.raises(ValueError) as exc:
        loop.single_domain([earnings("a"), macro("b"), earnings("c")])
    assert "earnings" in str(exc.value) and "macro" in str(exc.value)


def test_single_domain_refuses_an_unknown_domain():
    odd = earnings("odd")
    odd.domain = "rates"
    with pytest.raises(ValueError, match="rates"):
        loop.single_domain([earnings("a"), odd])
    with pytest.raises(ValueError, match="rates"):
        loop.single_domain([odd])


# --- the source --------------------------------------------------------------------------------

def test_new_code_uses_the_shared_date_helpers():
    text = Path(cases.__file__).read_text(encoding="utf-8")
    assert "parse_horizon" in text and "horizon_end" in text and "parse_iso_date" in text
    for module in (cases, masking, loop):
        Path(module.__file__).read_text(encoding="utf-8").encode("ascii")
