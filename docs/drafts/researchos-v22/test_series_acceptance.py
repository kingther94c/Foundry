"""Acceptance tests for series from free sources (v22), written from SPEC.md independently of the
code (held back from the agent that built it).

Each case traces to the "Series from free sources (v22)" section of SPEC.md. Nothing touches the
network: a transport is injected, or urllib.request.urlopen is replaced. The FRED, ALFRED and
CFTC samples are cut from what those sites returned on 2026-10-31.
"""

from __future__ import annotations

import codecs
import json
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pytest

from researchos import series
from researchos.__main__ import main
from researchos.sources import load_closes

NL = chr(10)
CRLF = chr(13) + chr(10)

DGS10_LINES = ["observation_date,DGS10", "2026-10-01,5.24", "2026-10-02,5.28", "2026-10-05,5.31",
               "2026-10-06,5.27", "2026-10-07,5.28", "2026-10-08,5.22", "2026-10-09,5.24",
               "2026-10-12,", "2026-10-13,5.21", "2026-10-14,5.19", "2026-10-15,5.10",
               "2026-10-16,5.09", "2026-10-19,5.15", "2026-10-20,5.12", "2026-10-21,5.13",
               "2026-10-22,4.96", "2026-10-23,5.00", "2026-10-26,5.00", "2026-10-27,4.99",
               "2026-10-28,5.05", "2026-10-29,4.98"]
DGS10 = NL.join(DGS10_LINES) + NL
UNRATE_VINTAGE = NL.join(["observation_date,UNRATE_20261030", "2026-07-01,4.1", "2026-08-01,4.1",
                          "2026-09-01,4.2"]) + NL

CFTC_ROWS = [
    {"market_and_exchange_names": "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
     "report_date_as_yyyy_mm_dd": "2026-10-27T00:00:00.000", "cftc_contract_market_code": "043602",
     "cftc_market_code": "CBT ", "open_interest_all": "5881588",
     "noncomm_positions_long_all": "668230", "noncomm_positions_short_all": "1649768",
     "noncomm_positions_spread": "144442", "change_in_noncomm_long_all": "-53307"},
    {"market_and_exchange_names": "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
     "report_date_as_yyyy_mm_dd": "2026-10-20T00:00:00.000", "cftc_contract_market_code": "043602",
     "cftc_market_code": "CBT ", "open_interest_all": "5868295",
     "noncomm_positions_long_all": "721537", "noncomm_positions_short_all": "1685830",
     "noncomm_positions_spread": "133237", "change_in_noncomm_long_all": "12035"},
]

UA = "ResearchOS acceptance test"


class Transport:
    def __init__(self, served):
        self.served = served
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        body, ctype = self.served[url]
        if isinstance(body, Exception):
            raise body
        return body, ctype


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
    """Replace urllib.request.urlopen; record (url, user agent, timeout) per call."""
    calls = []

    def urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_header("User-agent"), timeout))
        if request.full_url not in served:
            raise urllib.error.URLError("no route to " + request.full_url)
        body, ctype = served[request.full_url]
        return FakeResponse(body, ctype)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


# --- addresses ----------------------------------------------------------------------------------

def test_constants():
    assert series.FRED_CSV == "https://fred.stlouisfed.org/graph/fredgraph.csv"
    assert series.ALFRED_CSV == "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
    assert series.CFTC_LEGACY == "https://publicreporting.cftc.gov/resource/6dca-aqww.json"


def test_fred_url():
    base = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    alfred = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
    assert series.fred_url("DGS10") == f"{base}?id=DGS10"
    assert series.fred_url("DGS10", start="2026-01-01") == f"{base}?id=DGS10&cosd=2026-01-01"
    assert series.fred_url("DGS10", start=date(2026, 1, 1)) == f"{base}?id=DGS10&cosd=2026-01-01"
    assert series.fred_url("DGS10", start=" 2026-01-01 ") == f"{base}?id=DGS10&cosd=2026-01-01"
    assert series.fred_url("PAYEMS", vintage="2026-08-01") == \
        f"{alfred}?id=PAYEMS&vintage_date=2026-08-01"
    assert series.fred_url("PAYEMS", start=date(2026, 1, 1), vintage=date(2026, 8, 1)) == \
        f"{alfred}?id=PAYEMS&vintage_date=2026-08-01&cosd=2026-01-01"
    assert series.fred_url("BAMLH0A0HYM2", start=datetime(2026, 10, 1, 15, 30)) == \
        f"{base}?id=BAMLH0A0HYM2&cosd=2026-10-01"
    assert series.fred_url("T10Y2Y", start=None, vintage=None) == f"{base}?id=T10Y2Y"


