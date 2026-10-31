"""Series from free sources (v22): researchos.series and the fetch-series command.

Nothing here touches the network: an autouse fixture makes urllib.request.urlopen fail,
and every test either passes its own transport or replaces urlopen with a fake.
"""

import http.client
import json
import urllib.error
import urllib.request
from datetime import date, datetime

import pytest

from test_fetch_by_address import FakeWeb, Site

from researchos.__main__ import main
from researchos.series import (
    ALFRED_CSV,
    CFTC_LEGACY,
    FRED_CSV,
    cftc_url,
    fetch_cftc,
    fetch_fred,
    fetch_text,
    fred_url,
    load_series,
    parse_cftc,
    parse_fred_csv,
    save_series,
)
from researchos.sources import load_closes

DEFAULT_UA = "ResearchOS/0.1 (research use)"
UA = "Acme Research ops@acme.example"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a real URL")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


# ---- addresses

def test_fred_url():
    assert FRED_CSV == "https://fred.stlouisfed.org/graph/fredgraph.csv"
    assert ALFRED_CSV == "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
    assert fred_url("DGS10") == "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
    assert fred_url("DGS10", start="2026-01-02") == (
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2026-01-02")
    assert fred_url("PAYEMS", vintage=date(2024, 5, 3)) == (
        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=PAYEMS&vintage_date=2024-05-03")
    # Dates in URLs are YYYY-MM-DD, whatever form the argument had.
    assert fred_url("PAYEMS", start=" 2020-01-01 ", vintage="20240503") == (
        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=PAYEMS"
        "&vintage_date=2024-05-03&cosd=2020-01-01")
    assert fred_url("cpi_U_2", start=date(2020, 1, 1)) == (
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=cpi_U_2&cosd=2020-01-01")


@pytest.mark.parametrize("series_id", ["", "DGS 10", "DGS-10", "DGS10\n", " DGS10", "DGS10;x",
                                       "DGS" + chr(0x0661), 10, None, b"DGS10"])
def test_fred_url_refuses_a_bad_series_id(series_id):
    with pytest.raises(ValueError):
        fred_url(series_id)


@pytest.mark.parametrize("kwargs", [
    {"start": "2026-1-2"}, {"start": "yesterday"}, {"start": ""}, {"start": 20260102},
    {"start": "2026-01-02T10:00"}, {"vintage": "2026-02-30"}, {"vintage": True},
    {"vintage": ["2026-01-02"]},
])
def test_fred_url_refuses_a_bad_date(kwargs):
    with pytest.raises(ValueError):
        fred_url("DGS10", **kwargs)


def test_cftc_url():
    assert CFTC_LEGACY == "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    assert cftc_url("043602") == (
        "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
        "?cftc_contract_market_code=043602&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=600")
    assert cftc_url("13874A", limit=1) == (
        "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
        "?cftc_contract_market_code=13874A&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=1")
    assert cftc_url("1170E1", limit=50000).endswith("=1170E1&$order=report_date_as_yyyy_mm_dd"
                                                    "%20DESC&$limit=50000")


@pytest.mark.parametrize("code", ["43602", "0436021", "13874a", " 043602", "043602 ", "04-602",
                                  "", 43602, None])
def test_cftc_url_refuses_a_bad_code(code):
    with pytest.raises(ValueError):
        cftc_url(code)


@pytest.mark.parametrize("limit", [0, -1, 50001, True, False, 5.0, "600", None])
def test_cftc_url_refuses_a_bad_limit(limit):
    with pytest.raises(ValueError):
        cftc_url("043602", limit=limit)


# ---- parse_fred_csv

def test_parse_fred_csv_reads_floats_sorted_by_date():
    text = ("observation_date,DGS10\n"
            "2026-10-28,4.05\n"
            "2026-10-26,4.01\n"
            "2026-10-27,\n"
            "2026-10-29,.\n"
            "2026-10-30,-0.25\n"
            "2026-10-31,4\n")
    series = parse_fred_csv(text, "DGS10")
    assert series == [(date(2026, 10, 26), 4.01), (date(2026, 10, 28), 4.05),
                      (date(2026, 10, 30), -0.25), (date(2026, 10, 31), 4.0)]
    assert all(type(value) is float for _, value in series)


def test_parse_fred_csv_headers():
    assert parse_fred_csv("DATE,DGS10\n2026-10-26,4.01", "DGS10") == [(date(2026, 10, 26), 4.01)]
    assert parse_fred_csv("observation_date,PAYEMS_20240503\n2024-04-01,158000\n", "PAYEMS") \
        == [(date(2024, 4, 1), 158000.0)]
    assert parse_fred_csv("observation_date,DGS10\n", "DGS10") == []


def test_parse_fred_csv_skips_blank_lines_and_strips_fields():
    text = ("\r\n   \n observation_date , DGS10 \r\n\n\t\n 2026-10-26 , 4.01 \r\n  \n"
            "2026-10-27,\t.\t\n\n")
    assert parse_fred_csv(text, "DGS10") == [(date(2026, 10, 26), 4.01)]


def test_parse_fred_csv_later_line_wins_even_without_a_value():
    text = ("observation_date,DGS10\n"
            "2026-10-26,4.01\n"
            "2026-10-27,4.02\n"
            "2026-10-26,4.11\n"
            "2026-10-27,.\n"
            "2026-10-28,\n"
            "2026-10-28,4.20\n")
    assert parse_fred_csv(text, "DGS10") == [(date(2026, 10, 26), 4.11),
                                             (date(2026, 10, 28), 4.2)]


@pytest.mark.parametrize("text", [
    "",
    "  \n\r\n\t",
    "<!DOCTYPE html>\n<html><body>Error</body></html>",
    "observation_date,DGS2\n2026-10-26,3.5",  # another series
    "date,DGS10\n2026-10-26,3.5",
    "observation_date,DGS10,DGS2\n2026-10-26,3.5,3.1",
    "observation_date\n2026-10-26",
    "observation_date;DGS10\n2026-10-26;3.5",
    "observation_date,DGS10_2024050\n",  # seven digits
    "observation_date,DGS10_202405031\n",
    "observation_date,DGS10_vintage\n",
    "observation_date,DGS10x_20240503\n",
    "2026-10-26,4.01\n",  # no header
])
def test_parse_fred_csv_refuses_a_bad_header(text):
    with pytest.raises(ValueError) as info:
        parse_fred_csv(text, "DGS10")
    assert "DGS10" in str(info.value)


@pytest.mark.parametrize("line", [
    "2026-10-26",
    "2026-10-26,4.01,4.02",
    "2026-10-26;4.01",
    "2026-10-26,1,000",
    "2026-13-01,4.01",
    "26/10/2026,4.01",
    ",4.01",
    "2026-10-26,abc",
    "2026-10-26,1e3",
    "2026-10-26,+1",
    "2026-10-26,1.",
    "2026-10-26,.5",
    "2026-10-26,- 1",
    "2026-10-26,--1",
    "2026-10-26,nan",
    "2026-10-26,inf",
    "2026-10-26,NA",
    "2026-10-26," + "9" * 400,  # float() of it is not finite
    "2026-10-26," + chr(0x0664),  # a digit, but not an ASCII one
])
def test_parse_fred_csv_refuses_a_bad_observation(line):
    with pytest.raises(ValueError) as info:
        parse_fred_csv("observation_date,DGS10\n2026-10-25,4.0\n" + line + "\n", "DGS10")
    assert "DGS10" in str(info.value)


def test_parse_fred_csv_refuses_a_bad_series_id():
    with pytest.raises(ValueError):
        parse_fred_csv("observation_date,DGS-10\n2026-10-25,4.0\n", "DGS-10")


# ---- parse_cftc

def report(day="2026-10-27T00:00:00.000", long="350000", short="420500", oi="4100000",
           code="043602"):
    return {"market_and_exchange_names": "UST BOND - CHICAGO BOARD OF TRADE",
            "cftc_contract_market_code": code, "report_date_as_yyyy_mm_dd": day,
            "noncomm_positions_long_all": long, "noncomm_positions_short_all": short,
            "open_interest_all": oi}


def without(name):
    row = report()
    del row[name]
    return row


def test_parse_cftc_net_position_and_open_interest():
    payload = [report(),  # the newest first, as cftc_url asks
               report("2026-10-20T00:00:00.000", long=" 360000 ", short=400000, oi=4000000,
                      code=" 043602 "),
               report("2026-10-13", long="-5", short="-15", oi="0")]
    net, oi = parse_cftc(payload, "043602")
    assert net == [(date(2026, 10, 13), 10.0), (date(2026, 10, 20), -40000.0),
                   (date(2026, 10, 27), -70500.0)]
    assert oi == [(date(2026, 10, 13), 0.0), (date(2026, 10, 20), 4000000.0),
                  (date(2026, 10, 27), 4100000.0)]
    assert all(type(value) is float for _, value in net + oi)


def test_parse_cftc_later_row_wins_in_both_series():
    payload = [report("2026-10-27", long="10", short="4", oi="100"),
               report("2026-10-20", long="1", short="1", oi="1"),
               report("2026-10-27T00:00:00.000", long="20", short="5", oi="200")]
    assert parse_cftc(payload, "043602") == (
        [(date(2026, 10, 20), 0.0), (date(2026, 10, 27), 15.0)],
        [(date(2026, 10, 20), 1.0), (date(2026, 10, 27), 200.0)])


def test_parse_cftc_empty_list():
    assert parse_cftc([], "043602") == ([], [])


def test_parse_cftc_includes_the_sites_error_message():
    with pytest.raises(ValueError) as info:
        parse_cftc({"code": "query.soql.no-such-column", "error": True,
                    "message": "No such column: foo"}, "043602")
    assert "No such column: foo" in str(info.value) and "043602" in str(info.value)


@pytest.mark.parametrize("payload", [{}, {"data": []}, "[]", None, 5])
def test_parse_cftc_refuses_what_is_not_a_list(payload):
    with pytest.raises(ValueError) as info:
        parse_cftc(payload, "043602")
    assert "043602" in str(info.value)


@pytest.mark.parametrize("row", [
    "not a row", None, ["043602"],
    without("cftc_contract_market_code"), without("report_date_as_yyyy_mm_dd"),
    without("noncomm_positions_long_all"), without("noncomm_positions_short_all"),
    without("open_interest_all"),
    report(code="13874A"), report(code="43602"), report(code=43602), report(code=None),
    report(day="2026-10-32T00:00:00.000"), report(day="10/27/2026"), report(day=""),
    report(day="T2026-10-27"), report(day=None), report(day=20261027),
    report(long="1.5"), report(long=""), report(long="1e3"), report(long="+5"),
    report(long=True), report(short=1.0), report(short=None), report(oi="12,000"),
    report(oi="four"), report(oi=chr(0x0661)),
])
def test_parse_cftc_refuses_a_bad_row(row):
    with pytest.raises(ValueError) as info:  # never KeyError or TypeError
        parse_cftc([report("2026-10-20"), row], "043602")
    assert "043602" in str(info.value)


@pytest.mark.parametrize("code", ["43602", "13874a", 43602])
def test_parse_cftc_refuses_a_bad_code(code):
    with pytest.raises(ValueError):
        parse_cftc([], code)


# ---- files

def test_save_series_writes_a_file_load_closes_reads(tmp_path):
    path = tmp_path / "series" / "deep" / "DGS10.json"
    save_series(path, [(date(2026, 10, 28), 4.05), (date(2026, 10, 26), 4),
                       [date(2026, 10, 28), 4.5]])
    assert json.loads(path.read_text(encoding="utf-8")) == [["2026-10-26", 4],
                                                            ["2026-10-28", 4.5]]
    assert load_closes(path) == [(date(2026, 10, 26), 4.0), (date(2026, 10, 28), 4.5)]
    save_series(str(path), iter([]))
    assert load_closes(path) == []


OLD = '[\n  [\n    "2026-01-02",\n    1.0\n  ]\n]\n'


@pytest.mark.parametrize("pair", [
    (datetime(2026, 10, 26, 0, 0), 4.0), ("2026-10-26", 4.0), (date(2026, 10, 26), "4.0"),
    (date(2026, 10, 26), True), (date(2026, 10, 26), float("nan")),
    (date(2026, 10, 26), float("inf")), (date(2026, 10, 26), None),
    (date(2026, 10, 26), 4.0, 5.0), (date(2026, 10, 26),), "ab", None,
])
def test_save_series_refuses_a_bad_pair_before_writing(tmp_path, pair):
    path = tmp_path / "DGS10.json"
    path.write_text(OLD, encoding="utf-8")
    with pytest.raises(ValueError):
        save_series(path, [(date(2026, 10, 25), 1.0), pair])
    assert path.read_text(encoding="utf-8") == OLD
    with pytest.raises(ValueError):
        save_series(tmp_path / "new" / "DGS10.json", [pair])
    assert not (tmp_path / "new").exists()


def test_load_series_current_and_as_known_on_a_date(tmp_path):
    save_series(tmp_path / "PAYEMS.json", [(date(2026, 7, 1), 159500.0),
                                           (date(2026, 8, 1), 159700.0),
                                           (date(2026, 9, 1), 159900.0)])
    save_series(tmp_path / "PAYEMS@2026-08-15.json", [(date(2026, 7, 1), 159400.0),
                                                      (date(2026, 8, 1), 159650.0),
                                                      (date(2026, 8, 20), 1.0)])
    assert load_series(tmp_path, "PAYEMS") == [(date(2026, 7, 1), 159500.0),
                                               (date(2026, 8, 1), 159700.0),
                                               (date(2026, 9, 1), 159900.0)]
    # The file of that date, and nothing after the date.
    known = [(date(2026, 7, 1), 159400.0), (date(2026, 8, 1), 159650.0)]
    assert load_series(tmp_path, "PAYEMS", "2026-08-15") == known
    assert load_series(str(tmp_path), "PAYEMS", date(2026, 8, 15)) == known
    # No file of that date: the current file, and nothing after the date.
    assert load_series(tmp_path, "PAYEMS", "2026-08-01") == [(date(2026, 7, 1), 159500.0),
                                                             (date(2026, 8, 1), 159700.0)]
    assert load_series(tmp_path, "PAYEMS", date(2026, 6, 30)) == []


def test_load_series_errors(tmp_path):
    with pytest.raises(OSError):
        load_series(tmp_path, "DGS10")
    with pytest.raises(OSError):
        load_series(tmp_path, "DGS10", "2026-10-01")
    (tmp_path / "BAD.json").write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_series(tmp_path, "BAD")
    save_series(tmp_path / "DGS10.json", [(date(2026, 10, 1), 4.0)])
    for as_of in ("2026-1-2", "soon", 20261002, 5.0):
        with pytest.raises(ValueError):
            load_series(tmp_path, "DGS10", as_of)
    with pytest.raises(ValueError):
        load_series(tmp_path, "../DGS10")


# ---- fetching

CSV_URL = "https://fred.example/x.csv"
CAFE = "caf" + chr(0xE9)


def test_fetch_text_sends_the_user_agent_and_accepts_any_content_type():
    for content_type in ("application/csv", "application/json", "image/png", "", None):
        site = Site({CSV_URL: (b"observation_date,DGS10\n", content_type)})
        assert fetch_text(CSV_URL, user_agent=UA, transport=site) == "observation_date,DGS10\n"
        assert site.calls == [(CSV_URL, {"User-Agent": UA})]


@pytest.mark.parametrize("body, content_type", [
    (CAFE.encode("utf-8"), "application/csv"),
    (CAFE.encode("utf-8"), ""),
    (CAFE.encode("latin-1"), "text/csv; charset=ISO-8859-1"),
    (CAFE.encode("cp1252"), 'Text/Plain; Charset="windows-1252"'),
    (CAFE.encode("utf-16"), "application/json; charset=utf-16"),
    (CAFE.encode("utf-8"), "text/csv; charset=no-such-charset"),
    (b"\xef\xbb\xbf" + CAFE.encode("utf-8"), "application/csv"),
    (b"\xef\xbb\xbf" + CAFE.encode("utf-8"), "text/csv; charset=utf-8"),
])
def test_fetch_text_decodes_by_the_charset(body, content_type):
    site = Site({CSV_URL: (body, content_type)})
    assert fetch_text(CSV_URL, user_agent="UA", transport=site) == CAFE


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError(CSV_URL, 404, "Not Found", {}, None),
    urllib.error.URLError("no route to host"),
    TimeoutError("timed out"),
    RuntimeError("a bug"),
])
def test_fetch_text_lets_transport_errors_through(error):
    with pytest.raises(type(error)):
        fetch_text(CSV_URL, user_agent="UA", transport=Site({CSV_URL: error}))


