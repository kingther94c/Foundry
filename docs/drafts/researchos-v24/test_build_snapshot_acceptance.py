"""Acceptance tests for building a snapshot (v24), written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "Building a snapshot (v24)" section of SPEC.md. Nothing touches the
network: fetch_page is a fake, or urllib.request.urlopen is replaced.
"""

from __future__ import annotations

import codecs
import json
import logging
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchos import cases, snapshot
from researchos.__main__ import main

NL = chr(10)
AS_OF = date(2026, 10, 30)
CASE_ID = "macro-2026-10-30"

SERIES = {
    "DGS10": [("2025-10-30", 4.10), ("2026-07-30", 4.40), ("2026-09-30", 5.31), ("2026-10-23", 5.00),
              ("2026-10-29", 4.98), ("2026-11-02", 9.99)],
    "SP500": [("2025-10-29", 5000.0), ("2026-07-30", 7000.0), ("2026-09-30", 7600.0),
              ("2026-10-23", 7800.0), ("2026-10-28", 7743.98)],
    "UNRATE": [("2026-08-01", 4.1), ("2026-09-01", 4.3), ("2026-10-01", 4.4)],
    "UNRATE@2026-10-30": [("2025-09-01", 4.1), ("2026-08-01", 4.1), ("2026-09-01", 4.2)],
    "PAYEMS": [("2026-08-01", 159015.0), ("2026-09-01", 159044.0)],
    "TY_net": [("2026-10-20", -964293.0), ("2026-10-27", -981538.0)],
    "TY_oi": [("2026-10-20", 5868295.0), ("2026-10-27", 5881588.0)],
}

PAGES = {"https://fed.example/0916": "September statement text.",
         "https://fed.example/1028": "The Committee decided to lower the target range."}


def config():
    return {
        "case_prefix": "macro",
        "horizons": ["1w", "1m", "3m"],
        "targets": [
            {"id": "DGS10", "name": "10-year Treasury yield", "unit": "percent", "kind": "bp"},
            {"id": "SP500", "name": "S&P 500", "unit": "index", "kind": "return", "optional": False},
            {"id": "NASDAQQGLDI", "name": "Gold", "unit": "index", "kind": "return",
             "optional": True},
        ],
        "context": [{"id": "UNRATE", "name": "Unemployment rate", "unit": "percent"},
                    {"id": "PAYEMS", "name": "Nonfarm payrolls", "unit": "thousands"}],
        "positioning": [{"id": "TY", "name": "10-year T-note futures", "net": "TY_net",
                         "open_interest": "TY_oi", "release_lag_days": 3}],
        "documents": [
            {"kind": "statement", "title": "FOMC statement", "dated": "2026-09-16",
             "url": "https://fed.example/0916"},
            {"kind": "statement", "title": "FOMC statement", "dated": "2026-10-28",
             "url": "https://fed.example/1028"},
            {"kind": "statement", "title": "FOMC statement", "dated": "2026-12-09",
             "url": "https://fed.example/1209"},
            {"kind": "jobs", "title": "Employment Situation", "dated": "2026-10-02",
             "text": "Payrolls rose 22,000."},
            {"kind": "jobs", "title": "Employment Situation", "dated": "2026-11-06", "text": "later"},
            {"kind": "cpi", "title": "CPI", "dated": "2026-11-12", "url": "https://bls.example/cpi"},
        ],
        "calendar": [{"date": "2026-11-04", "event": "FOMC decision"},
                     {"date": "2027-01-30", "event": "edge"},
                     {"date": "2027-02-01", "event": "too late"}],
    }


def write_series(directory, data=SERIES):
    directory.mkdir(parents=True, exist_ok=True)
    for name, pairs in data.items():
        (directory / f"{name}.json").write_text(json.dumps([list(p) for p in pairs]), encoding="utf-8")
    return directory


def obs(name):
    return [(date.fromisoformat(d), v) for d, v in SERIES[name]]


class Fetch:
    def __init__(self, pages=PAGES, fail=()):
        self.pages, self.fail, self.calls = pages, dict(fail), []

    def __call__(self, url):
        self.calls.append(url)
        if url in self.fail:
            raise self.fail[url]
        return SimpleNamespace(url=url, title="ignored", text=self.pages[url])