@pytest.mark.parametrize("sid", ["", "DGS 10", "DGS10&x=1", "../DGS10", "DGS-10", "DGS10" + NL,
                                 chr(0xC5), None, 10, b"DGS10"])
def test_fred_url_rejects_ids(sid):
    with pytest.raises(ValueError):
        series.fred_url(sid)


@pytest.mark.parametrize("bad", ["2026-13-01", "yesterday", "", 20261001, 2026.1, True])
def test_fred_url_rejects_dates(bad):
    with pytest.raises(ValueError):
        series.fred_url("DGS10", start=bad)
    with pytest.raises(ValueError):
        series.fred_url("DGS10", vintage=bad)


def test_cftc_url():
    head = ("https://publicreporting.cftc.gov/resource/6dca-aqww.json?cftc_contract_market_code=043602"
            "&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=")
    assert series.cftc_url("043602") == head + "600"
    assert series.cftc_url("043602", limit=1) == head + "1"
    assert series.cftc_url("043602", limit=50000) == head + "50000"
    assert series.cftc_url("13874A").startswith(series.CFTC_LEGACY + "?cftc_contract_market_code=13874A&")
    assert "code=1170E1&" in series.cftc_url("1170E1")


@pytest.mark.parametrize("limit", [0, -1, 50001, True, 1.0, "600", None])
def test_cftc_url_rejects_limits(limit):
    with pytest.raises(ValueError):
        series.cftc_url("043602", limit=limit)


@pytest.mark.parametrize("code", ["43602", "0436021", "13874a", "043 602", "", None, 43602,
                                  "043602" + NL])
def test_cftc_url_rejects_codes(code):
    with pytest.raises(ValueError):
        series.cftc_url(code)


# --- FRED CSV -------------------------------------------------------------------------------------

def test_parse_fred_csv_real_sample():
    obs = series.parse_fred_csv(DGS10, "DGS10")
    assert len(obs) == 20
    assert obs[0] == (date(2026, 10, 1), 5.24) and obs[-1] == (date(2026, 10, 29), 4.98)
    assert date(2026, 10, 12) not in [d for d, _ in obs]  # Columbus Day: no value
    assert all(type(d) is date and type(v) is float for d, v in obs)
    assert [d for d, _ in obs] == sorted(d for d, _ in obs)


def test_parse_fred_csv_line_endings_and_blank_lines():
    text = CRLF.join(DGS10_LINES[:4]) + CRLF + CRLF + "  " + CRLF + DGS10_LINES[4] + CRLF
    assert series.parse_fred_csv(text, "DGS10") == series.parse_fred_csv(
        NL.join(DGS10_LINES[:5]), "DGS10")
    assert series.parse_fred_csv(NL + NL + DGS10_LINES[0] + NL + "2026-10-01,5.24", "DGS10") == \
        [(date(2026, 10, 1), 5.24)]


def test_parse_fred_csv_vintage_column():
    assert series.parse_fred_csv(UNRATE_VINTAGE, "UNRATE") == [
        (date(2026, 7, 1), 4.1), (date(2026, 8, 1), 4.1), (date(2026, 9, 1), 4.2)]


@pytest.mark.parametrize("header,sid", [
    ("DATE,DGS10", "DGS10"), (" observation_date , DGS10 ", "DGS10"),
    ("observation_date,DGS10_20260801", "DGS10"), ("observation_date,A_B_20260801", "A_B"),
])
def test_parse_fred_csv_accepts_headers(header, sid):
    assert series.parse_fred_csv(header + NL + "2026-10-01,1.5", sid) == [(date(2026, 10, 1), 1.5)]