def test_fetch_text_default_transport_is_urlopen(monkeypatch):
    web = FakeWeb({CSV_URL: (b"observation_date,DGS10\n", "application/csv"),
                   "https://fred.example/cut": (http.client.IncompleteRead(b"obs"), "text/csv")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    assert fetch_text(CSV_URL, user_agent=UA) == "observation_date,DGS10\n"
    (request, timeout), = web.requests
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == CSV_URL and request.get_method() == "GET"
    assert request.get_header("User-agent") == UA
    assert timeout == 30
    assert all(response.closed for response in web.responses)
    with pytest.raises(urllib.error.HTTPError):
        fetch_text("https://fred.example/missing", user_agent=UA)
    with pytest.raises(OSError):
        fetch_text("https://fred.example/cut", user_agent=UA)


def test_fetch_fred_gets_the_address_and_parses_it():
    url = fred_url("PAYEMS", start="2024-01-01", vintage="2024-05-03")
    assert url == ("https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=PAYEMS"
                   "&vintage_date=2024-05-03&cosd=2024-01-01")
    site = Site({url: (b"\xef\xbb\xbfobservation_date,PAYEMS_20240503\r\n2024-01-01,157000\r\n"
                       b"2024-02-01,.\r\n", "application/csv")})
    assert fetch_fred("PAYEMS", user_agent=UA, start="2024-01-01", vintage=date(2024, 5, 3),
                      transport=site) == [(date(2024, 1, 1), 157000.0)]
    assert site.calls == [(url, {"User-Agent": UA})]


def test_fetch_fred_refuses_another_series():
    site = Site({fred_url("DGS10"): (b"observation_date,DGS2\n2026-10-26,3.5\n", "text/csv")})
    with pytest.raises(ValueError) as info:
        fetch_fred("DGS10", user_agent=UA, transport=site)
    assert "DGS10" in str(info.value)


@pytest.mark.parametrize("kwargs", [{"series_id": "DGS-10"},
                                    {"series_id": "DGS10", "start": "soon"},
                                    {"series_id": "DGS10", "vintage": 2024}])
def test_fetch_fred_checks_its_arguments_before_fetching(kwargs):
    site = Site({})
    with pytest.raises(ValueError):
        fetch_fred(user_agent=UA, transport=site, **kwargs)
    assert site.calls == []


COT = [report(), report("2026-10-20T00:00:00.000", long="360000", short="400000", oi="4000000")]
COT_NET = [(date(2026, 10, 20), -40000.0), (date(2026, 10, 27), -70500.0)]
COT_OI = [(date(2026, 10, 20), 4000000.0), (date(2026, 10, 27), 4100000.0)]


def test_fetch_cftc_gets_the_address_and_parses_the_json():
    url = cftc_url("043602", limit=2)
    site = Site({url: (json.dumps(COT).encode("utf-8"), "application/json;charset=utf-8")})
    assert fetch_cftc("043602", user_agent=UA, limit=2, transport=site) == (COT_NET, COT_OI)
    assert site.calls == [(url, {"User-Agent": UA})]
    site = Site({cftc_url("043602"): (b"[]", "application/json")})
    assert fetch_cftc("043602", user_agent=UA, transport=site) == ([], [])


def test_fetch_cftc_refuses_text_that_is_not_json():
    site = Site({cftc_url("043602"): (b"<html>Service unavailable</html>", "text/html")})
    with pytest.raises(ValueError) as info:
        fetch_cftc("043602", user_agent=UA, transport=site)
    assert "043602" in str(info.value)


def test_fetch_cftc_reports_the_sites_error_message():
    site = Site({cftc_url("043602"): (b'{"message": "Too many requests", "error": true}',
                                      "application/json")})
    with pytest.raises(ValueError) as info:
        fetch_cftc("043602", user_agent=UA, transport=site)
    assert "Too many requests" in str(info.value)


@pytest.mark.parametrize("code, limit", [("43602", 600), ("043602", 0), ("043602", True),
                                         ("043602", 50001), ("043602", "600")])
def test_fetch_cftc_checks_its_arguments_before_fetching(code, limit):
    site = Site({})
    with pytest.raises(ValueError):
        fetch_cftc(code, user_agent=UA, limit=limit, transport=site)
    assert site.calls == []


# ---- the fetch-series command

FRED_TEXT = b"observation_date,DGS10\n2026-10-26,4.01\n2026-10-27,\n2026-10-28,4.05\n"


def run(argv, capsys):
    code = main(["fetch-series", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_fetch_series_fred(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    url = fred_url("DGS10", start="2026-10-01")
    web = FakeWeb({url: (FRED_TEXT, "application/csv")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    path = tmp_path / "series" / "DGS10.json"
    code, out, err = run(["--source", "fred", "--id", "DGS10", "--file", str(path),
                          "--start", "2026-10-01"], capsys)
    assert code == 0, err
    assert json.loads(out) == {"source": "fred", "id": "DGS10", "observations": 2,
                               "first": "2026-10-26", "last": "2026-10-28", "file": str(path)}
    assert load_closes(path) == [(date(2026, 10, 26), 4.01), (date(2026, 10, 28), 4.05)]
    assert web.urls() == [url]
    assert web.user_agents() == {DEFAULT_UA}


def test_cli_fetch_series_fred_vintage_to_out(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("RESEARCHOS_USER_AGENT", UA)
    url = fred_url("PAYEMS", vintage="2024-05-03")
    web = FakeWeb({url: (b"observation_date,PAYEMS_20240503\n2024-03-01,157800\n"
                         b"2024-04-01,158000\n", "text/csv")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    path = tmp_path / "PAYEMS@2024-05-03.json"
    summary = tmp_path / "summary.json"
    code, out, err = run(["--source", "fred", "--id", "PAYEMS", "--vintage", "2024-05-03",
                          "--file", str(path), "--out", str(summary)], capsys)
    assert code == 0, err
    assert out == ""
    assert json.loads(summary.read_text(encoding="utf-8")) == {
        "source": "fred", "id": "PAYEMS", "observations": 2, "first": "2024-03-01",
        "last": "2024-04-01", "file": str(path)}
    assert web.urls() == [url]
    assert web.user_agents() == {UA}
    assert load_series(tmp_path, "PAYEMS", "2024-05-03") == [(date(2024, 3, 1), 157800.0),
                                                             (date(2024, 4, 1), 158000.0)]


def test_cli_fetch_series_cftc(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("RESEARCHOS_USER_AGENT", UA)
    url = cftc_url("043602", limit=52)
    web = FakeWeb({url: (json.dumps(COT).encode("utf-8"), "application/json")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    net_path, oi_path = tmp_path / "cot" / "net.json", tmp_path / "cot" / "oi.json"
    code, out, err = run(["--source", "cftc", "--id", "043602", "--limit", "52",
                          "--file", str(net_path), "--oi-file", str(oi_path),
                          "--user-agent", "Cli Tester cli@example.com"], capsys)
    assert code == 0, err
    assert json.loads(out) == {"source": "cftc", "id": "043602", "observations": 2,
                               "first": "2026-10-20", "last": "2026-10-27",
                               "file": str(net_path), "oi_file": str(oi_path)}
    assert load_closes(net_path) == COT_NET
    assert load_closes(oi_path) == COT_OI
    assert web.urls() == [url]
    assert web.user_agents() == {"Cli Tester cli@example.com"}


def test_cli_fetch_series_with_no_observations_succeeds(tmp_path, capsys, monkeypatch):
    web = FakeWeb({cftc_url("13874A"): (b"[]", "application/json"),
                   fred_url("DGS10"): (b"observation_date,DGS10\n2026-10-27,.\n", "text/csv")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    path = tmp_path / "net.json"
    code, out, err = run(["--source", "cftc", "--id", "13874A", "--file", str(path)], capsys)
    assert code == 0, err
    assert json.loads(out) == {"source": "cftc", "id": "13874A", "observations": 0,
                               "first": None, "last": None, "file": str(path), "oi_file": None}
    assert load_closes(path) == []
    assert [p.name for p in tmp_path.iterdir()] == ["net.json"]
    path = tmp_path / "DGS10.json"
    code, out, err = run(["--source", "fred", "--id", "DGS10", "--file", str(path)], capsys)
    assert code == 0, err
    assert json.loads(out) == {"source": "fred", "id": "DGS10", "observations": 0,
                               "first": None, "last": None, "file": str(path)}
    assert web.urls() == [cftc_url("13874A"), fred_url("DGS10")]


@pytest.mark.parametrize("argv", [
    ["--source", "fred", "--id", "DGS10", "--file", "FILE", "--start", "2026-13-01"],
    ["--source", "fred", "--id", "DGS10", "--file", "FILE", "--vintage", "last week"],
    ["--source", "cftc", "--id", "043602", "--file", "FILE", "--start", "2026-01-01"],
    ["--source", "cftc", "--id", "043602", "--file", "FILE", "--vintage", "2026-01-01"],
    ["--source", "cftc", "--id", "043602", "--file", "FILE", "--limit", "0"],
    ["--source", "cftc", "--id", "043602", "--file", "FILE", "--limit", "50001"],
    ["--source", "cftc", "--id", "043602", "--file", "FILE", "--limit", "ten"],
    ["--source", "cftc", "--id", "043602", "--file", "FILE", "--limit", "1.5"],
    ["--source", "fred", "--id", "DGS10", "--file", "FILE", "--limit", "10"],
    ["--source", "fred", "--id", "DGS10", "--file", "FILE", "--oi-file", "OI"],
    ["--source", "ecb", "--id", "DGS10", "--file", "FILE"],
    ["--id", "DGS10", "--file", "FILE"],
    ["--source", "fred", "--file", "FILE"],
    ["--source", "fred", "--id", "DGS10"],
    ["--source", "fred", "--id", "DGS10", "--file", "FILE", "stray"],
])
def test_cli_fetch_series_bad_arguments_exit_2(tmp_path, capsys, argv):
    paths = {"FILE": str(tmp_path / "s.json"), "OI": str(tmp_path / "oi.json")}
    code, out, err = run([paths.get(a, a) for a in argv], capsys)
    assert code == 2
    assert out == "" and err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("argv", [["--source", "fred", "--id", "DGS-10"],
                                  ["--source", "fred", "--id", "dgs 10", "--start", "2026-01-01"],
                                  ["--source", "cftc", "--id", "43602"],
                                  ["--source", "cftc", "--id", "13874a", "--limit", "5"]])
def test_cli_fetch_series_bad_id_or_code_exits_1(tmp_path, capsys, argv):
    code, out, err = run([*argv, "--file", str(tmp_path / "s.json")], capsys)
    assert code == 1
    assert out == "" and err.startswith("error:")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("source, series, served", [
    ("fred", "DGS10", None),  # a 404
    ("fred", "DGS10", (b"observation_date,DGS2\n2026-10-26,3.5\n", "application/csv")),
    ("fred", "DGS10", (b"observation_date,DGS10\n2026-10-26,4.01\n2026-10-27,n/a\n", "text/csv")),
    ("fred", "DGS10", (http.client.IncompleteRead(b"obs"), "text/csv")),
    ("cftc", "043602", None),
    ("cftc", "043602", (b"<html>busy</html>", "text/html")),
    ("cftc", "043602", (b'{"message": "Too many requests"}', "application/json")),
    ("cftc", "043602", (json.dumps([report(), report(code="13874A")]).encode("utf-8"),
                        "application/json")),
])
def test_cli_fetch_series_failure_writes_nothing(tmp_path, capsys, monkeypatch, source, series,
                                                  served):
    url = fred_url(series) if source == "fred" else cftc_url(series)
    monkeypatch.setattr(urllib.request, "urlopen", FakeWeb({} if served is None else {url: served}))
    path, oi_path = tmp_path / "s.json", tmp_path / "oi.json"
    path.write_text(OLD, encoding="utf-8")
    argv = ["--source", source, "--id", series, "--file", str(path)]
    if source == "cftc":
        argv += ["--oi-file", str(oi_path)]
    code, out, err = run(argv, capsys)
    assert code == 1
    assert out == "" and err.startswith("error:")
    assert path.read_text(encoding="utf-8") == OLD
    assert not oi_path.exists()


def test_cli_fetch_series_file_that_cannot_be_written_exits_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        FakeWeb({fred_url("DGS10"): (FRED_TEXT, "application/csv")}))
    taken = tmp_path / "taken"
    taken.mkdir()
    code, out, err = run(["--source", "fred", "--id", "DGS10", "--file", str(taken)], capsys)
    assert code == 1
    assert out == "" and err.startswith("error:")