@pytest.fixture
def series_dir(tmp_path):
    return write_series(tmp_path / "series")


def build(tmp_path, series_dir, cfg=None, fetch=None, as_of=AS_OF, root=None):
    return snapshot.build_snapshot(root or tmp_path / "cases", cfg or config(), as_of,
                                   series_dir=series_dir, fetch_page=fetch or Fetch())


# --- build_snapshot -------------------------------------------------------------------------------

def test_summary(tmp_path, series_dir):
    assert build(tmp_path, series_dir) == {
        "case_id": CASE_ID, "as_of": "2026-10-30", "targets": ["DGS10", "SP500"],
        "left_out": ["NASDAQQGLDI"],
        "documents": ["dashboard", "economy", "positioning", "calendar", "statement", "jobs"],
        "keys": ["DGS10_1w", "DGS10_1m", "DGS10_3m", "SP500_1w", "SP500_1m", "SP500_3m"]}


def test_the_saved_case(tmp_path, series_dir):
    build(tmp_path, series_dir)
    root = tmp_path / "cases"
    case = cases.load_case(root, CASE_ID)
    assert case.domain == "macro" and case.event_date == "2026-10-30"
    assert (case.company, case.ticker, case.aliases) == ("", "", [])
    assert case.fiscal_period == "as of 2026-10-30"
    assert sorted(p.name for p in (root / CASE_ID).iterdir()) == ["case.json", "resolution.json"]
    assert cases.load_resolution(root, CASE_ID) == cases.resolution_manifest(case)
    assert cases.unresolved_targets(case, cases.load_resolution(root, CASE_ID)) == \
        cases.target_keys(case)


def test_targets(tmp_path, series_dir):
    build(tmp_path, series_dir)
    dgs10, sp500 = cases.load_case(tmp_path / "cases", CASE_ID).targets
    assert (dgs10.id, dgs10.name, dgs10.kind, dgs10.horizons) == (
        "DGS10", "10-year Treasury yield", "bp", ["1w", "1m", "3m"])
    assert (dgs10.as_of_date, dgs10.as_of_value) == ("2026-10-29", 4.98)
    assert dgs10.trailing == pytest.approx({"1w": -2.0, "1m": -33.0, "3m": 58.0})
    assert (sp500.as_of_date, sp500.as_of_value) == ("2026-10-28", 7743.98)
    assert sp500.trailing["3m"] == pytest.approx(7743.98 / 7000 - 1)


def test_documents(tmp_path, series_dir):
    fetch = Fetch()
    build(tmp_path, series_dir, fetch=fetch)
    docs = cases.load_case(tmp_path / "cases", CASE_ID).documents
    cfg = config()
    assert [(d.kind, d.title) for d in docs] == [
        ("dashboard", "Markets as of 2026-10-30"), ("economy", "Economic data as of 2026-10-30"),
        ("positioning", "Positioning as of 2026-10-30"), ("calendar", "Calendar after 2026-10-30"),
        ("statement", "FOMC statement (2026-10-28)"), ("jobs", "Employment Situation (2026-10-02)")]
    assert docs[0].text == snapshot.target_table(AS_OF, cfg["targets"][:2],
                                                 {"DGS10": obs("DGS10"), "SP500": obs("SP500")})
    assert docs[1].text == snapshot.context_table(
        AS_OF, cfg["context"], {"UNRATE": obs("UNRATE@2026-10-30"), "PAYEMS": obs("PAYEMS")},
        ["UNRATE"])
    assert "| 4.20 | 2026-09-01 |" in docs[1].text and "as published by 2026-10-30" in docs[1].text
    assert docs[2].text == snapshot.positioning_table(
        AS_OF, cfg["positioning"], {"TY_net": obs("TY_net"), "TY_oi": obs("TY_oi")})
    assert docs[3].text == snapshot.calendar_list(AS_OF, cfg["calendar"], date(2027, 1, 30))
    assert docs[4].text == PAGES["https://fed.example/1028"]
    assert docs[4].source == "https://fed.example/1028"
    assert (docs[5].text, docs[5].source) == ("Payrolls rose 22,000.", "")
    assert all(d.source == "" for d in docs[:4])
    assert fetch.calls == ["https://fed.example/1028"]