@pytest.mark.parametrize("header,sid", [
    ("date,DGS10", "DGS10"), ("observation_date,DGS2", "DGS10"), ("observation_date,DGS10", "DGS1"),
    ("observation_date,UNRATE_2026103", "UNRATE"), ("observation_date,UNRATE_x0261030", "UNRATE"),
    ("observation_date,UNRATE_202610301", "UNRATE"), ("observation_date,DGS10,DGS2", "DGS10"),
    ("observation_date", "DGS10"), ("<!DOCTYPE html><html><body>Error</body></html>", "DGS10"),
    ("observation_date;DGS10", "DGS10"), ("OBSERVATION_DATE,DGS10", "DGS10"),
])
def test_parse_fred_csv_rejects_headers_naming_the_series(header, sid):
    with pytest.raises(ValueError, match=sid):
        series.parse_fred_csv(header + NL + "2026-10-01,1.5", sid)


@pytest.mark.parametrize("text", ["", NL, "   " + NL + NL])
def test_parse_fred_csv_needs_a_header(text):
    with pytest.raises(ValueError, match="DGS10"):
        series.parse_fred_csv(text, "DGS10")


def test_parse_fred_csv_header_only_is_empty():
    assert series.parse_fred_csv("observation_date,DGS10" + NL, "DGS10") == []


@pytest.mark.parametrize("value,expected", [
    ("5.24", 5.24), ("-0.35", -0.35), ("4", 4.0), (" 4.21 ", 4.21), ("0.0", 0.0), ("007", 7.0),
])
def test_parse_fred_csv_values(value, expected):
    obs = series.parse_fred_csv("observation_date,X" + NL + "2026-10-01," + value, "X")
    assert obs == [(date(2026, 10, 1), expected)] and type(obs[0][1]) is float


@pytest.mark.parametrize("value", ["", ".", " ", " . "])
def test_parse_fred_csv_missing_values(value):
    text = "observation_date,X" + NL + "2026-10-01," + value + NL + "2026-10-02,1.0"
    assert series.parse_fred_csv(text, "X") == [(date(2026, 10, 2), 1.0)]


@pytest.mark.parametrize("line", [
    "2026-10-01,1e3", "2026-10-01,4.2.1", "2026-10-01,+4.2", "2026-10-01,.5", "2026-10-01,5.",
    "2026-10-01,NaN", "2026-10-01,inf", "2026-10-01," + "9" * 400, "2026-10-01,1_000",
    "2026-10-01,1,000", "2026-10-01", "2026-10-32,1.0", "Oct 1 2026,1.0", ",1.0", "2026-10-01,--1",
    "2026-10-01,4,", "2026-10-01,#N/A",
])
def test_parse_fred_csv_rejects_lines_naming_the_series(line):
    with pytest.raises(ValueError, match="DGS10"):
        series.parse_fred_csv("observation_date,DGS10" + NL + "2026-10-02,1.0" + NL + line, "DGS10")


def test_parse_fred_csv_sorts_and_the_later_line_wins():
    text = NL.join(["observation_date,X", "2026-10-05,3", "2026-10-01,1", "2026-10-05,30",
                    "2026-10-03,2", "2026-10-03,", "2026-10-02,", "2026-10-02,20"])
    assert series.parse_fred_csv(text, "X") == [
        (date(2026, 10, 1), 1.0), (date(2026, 10, 2), 20.0), (date(2026, 10, 5), 30.0)]


def test_parse_fred_csv_padded_dates():
    obs = series.parse_fred_csv("observation_date,X" + NL + " 2026-10-01 ,1.5", "X")
    assert obs == [(date(2026, 10, 1), 1.5)]


def test_parse_fred_csv_checks_the_id():
    with pytest.raises(ValueError):
        series.parse_fred_csv("observation_date,DGS 10" + NL, "DGS 10")


# --- CFTC ----------------------------------------------------------------------------------------

def test_parse_cftc_real_rows():
    net, oi = series.parse_cftc(json.loads(json.dumps(CFTC_ROWS)), "043602")
    assert net == [(date(2026, 10, 20), 721537.0 - 1685830.0), (date(2026, 10, 27), -981538.0)]
    assert oi == [(date(2026, 10, 20), 5868295.0), (date(2026, 10, 27), 5881588.0)]
    assert all(type(v) is float for _, v in net + oi)


def test_parse_cftc_empty():
    assert series.parse_cftc([], "043602") == ([], [])


def row(**changes):
    r = dict(CFTC_ROWS[0])
    for k, v in changes.items():
        if v is KeyError:
            del r[k]
        else:
            r[k] = v
    return r


@pytest.mark.parametrize("long_,short,oi,net,total", [
    (10, 4, 100, 6.0, 100.0), (" 10 ", "4", "100", 6.0, 100.0), ("-5", "0", "7", -5.0, 7.0),
    (0, 0, 0, 0.0, 0.0),
])
def test_parse_cftc_counts(long_, short, oi, net, total):
    r = row(noncomm_positions_long_all=long_, noncomm_positions_short_all=short,
            open_interest_all=oi)
    assert series.parse_cftc([r], "043602") == ([(date(2026, 10, 27), net)],
                                                [(date(2026, 10, 27), total)])


@pytest.mark.parametrize("field", ["noncomm_positions_long_all", "noncomm_positions_short_all",
                                   "open_interest_all"])
@pytest.mark.parametrize("value", ["1.5", "", "1e3", True, None, 1.0, "12 3", KeyError, [1]])
def test_parse_cftc_rejects_counts(field, value):
    with pytest.raises(ValueError, match="043602"):
        series.parse_cftc([CFTC_ROWS[1], row(**{field: value})], "043602")


@pytest.mark.parametrize("day,expected", [("2026-10-27T00:00:00.000", date(2026, 10, 27)),
                                          ("2026-10-27", date(2026, 10, 27)),
                                          (" 2026-10-27 ", date(2026, 10, 27)),
                                          ("2026-10-27Tanything", date(2026, 10, 27))])
def test_parse_cftc_dates(day, expected):
    net, _ = series.parse_cftc([row(report_date_as_yyyy_mm_dd=day)], "043602")
    assert net[0][0] == expected


@pytest.mark.parametrize("day", ["10/27/2026", "", None, 20261027, KeyError, "2026-10-32T00:00"])
def test_parse_cftc_rejects_dates(day):
    with pytest.raises(ValueError, match="043602"):
        series.parse_cftc([row(report_date_as_yyyy_mm_dd=day)], "043602")


def test_parse_cftc_codes():
    assert series.parse_cftc([row(cftc_contract_market_code=" 043602 ")], "043602")[0]
    for code in ("042601", "", None, KeyError, 43602):
        with pytest.raises(ValueError, match="043602"):
            series.parse_cftc([CFTC_ROWS[0], row(cftc_contract_market_code=code)], "043602")


def test_parse_cftc_rejects_payloads():
    with pytest.raises(ValueError, match="Unrecognized arguments"):
        series.parse_cftc({"code": "query.soql.no-such-column", "error": True,
                           "message": "Unrecognized arguments [foo]"}, "043602")
    for payload in ({}, None, "x", 3, {"data": CFTC_ROWS}):
        with pytest.raises(ValueError):
            series.parse_cftc(payload, "043602")
    with pytest.raises(ValueError):
        series.parse_cftc([CFTC_ROWS[0], ["not", "a", "row"]], "043602")
    with pytest.raises(ValueError):
        series.parse_cftc(CFTC_ROWS, "43602")


def test_parse_cftc_later_row_wins():
    a = row(noncomm_positions_long_all="1", noncomm_positions_short_all="0", open_interest_all="9")
    b = row(noncomm_positions_long_all="5", noncomm_positions_short_all="1", open_interest_all="8")
    assert series.parse_cftc([a, CFTC_ROWS[1], b], "043602") == (
        [(date(2026, 10, 20), -964293.0), (date(2026, 10, 27), 4.0)],
        [(date(2026, 10, 20), 5868295.0), (date(2026, 10, 27), 8.0)])


# --- files ----------------------------------------------------------------------------------------