def test_the_targets_series_vintage_file_is_used(tmp_path, series_dir):
    (series_dir / "DGS10@2026-10-30.json").write_text('[["2026-10-30", 5.5]]', encoding="utf-8")
    build(tmp_path, series_dir)
    t = cases.load_case(tmp_path / "cases", CASE_ID).targets[0]
    assert (t.as_of_date, t.as_of_value) == ("2026-10-30", 5.5)


def test_no_value_from_after_the_date_reaches_the_case(tmp_path, series_dir):
    build(tmp_path, series_dir, root=tmp_path / "a")
    later = {k: v + [("2026-10-31", 1.0), ("2026-12-01", 2.0)] for k, v in SERIES.items()}
    write_series(tmp_path / "later", later)
    build(tmp_path, tmp_path / "later", root=tmp_path / "b")
    for name in ("case.json", "resolution.json"):
        assert (tmp_path / "a" / CASE_ID / name).read_bytes() == \
            (tmp_path / "b" / CASE_ID / name).read_bytes()


def test_the_same_inputs_give_the_same_bytes(tmp_path, series_dir):
    build(tmp_path, series_dir, root=tmp_path / "a")
    build(tmp_path, series_dir, root=tmp_path / "b", as_of="2026-10-30")
    for name in ("case.json", "resolution.json"):
        assert (tmp_path / "a" / CASE_ID / name).read_bytes() == \
            (tmp_path / "b" / CASE_ID / name).read_bytes()


def test_rebuilding_before_it_is_sealed_rewrites(tmp_path, series_dir):
    build(tmp_path, series_dir)
    first = (tmp_path / "cases" / CASE_ID / "case.json").read_bytes()
    (tmp_path / "cases" / CASE_ID / "case.json").write_text("{}", encoding="utf-8")
    build(tmp_path, series_dir)
    assert (tmp_path / "cases" / CASE_ID / "case.json").read_bytes() == first


@pytest.mark.parametrize("name", ["prediction.json", "outcome.json"])
def test_never_rebuilt_under_a_seal_or_an_outcome(tmp_path, series_dir, name):
    build(tmp_path, series_dir)
    case_dir = tmp_path / "cases" / CASE_ID
    (case_dir / name).write_text("{}", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in case_dir.iterdir()}
    fetch = Fetch()
    with pytest.raises(ValueError, match=CASE_ID):
        build(tmp_path, series_dir, fetch=fetch)
    assert {p.name: p.read_bytes() for p in case_dir.iterdir()} == before
    assert fetch.calls == []


def test_a_missing_optional_target_is_left_out_with_a_warning(tmp_path, series_dir, caplog):
    with caplog.at_level(logging.WARNING, logger="researchos"):
        summary = build(tmp_path, series_dir)
    assert summary["left_out"] == ["NASDAQQGLDI"]
    assert any("NASDAQQGLDI" in r.getMessage() for r in caplog.records)
    case = cases.load_case(tmp_path / "cases", CASE_ID)
    assert "NASDAQQGLDI" not in case.documents[0].text


def test_an_optional_target_with_data_is_kept(tmp_path, series_dir):
    (series_dir / "NASDAQQGLDI.json").write_text('[["2026-10-29", 2460.63]]', encoding="utf-8")
    summary = build(tmp_path, series_dir)
    assert summary["targets"] == ["DGS10", "SP500", "NASDAQQGLDI"] and summary["left_out"] == []


def test_missing_required_targets_refuse_and_write_nothing(tmp_path, series_dir):
    (series_dir / "SP500.json").unlink()
    (series_dir / "DGS10.json").write_text('[["2026-11-02", 5.0]]', encoding="utf-8")
    fetch = Fetch()
    with pytest.raises(ValueError) as exc:
        build(tmp_path, series_dir, fetch=fetch)
    assert "DGS10" in str(exc.value) and "SP500" in str(exc.value)
    assert not (tmp_path / "cases").exists() or list((tmp_path / "cases").rglob("*")) == []
    assert fetch.calls == []


def test_a_failed_fetch_leaves_the_document_out(tmp_path, series_dir, caplog):
    fetch = Fetch(fail={"https://fed.example/1028": OSError("down")})
    with caplog.at_level(logging.WARNING, logger="researchos"):
        summary = build(tmp_path, series_dir, fetch=fetch)
    assert summary["documents"] == ["dashboard", "economy", "positioning", "calendar", "jobs"]
    assert any("https://fed.example/1028" in r.getMessage() for r in caplog.records)
    fetch = Fetch(fail={"https://fed.example/1028": ValueError("a PDF")})
    assert build(tmp_path, series_dir, fetch=fetch, root=tmp_path / "b")["documents"][-1] == "jobs"


def test_the_later_entry_wins_a_tie(tmp_path, series_dir):
    cfg = config()
    cfg["documents"] = [{"kind": "note", "title": "A", "dated": "2026-10-01", "text": "first"},
                        {"kind": "note", "title": "B", "dated": "2026-10-01", "text": "second"},
                        {"kind": "note", "title": "C", "dated": "2026-09-01", "text": "older"}]
    build(tmp_path, series_dir, cfg=cfg)
    doc = cases.load_case(tmp_path / "cases", CASE_ID).documents[-1]
    assert (doc.kind, doc.title, doc.text) == ("note", "B (2026-10-01)", "second")


def test_padded_dates_in_documents(tmp_path, series_dir):
    cfg = config()
    cfg["documents"] = [{"kind": "note", "title": "A", "dated": " 2026-10-30 ", "text": "on the day"}]
    build(tmp_path, series_dir, cfg=cfg)
    assert cases.load_case(tmp_path / "cases", CASE_ID).documents[-1].title == "A (2026-10-30)"


def test_only_the_dashboard_when_the_rest_is_empty(tmp_path, series_dir):
    cfg = config()
    for key in ("context", "positioning", "documents", "calendar"):
        cfg[key] = []
    assert build(tmp_path, series_dir, cfg=cfg)["documents"] == ["dashboard"]


def test_missing_context_and_positioning_files_give_no_observations(tmp_path, series_dir):
    cfg = config()
    cfg["context"].append({"id": "CPIAUCSL", "name": "CPI", "unit": "index"})
    cfg["positioning"].append({"id": "ES", "name": "E-mini", "net": "ES_net",
                               "open_interest": None, "release_lag_days": 3})
    build(tmp_path, series_dir, cfg=cfg)
    docs = cases.load_case(tmp_path / "cases", CASE_ID).documents
    assert "| CPI (CPIAUCSL, index) | n/a | n/a | n/a | n/a | n/a | current values |" in docs[1].text
    assert "| E-mini (ES) | n/a | n/a | n/a | n/a | n/a |" in docs[2].text


def test_the_calendar_runs_to_the_longest_horizon(tmp_path, series_dir):
    cfg = config()
    cfg["horizons"] = ["1m", "1w"]
    build(tmp_path, series_dir, cfg=cfg)
    text = cases.load_case(tmp_path / "cases", CASE_ID).documents[3].text
    assert text.startswith("Scheduled after 2026-10-30, through 2026-11-30:")


@pytest.mark.parametrize("as_of", ["2026-10-32", "soon", 20261030, None])
def test_bad_as_of(tmp_path, series_dir, as_of):
    with pytest.raises(ValueError):
        build(tmp_path, series_dir, as_of=as_of)


# --- load_macro_config ----------------------------------------------------------------------------