def test_save_series_writes_what_load_closes_reads(tmp_path):
    obs = series.parse_fred_csv(DGS10, "DGS10")
    path = tmp_path / "a" / "b" / "DGS10.json"
    series.save_series(path, reversed(obs))
    expected = [[d.isoformat(), v] for d, v in obs]
    assert path.read_bytes() == (json.dumps(expected, indent=2, ensure_ascii=False) + NL).encode()
    assert load_closes(path) == obs


def test_save_series_later_pair_wins(tmp_path):
    path = tmp_path / "x.json"
    series.save_series(path, [(date(2026, 1, 2), 2.0), (date(2026, 1, 1), 1.0),
                              (date(2026, 1, 2), 3.5)])
    assert load_closes(path) == [(date(2026, 1, 1), 1.0), (date(2026, 1, 2), 3.5)]


def test_save_series_empty(tmp_path):
    series.save_series(tmp_path / "x.json", [])
    assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("pair", [(datetime(2026, 1, 1, 12), 1.0), ("2026-01-01", 1.0),
                                  (date(2026, 1, 1), True), (date(2026, 1, 1), float("nan")),
                                  (date(2026, 1, 1), "4.2"), (date(2026, 1, 1), None),
                                  (date(2026, 1, 1),), None, (date(2026, 1, 1), 1.0, 2.0)])
def test_save_series_rejects_pairs_and_leaves_the_file(tmp_path, pair):
    path = tmp_path / "x.json"
    path.write_bytes(b"[]")
    with pytest.raises(ValueError):
        series.save_series(path, [(date(2026, 1, 1), 1.0), pair])
    assert path.read_bytes() == b"[]"


def put(path, pairs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([[d, v] for d, v in pairs]), encoding="utf-8")


def test_load_series_current_file(tmp_path):
    put(tmp_path / "DGS10.json", [("2026-10-02", 5.28), ("2026-10-01", 5.24)])
    assert series.load_series(tmp_path, "DGS10") == [(date(2026, 10, 1), 5.24),
                                                     (date(2026, 10, 2), 5.28)]


def test_load_series_never_returns_a_later_value(tmp_path):
    put(tmp_path / "DGS10.json", [("2026-10-29", 4.98), ("2026-10-30", 5.0), ("2026-11-02", 9.9)])
    assert series.load_series(tmp_path, "DGS10", "2026-10-30") == [(date(2026, 10, 29), 4.98),
                                                                   (date(2026, 10, 30), 5.0)]
    assert series.load_series(tmp_path, "DGS10", date(2026, 10, 29)) == [(date(2026, 10, 29), 4.98)]
    assert series.load_series(tmp_path, "DGS10", " 2026-10-01 ") == []


def test_load_series_prefers_the_vintage_of_that_date(tmp_path):
    put(tmp_path / "PAYEMS.json", [("2026-07-01", 159015.0), ("2026-08-01", 159044.0)])
    put(tmp_path / "PAYEMS@2026-08-01.json", [("2026-06-01", 158984.0), ("2026-07-01", 158900.0),
                                              ("2026-09-01", 1.0)])
    put(tmp_path / "PAYEMS@2026-09-05.json", [("2026-07-01", 158950.0)])
    assert series.load_series(tmp_path, "PAYEMS", "2026-08-01") == [(date(2026, 6, 1), 158984.0),
                                                                    (date(2026, 7, 1), 158900.0)]
    assert series.load_series(tmp_path, "PAYEMS", date(2026, 9, 5)) == [(date(2026, 7, 1), 158950.0)]
    # no vintage file for this date: the current file, cut at the date
    assert series.load_series(tmp_path, "PAYEMS", "2026-07-15") == [(date(2026, 7, 1), 159015.0)]
    assert series.load_series(tmp_path, "PAYEMS") == [(date(2026, 7, 1), 159015.0),
                                                      (date(2026, 8, 1), 159044.0)]