def put_config(tmp_path, data, name="macro-config.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_macro_config_fills_defaults(tmp_path):
    raw = {"case_prefix": "m_1-x", "horizons": ["1w"],
           "targets": [{"id": "DGS10", "name": "10y", "unit": "percent", "kind": "bp"}]}
    cfg = snapshot.load_macro_config(put_config(tmp_path, raw))
    assert cfg["targets"][0]["optional"] is False
    for key in ("context", "positioning", "documents", "calendar"):
        assert cfg[key] == []
    raw["positioning"] = [{"id": "TY", "name": "TY", "net": "TY_net", "release_lag_days": 0}]
    cfg = snapshot.load_macro_config(put_config(tmp_path, raw))
    assert cfg["positioning"][0]["open_interest"] is None


def test_load_macro_config_full(tmp_path):
    cfg = snapshot.load_macro_config(put_config(tmp_path, config()))
    for key in ("case_prefix", "horizons", "context", "documents", "calendar"):
        assert cfg[key] == config()[key]
    assert [t["optional"] for t in cfg["targets"]] == [False, False, True]


def test_load_macro_config_byte_order_mark(tmp_path):
    path = tmp_path / "c.json"
    path.write_bytes(codecs.BOM_UTF8 + json.dumps(config()).encode("utf-8"))
    assert snapshot.load_macro_config(path)["case_prefix"] == "macro"


def test_load_macro_config_missing_file(tmp_path):
    with pytest.raises(OSError):
        snapshot.load_macro_config(tmp_path / "none.json")


def broken(change):
    cfg = config()
    change(cfg)
    return cfg


BROKEN = {
    "not an object": lambda c: c.clear() or c.update({"x": [1]}) or None,
    "prefix with a space": lambda c: c.update(case_prefix="macro 1"),
    "empty prefix": lambda c: c.update(case_prefix=""),
    "prefix not a str": lambda c: c.update(case_prefix=5),
    "no prefix": lambda c: c.pop("case_prefix"),
    "no horizons": lambda c: c.update(horizons=[]),
    "repeated horizon": lambda c: c.update(horizons=["1w", "1w"]),
    "padded horizon": lambda c: c.update(horizons=[" 1w"]),
    "horizons a str": lambda c: c.update(horizons="1w"),
    "bad horizon": lambda c: c.update(horizons=["1W"]),
    "no targets": lambda c: c.update(targets=[]),
    "target not an object": lambda c: c["targets"].append("SP500"),
    "bad target id": lambda c: c["targets"][0].update(id="DGS-10"),
    "repeated target id": lambda c: c["targets"][1].update(id="DGS10"),
    "target name": lambda c: c["targets"][0].update(name=None),
    "target unit": lambda c: c["targets"][0].pop("unit"),
    "target kind": lambda c: c["targets"][0].update(kind="pct"),
    "target optional": lambda c: c["targets"][2].update(optional="yes"),
    "context not a list": lambda c: c.update(context={"id": "UNRATE"}),
    "context id": lambda c: c["context"][0].update(id="UN RATE"),
    "context name": lambda c: c["context"][0].pop("name"),
    "context entry": lambda c: c["context"].append(3),
    "negative lag": lambda c: c["positioning"][0].update(release_lag_days=-1),
    "bool lag": lambda c: c["positioning"][0].update(release_lag_days=True),
    "str lag": lambda c: c["positioning"][0].update(release_lag_days="3"),
    "no lag": lambda c: c["positioning"][0].pop("release_lag_days"),
    "net id": lambda c: c["positioning"][0].update(net="TY net"),
    "open interest id": lambda c: c["positioning"][0].update(open_interest=""),
    "positioning name": lambda c: c["positioning"][0].pop("name"),
    "url and text": lambda c: c["documents"][0].update(text="both"),
    "neither url nor text": lambda c: c["documents"][3].pop("text"),
    "bad dated": lambda c: c["documents"][0].update(dated="Sept 16"),
    "empty kind": lambda c: c["documents"][0].update(kind=""),
    "no title": lambda c: c["documents"][0].pop("title"),
    "url not a str": lambda c: c["documents"][0].update(url=None),
    "documents not a list": lambda c: c.update(documents="none"),
    "calendar date": lambda c: c["calendar"][0].update(date="Nov 4"),
    "calendar event": lambda c: c["calendar"][0].pop("event"),
}


@pytest.mark.parametrize("problem", sorted(BROKEN))
def test_load_macro_config_refuses(tmp_path, problem):
    data = broken(BROKEN[problem]) if problem != "not an object" else [config()]
    path = put_config(tmp_path, data)
    with pytest.raises(ValueError, match="macro-config.json"):
        snapshot.load_macro_config(path)


# --- CLI ------------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body, ctype):
        self.body, self.headers = body, {"Content-Type": ctype}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(monkeypatch, served):
    calls = []

    def urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_header("User-agent")))
        if request.full_url not in served:
            raise urllib.error.URLError("no route")
        return FakeResponse(served[request.full_url].encode("utf-8"), "text/html; charset=utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def no_client():
    raise AssertionError("snapshot never builds a client")


def run(argv, capsys):
    code = main(argv, client_factory=no_client)
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_snapshot(tmp_path, series_dir, monkeypatch, capsys):
    monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    calls = serve(monkeypatch, {"https://fed.example/1028":
                                "<html><head><title>T</title></head><body><p>Lower the range.</p>"
                                "</body></html>"})
    path = put_config(tmp_path, config())
    code, out, err = run(["snapshot", "--root", str(tmp_path / "cases"), "--config", str(path),
                          "--as-of", "2026-10-30", "--series-dir", str(series_dir)], capsys)
    assert code == 0, err
    assert json.loads(out)["case_id"] == CASE_ID
    assert calls == [("https://fed.example/1028", "ResearchOS/0.1 (research use)")]
    doc = cases.load_case(tmp_path / "cases", CASE_ID).documents[4]
    assert doc.text == "Lower the range." and doc.source == "https://fed.example/1028"


def test_cli_snapshot_out_and_user_agent(tmp_path, series_dir, monkeypatch, capsys):
    calls = serve(monkeypatch, {"https://fed.example/1028": "<p>x</p>"})
    out_file = tmp_path / "summary.json"
    code, out, _ = run(["snapshot", "--root", str(tmp_path / "cases"), "--config",
                        str(put_config(tmp_path, config())), "--as-of", "2026-10-30",
                        "--series-dir", str(series_dir), "--user-agent", "me@example.com",
                        "--out", str(out_file)], capsys)
    assert code == 0 and out == ""
    assert json.loads(out_file.read_text(encoding="utf-8"))["left_out"] == ["NASDAQQGLDI"]
    assert calls[0][1] == "me@example.com"


def test_cli_snapshot_dead_link_still_builds(tmp_path, series_dir, monkeypatch, capsys):
    serve(monkeypatch, {})
    code, out, _ = run(["snapshot", "--root", str(tmp_path / "cases"), "--config",
                        str(put_config(tmp_path, config())), "--as-of", "2026-10-30",
                        "--series-dir", str(series_dir)], capsys)
    assert code == 0
    assert "statement" not in json.loads(out)["documents"]


@pytest.mark.parametrize("argv", [
    ["--as-of", "2026-13-01"], ["--as-of", "soon"], [],
])
def test_cli_snapshot_bad_arguments(tmp_path, series_dir, capsys, argv):
    base = ["snapshot", "--root", str(tmp_path / "cases"), "--config",
            str(put_config(tmp_path, config())), "--series-dir", str(series_dir)]
    code, _, err = run(base + argv, capsys)
    assert code == 2 and err


def test_cli_snapshot_missing_option(tmp_path, capsys):
    code, _, _ = run(["snapshot", "--root", str(tmp_path), "--as-of", "2026-10-30",
                      "--series-dir", str(tmp_path)], capsys)
    assert code == 2


def test_cli_snapshot_failures_exit_1(tmp_path, series_dir, monkeypatch, capsys):
    serve(monkeypatch, {})
    base = ["snapshot", "--root", str(tmp_path / "cases"), "--as-of", "2026-10-30",
            "--series-dir", str(series_dir)]
    code, out, err = run(base + ["--config", str(tmp_path / "none.json")], capsys)
    assert code == 1 and out == "" and err.strip()
    code, _, err = run(base + ["--config", str(put_config(tmp_path, broken(
        BROKEN["target kind"])))], capsys)
    assert code == 1 and err.strip()
    (series_dir / "DGS10.json").unlink()
    code, _, err = run(base + ["--config", str(put_config(tmp_path, config()))], capsys)
    assert code == 1 and "DGS10" in err
    assert not (tmp_path / "cases" / CASE_ID).exists()


def test_cli_snapshot_refuses_a_sealed_case(tmp_path, series_dir, monkeypatch, capsys):
    serve(monkeypatch, {})
    (tmp_path / "cases" / CASE_ID).mkdir(parents=True)
    (tmp_path / "cases" / CASE_ID / "prediction.json").write_text("{}", encoding="utf-8")
    code, _, err = run(["snapshot", "--root", str(tmp_path / "cases"), "--config",
                        str(put_config(tmp_path, config())), "--as-of", "2026-10-30",
                        "--series-dir", str(series_dir)], capsys)
    assert code == 1 and CASE_ID in err


def test_snapshot_module_rules():
    text = Path(snapshot.__file__).read_text(encoding="utf-8")
    text.encode("ascii")
    for word in ("anthropic", "fromisoformat", "isfinite"):
        assert word not in text, word