def test_load_series_errors(tmp_path):
    with pytest.raises(OSError):
        series.load_series(tmp_path, "DGS10")
    with pytest.raises(OSError):
        series.load_series(tmp_path, "DGS10", "2026-10-30")
    with pytest.raises(ValueError):
        series.load_series(tmp_path, "../DGS10")
    put(tmp_path / "DGS10.json", [("2026-10-01", 1.0)])
    with pytest.raises(ValueError):
        series.load_series(tmp_path, "DGS10", "2026-02-30")
    with pytest.raises(ValueError):
        series.load_series(tmp_path, "DGS10", 20261030)
    (tmp_path / "BAD.json").write_text('[["2026-10-01", "5.24"]]', encoding="utf-8")
    with pytest.raises(ValueError):
        series.load_series(tmp_path, "BAD")


# --- fetching ------------------------------------------------------------------------------------

def test_fetch_text_sends_the_user_agent():
    t = Transport({"https://x.example/a.csv": (b"a,b", "application/csv")})
    assert series.fetch_text("https://x.example/a.csv", user_agent=UA, transport=t) == "a,b"
    assert t.calls == [("https://x.example/a.csv", {"User-Agent": UA})]


CAFE = "caf" + chr(0xE9)


@pytest.mark.parametrize("body,ctype,text", [
    (CAFE.encode("latin-1"), "text/csv; charset=ISO-8859-1", CAFE),
    (CAFE.encode("utf-8"), "application/csv", CAFE),
    (CAFE.encode("utf-8"), "", CAFE),
    (CAFE.encode("utf-8"), "text/csv; charset=no-such-charset", CAFE),
    (codecs.BOM_UTF8 + b"observation_date,X", "text/csv", "observation_date,X"),
    (b"[]", "application/json;charset=utf-8", "[]"),
    (b"%PDF", "application/pdf", "%PDF"),
])
def test_fetch_text_decodes(body, ctype, text):
    t = Transport({"u": (body, ctype)})
    assert series.fetch_text("u", user_agent=UA, transport=t) == text


def test_fetch_text_errors_propagate():
    t = Transport({"u": (OSError("down"), "")})
    with pytest.raises(OSError):
        series.fetch_text("u", user_agent=UA, transport=t)


def test_fetch_text_default_transport(monkeypatch):
    calls = serve(monkeypatch, {"https://x.example/s.csv": (b"observation_date,X", "text/csv")})
    assert series.fetch_text("https://x.example/s.csv", user_agent=UA) == "observation_date,X"
    assert calls == [("https://x.example/s.csv", UA, 30)]
    with pytest.raises(OSError):
        series.fetch_text("https://x.example/missing", user_agent=UA)


def test_fetch_fred():
    url = series.fred_url("DGS10", start="2026-10-01")
    t = Transport({url: (DGS10.encode(), "application/csv")})
    obs = series.fetch_fred("DGS10", user_agent=UA, start="2026-10-01", transport=t)
    assert obs == series.parse_fred_csv(DGS10, "DGS10")
    assert t.calls == [(url, {"User-Agent": UA})]


def test_fetch_fred_vintage():
    url = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=UNRATE&vintage_date=2026-10-30"
    t = Transport({url: (UNRATE_VINTAGE.encode(), "application/csv")})
    assert series.fetch_fred("UNRATE", user_agent=UA, vintage=date(2026, 10, 30), transport=t)[-1] \
        == (date(2026, 9, 1), 4.2)


def test_fetch_fred_checks_before_fetching():
    t = Transport({})
    for kwargs in ({"series_id": "DGS 10"}, {"series_id": "DGS10", "start": "soon"},
                   {"series_id": "DGS10", "vintage": 7}):
        with pytest.raises(ValueError):
            series.fetch_fred(user_agent=UA, transport=t, **kwargs)
    assert t.calls == []


def test_fetch_fred_rejects_an_error_page():
    url = series.fred_url("DGS10")
    t = Transport({url: (b"<html><body>Series does not exist.</body></html>", "text/html")})
    with pytest.raises(ValueError, match="DGS10"):
        series.fetch_fred("DGS10", user_agent=UA, transport=t)


def test_fetch_cftc():
    url = series.cftc_url("043602", limit=2)
    t = Transport({url: (json.dumps(CFTC_ROWS).encode(), "application/json;charset=utf-8")})
    net, oi = series.fetch_cftc("043602", user_agent=UA, limit=2, transport=t)
    assert net[-1] == (date(2026, 10, 27), -981538.0) and oi[-1] == (date(2026, 10, 27), 5881588.0)
    assert t.calls == [(url, {"User-Agent": UA})]
    t = Transport({series.cftc_url("043602"): (b"[]", "application/json")})
    assert series.fetch_cftc("043602", user_agent=UA, transport=t) == ([], [])


def test_fetch_cftc_errors():
    url = series.cftc_url("043602")
    t = Transport({url: (b"<html>busy</html>", "text/html")})
    with pytest.raises(ValueError):
        series.fetch_cftc("043602", user_agent=UA, transport=t)
    t = Transport({url: (b'{"error": true, "message": "Too many requests"}', "application/json")})
    with pytest.raises(ValueError, match="Too many requests"):
        series.fetch_cftc("043602", user_agent=UA, transport=t)
    t = Transport({})
    with pytest.raises(ValueError):
        series.fetch_cftc("043602", user_agent=UA, limit=0, transport=t)
    with pytest.raises(ValueError):
        series.fetch_cftc("0436", user_agent=UA, transport=t)
    assert t.calls == []


# --- CLI -----------------------------------------------------------------------------------------

def no_client():
    raise AssertionError("fetch-series never builds a client")


def run(argv, capsys):
    code = main(argv, client_factory=no_client)
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_fred(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    url = series.fred_url("DGS10", start="2026-10-01")
    calls = serve(monkeypatch, {url: (DGS10.encode(), "application/csv")})
    f = tmp_path / "series" / "DGS10.json"
    code, out, err = run(["fetch-series", "--source", "fred", "--id", "DGS10", "--file", str(f),
                          "--start", "2026-10-01"], capsys)
    assert code == 0, err
    assert json.loads(out) == {"source": "fred", "id": "DGS10", "observations": 20,
                               "first": "2026-10-01", "last": "2026-10-29", "file": str(f)}
    assert load_closes(f) == series.parse_fred_csv(DGS10, "DGS10")
    assert calls == [(url, "ResearchOS/0.1 (research use)", 30)]


def test_cli_fred_vintage_and_out(tmp_path, monkeypatch, capsys):
    url = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=UNRATE&vintage_date=2026-10-30"
    calls = serve(monkeypatch, {url: (UNRATE_VINTAGE.encode(), "application/csv")})
    f = tmp_path / "UNRATE@2026-10-30.json"
    summary = tmp_path / "summary.json"
    code, out, err = run(["fetch-series", "--source", "fred", "--id", "UNRATE", "--file", str(f),
                          "--vintage", "2026-10-30", "--user-agent", "me@example.com",
                          "--out", str(summary)], capsys)
    assert code == 0, err
    assert out == ""
    assert json.loads(summary.read_text(encoding="utf-8"))["observations"] == 3
    assert calls[0][1] == "me@example.com"
    assert series.load_series(tmp_path, "UNRATE", "2026-10-30")[-1] == (date(2026, 9, 1), 4.2)


def test_cli_cftc(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RESEARCHOS_USER_AGENT", "from the environment")
    url = series.cftc_url("043602", limit=2)
    calls = serve(monkeypatch, {url: (json.dumps(CFTC_ROWS).encode(), "application/json")})
    f, oi = tmp_path / "TY_net.json", tmp_path / "TY_oi.json"
    code, out, err = run(["fetch-series", "--source", "cftc", "--id", "043602", "--file", str(f),
                          "--oi-file", str(oi), "--limit", "2"], capsys)
    assert code == 0, err
    assert json.loads(out) == {"source": "cftc", "id": "043602", "observations": 2,
                               "first": "2026-10-20", "last": "2026-10-27", "file": str(f),
                               "oi_file": str(oi)}
    assert load_closes(f)[-1] == (date(2026, 10, 27), -981538.0)
    assert load_closes(oi)[-1] == (date(2026, 10, 27), 5881588.0)
    assert calls == [(url, "from the environment", 30)]


def test_cli_cftc_without_open_interest(tmp_path, monkeypatch, capsys):
    serve(monkeypatch, {series.cftc_url("043602"): (json.dumps(CFTC_ROWS).encode(),
                                                    "application/json")})
    f = tmp_path / "TY_net.json"
    code, out, _ = run(["fetch-series", "--source", "cftc", "--id", "043602", "--file", str(f)],
                       capsys)
    assert code == 0
    assert json.loads(out)["oi_file"] is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["TY_net.json"]


def test_cli_no_observations(tmp_path, monkeypatch, capsys):
    serve(monkeypatch, {series.fred_url("DGS10"): (b"observation_date,DGS10" + NL.encode(),
                                                   "application/csv")})
    f = tmp_path / "DGS10.json"
    code, out, _ = run(["fetch-series", "--source", "fred", "--id", "DGS10", "--file", str(f)],
                       capsys)
    assert code == 0
    assert json.loads(out) == {"source": "fred", "id": "DGS10", "observations": 0, "first": None,
                               "last": None, "file": str(f)}
    assert json.loads(f.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("extra", [
    ["--source", "fred", "--id", "DGS10", "--start", "2026-13-01"],
    ["--source", "fred", "--id", "DGS10", "--vintage", "soon"],
    ["--source", "cftc", "--id", "043602", "--start", "2026-10-01"],
    ["--source", "cftc", "--id", "043602", "--vintage", "2026-10-01"],
    ["--source", "fred", "--id", "DGS10", "--limit", "5"],
    ["--source", "fred", "--id", "DGS10", "--oi-file", "oi.json"],
    ["--source", "cftc", "--id", "043602", "--limit", "0"],
    ["--source", "cftc", "--id", "043602", "--limit", "50001"],
    ["--source", "cftc", "--id", "043602", "--limit", "many"],
    ["--source", "stooq", "--id", "XAUUSD"],
    ["--id", "DGS10"],
    ["--source", "fred"],
])
def test_cli_bad_arguments_exit_2(tmp_path, monkeypatch, capsys, extra):
    calls = serve(monkeypatch, {})
    argv = ["fetch-series", *extra]
    if "--source" in extra and "--id" in extra:
        argv += ["--file", str(tmp_path / "f.json")]
    code, _, err = run(argv, capsys)
    assert code == 2
    assert err
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_cli_missing_file_option_exits_2(tmp_path, capsys):
    code, _, _ = run(["fetch-series", "--source", "fred", "--id", "DGS10"], capsys)
    assert code == 2


@pytest.mark.parametrize("served", [{}, {"html": True}, {"bad id": True}])
def test_cli_failures_exit_1_and_write_nothing(tmp_path, monkeypatch, capsys, served):
    sid = "DGS 10" if "bad id" in served else "DGS10"
    pages = {}
    if "html" in served:
        pages[series.fred_url("DGS10")] = (b"<html>Not found</html>", "text/html")
    serve(monkeypatch, pages)
    f = tmp_path / "DGS10.json"
    code, out, err = run(["fetch-series", "--source", "fred", "--id", sid, "--file", str(f)], capsys)
    assert code == 1
    assert out == "" and err.strip()
    assert not f.exists()


def test_cli_cftc_failure_writes_neither_file(tmp_path, monkeypatch, capsys):
    rows = [CFTC_ROWS[0], dict(CFTC_ROWS[1], open_interest_all="n/a")]
    serve(monkeypatch, {series.cftc_url("043602"): (json.dumps(rows).encode(), "application/json")})
    code, _, err = run(["fetch-series", "--source", "cftc", "--id", "043602", "--file",
                        str(tmp_path / "net.json"), "--oi-file", str(tmp_path / "oi.json")], capsys)
    assert code == 1 and err.strip()
    assert list(tmp_path.iterdir()) == []


def test_cli_bad_code_exits_1(tmp_path, monkeypatch, capsys):
    calls = serve(monkeypatch, {})
    code, _, _ = run(["fetch-series", "--source", "cftc", "--id", "TY", "--file",
                      str(tmp_path / "x.json")], capsys)
    assert code == 1
    assert calls == []


# --- the source -----------------------------------------------------------------------------------

def test_series_module_rules():
    text = Path(series.__file__).read_text(encoding="utf-8")
    text.encode("ascii")
    assert "anthropic" not in text
    assert "fromisoformat" not in text
    assert "parse_iso_date" in text
