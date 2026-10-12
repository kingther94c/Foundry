"""Tests for earnings_pm.edgar. Offline only: every fetch goes through a fake.

All fixtures below are synthetic and hand-written; they describe no real company,
and only mimic the shape of SEC's company_tickers.json and companyfacts JSON.
"""

import json
import urllib.error

import pytest

from earnings_pm import edgar
from earnings_pm.edgar import (
    COMPANY_FACTS_TOOL,
    EdgarClient,
    EdgarError,
    quarterly_series,
    run_company_facts_tool,
    summarize,
)

UNSUPPORTED_KEYWORDS = {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}

# Synthetic ticker map.
TICKERS = {
    "0": {"cik_str": 1234567, "ticker": "EXMP", "title": "Example Corp"},
    "1": {"cik_str": 7654321, "ticker": "BRK-Z", "title": "Synthetic Holdings"},
}


def fact(val, end, frame=None, start=None, form="10-Q", filed="2026-11-01"):
    d = {"end": end, "val": val, "fy": 2026, "fp": "Q3", "form": form, "filed": filed}
    if start is not None:
        d["start"] = start
    if frame is not None:
        d["frame"] = frame
    return d


# Synthetic companyfacts.
FACTS = {
    "cik": 1234567,
    "entityName": "Example Corp",
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": []}}},
        "us-gaap": {
            # Revenue under the ASC 606 concept; "Revenues" absent.
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        # Out of order on purpose; must come back sorted by end.
                        fact(1300, "2026-09-30", "CY2026Q3"),
                        fact(1000, "2025-09-30", "CY2025Q3", filed="2025-11-01"),
                        fact(1050, "2025-12-31", "CY2025Q4", form="10-K", filed="2026-02-01"),
                        fact(1100, "2026-03-31", "CY2026Q1", filed="2026-05-01"),
                        fact(1200, "2026-06-30", "CY2026Q2", filed="2026-08-01"),
                        fact(900, "2025-06-30", "CY2025Q2", filed="2025-08-01"),
                        # Year-to-date (nine months) and annual: not quarterly.
                        fact(3600, "2026-09-30", None, start="2026-01-01"),
                        fact(4000, "2025-12-31", "CY2025", form="10-K"),
                        # Re-filed comparative value without a frame: ignored.
                        fact(1000, "2025-09-30", None, filed="2026-11-01"),
                        # Instant frame: not a duration quarter.
                        fact(5, "2026-09-30", "CY2026Q3I"),
                    ]
                }
            },
            "SalesRevenueNet": {"units": {"USD": [fact(1, "2026-09-30", "CY2026Q3")]}},
            "GrossProfit": {"units": {"USD": [fact(780, "2026-09-30", "CY2026Q3")]}},
            "OperatingIncomeLoss": {"units": {"USD": [fact(-20, "2026-09-30", "CY2026Q3")]}},
            "NetIncomeLoss": {"units": {"USD": [fact(0, "2026-09-30", "CY2026Q3")]}},
            "EarningsPerShareDiluted": {
                "units": {"USD/shares": [fact(1.25, "2026-09-30", "CY2026Q3")]}
            },
            # Cash flow filed only year-to-date: no quarterly frames.
            "NetCashProvidedByUsedInOperatingActivities": {
                "units": {
                    "USD": [
                        fact(400, "2026-03-31", None, start="2026-01-01"),
                        fact(900, "2026-06-30", None, start="2026-01-01"),
                        fact(1500, "2025-12-31", "CY2025", form="10-K"),
                    ]
                }
            },
            # Capex absent entirely.
        },
    },
}

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0001234567.json"


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, bytes):
            return response
        return json.dumps(response).encode("utf-8")


def make_client():
    return EdgarClient(FakeFetcher({edgar.TICKERS_URL: TICKERS, FACTS_URL: FACTS}))


# --- default fetcher --------------------------------------------------------


def test_default_fetcher_requires_user_agent(monkeypatch):
    monkeypatch.delenv(edgar.USER_AGENT_ENV, raising=False)
    called = []
    monkeypatch.setattr(edgar.urllib.request, "urlopen", lambda *a, **k: called.append(a))
    with pytest.raises(EdgarError, match="SEC requires a contact User-Agent"):
        edgar.default_fetcher(edgar.TICKERS_URL)
    assert called == []


def test_default_fetcher_sends_user_agent(monkeypatch):
    monkeypatch.setenv(edgar.USER_AGENT_ENV, "Test Person test@example.com")
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        seen["ua"] = request.get_header("User-agent")
        seen["url"] = request.full_url
        return Response()

    monkeypatch.setattr(edgar.urllib.request, "urlopen", fake_urlopen)
    assert edgar.default_fetcher(edgar.TICKERS_URL) == b"{}"
    assert seen == {"ua": "Test Person test@example.com", "url": edgar.TICKERS_URL}


def test_client_defaults_to_default_fetcher():
    assert EdgarClient()._fetch is edgar.default_fetcher


# --- client -----------------------------------------------------------------


@pytest.mark.parametrize("ticker", ["EXMP", "exmp", " Exmp "])
def test_cik_for_is_case_insensitive(ticker):
    assert make_client().cik_for(ticker) == 1234567


def test_cik_for_accepts_dot_share_class():
    assert make_client().cik_for("brk.z") == 7654321


def test_cik_for_unknown_ticker():
    with pytest.raises(ValueError, match="unknown ticker 'NOPE'"):
        make_client().cik_for("NOPE")


def test_ticker_map_fetched_once():
    client = make_client()
    client.cik_for("EXMP")
    client.cik_for("BRK-Z")
    assert client._fetch.urls == [edgar.TICKERS_URL]


@pytest.mark.parametrize("cik", [1234567, "1234567", "0001234567", "CIK0001234567"])
def test_company_facts_pads_cik(cik):
    client = make_client()
    assert client.company_facts(cik)["entityName"] == "Example Corp"
    assert client._fetch.urls == [FACTS_URL]


@pytest.mark.parametrize("cik", [0, -1, "abc", 10**10, True])
def test_company_facts_rejects_bad_cik(cik):
    with pytest.raises(ValueError, match="invalid CIK"):
        make_client().company_facts(cik)


def test_network_failure_becomes_edgar_error():
    client = EdgarClient(FakeFetcher({edgar.TICKERS_URL: urllib.error.URLError("no route")}))
    with pytest.raises(EdgarError, match="could not fetch"):
        client.cik_for("EXMP")


def test_invalid_json_becomes_edgar_error():
    client = EdgarClient(FakeFetcher({edgar.TICKERS_URL: b"<html>blocked</html>"}))
    with pytest.raises(EdgarError, match="valid JSON"):
        client.cik_for("EXMP")


# --- quarterly_series -------------------------------------------------------


def test_quarterly_series_first_existing_concept_sorted():
    series = quarterly_series(
        FACTS,
        ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    )
    assert [r["frame"] for r in series] == [
        "CY2025Q2", "CY2025Q3", "CY2025Q4", "CY2026Q1", "CY2026Q2", "CY2026Q3",
    ]
    assert [r["val"] for r in series] == [900, 1000, 1050, 1100, 1200, 1300]
    assert series[2] == {
        "frame": "CY2025Q4",
        "end": "2025-12-31",
        "val": 1050,
        "form": "10-K",
        "filed": "2026-02-01",
    }


# Synthetic companyfacts: "Revenues" stops in 2018 and the ASC 606 concept runs
# to 2026, both still present -- as for many real filers after the switch.
STALE_FACTS = {
    "cik": 1234567,
    "entityName": "Example Corp",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        fact(500, "2017-12-31", "CY2017Q4", filed="2018-02-01"),
                        fact(510, "2018-03-31", "CY2018Q1", filed="2018-05-01"),
                        fact(520, "2018-06-30", "CY2018Q2", filed="2018-08-01"),
                        fact(530, "2018-09-30", "CY2018Q3", filed="2018-11-01"),
                        fact(540, "2018-12-31", "CY2018Q4", filed="2019-02-01"),
                        fact(2100, "2018-12-31", "CY2018", form="10-K"),
                    ]
                }
            },
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        fact(1000, "2025-09-30", "CY2025Q3", filed="2025-11-01"),
                        fact(1050, "2025-12-31", "CY2025Q4", filed="2026-02-01"),
                        fact(1100, "2026-03-31", "CY2026Q1", filed="2026-05-01"),
                        fact(1200, "2026-06-30", "CY2026Q2", filed="2026-08-01"),
                        fact(1300, "2026-09-30", "CY2026Q3"),
                    ]
                }
            },
            # Exists, but only year-to-date: no quarterly values, so ignored.
            "SalesRevenueNet": {
                "units": {"USD": [fact(9999, "2026-09-30", None, start="2026-01-01")]}
            },
        }
    },
}

REVENUE_CONCEPTS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
]


def test_summarize_prefers_concept_with_latest_quarter_over_stale_one():
    revenue = summarize(STALE_FACTS)["metrics"]["revenue"]
    assert revenue["missing"] is False
    assert revenue["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert [r["frame"] for r in revenue["values"]] == [
        "CY2025Q3", "CY2025Q4", "CY2026Q1", "CY2026Q2", "CY2026Q3",
    ]
    assert [r["val"] for r in revenue["values"]] == [1000, 1050, 1100, 1200, 1300]


def test_quarterly_series_prefers_concept_with_latest_quarter():
    series = quarterly_series(STALE_FACTS, REVENUE_CONCEPTS)
    assert [r["val"] for r in series] == [1000, 1050, 1100, 1200, 1300]


def test_concept_tie_goes_to_earlier_candidate():
    # FACTS: SalesRevenueNet's latest quarter ends on the same date as the
    # ASC 606 concept's; the earlier candidate wins.
    asc606 = "RevenueFromContractWithCustomerExcludingAssessedTax"
    series = quarterly_series(FACTS, [asc606, "SalesRevenueNet"])
    assert series[-1]["val"] == 1300
    series = quarterly_series(FACTS, ["SalesRevenueNet", asc606])
    assert [r["val"] for r in series] == [1]


def test_concept_without_quarters_reported_only_if_none_has_any():
    facts = {"facts": {"us-gaap": {
        "SalesRevenueNet": STALE_FACTS["facts"]["us-gaap"]["SalesRevenueNet"],
    }}}
    revenue = summarize(facts)["metrics"]["revenue"]
    assert revenue["missing"] is True
    assert revenue["concept"] == "SalesRevenueNet"
    assert revenue["values"] == []
    assert quarterly_series(facts, REVENUE_CONCEPTS) == []


def test_quarterly_series_eps_unit():
    assert quarterly_series(FACTS, ["EarningsPerShareDiluted"]) == []
    series = quarterly_series(FACTS, ["EarningsPerShareDiluted"], unit="USD/shares")
    assert [r["val"] for r in series] == [1.25]


def test_quarterly_series_missing_and_ytd_only():
    assert quarterly_series(FACTS, ["Nope"]) == []
    assert quarterly_series({}, ["Revenues"]) == []
    assert quarterly_series(FACTS, ["NetCashProvidedByUsedInOperatingActivities"]) == []


# --- summarize --------------------------------------------------------------


def test_summarize_last_n_quarters():
    summary = summarize(FACTS)
    assert summary["entity_name"] == "Example Corp"
    revenue = summary["metrics"]["revenue"]
    assert revenue["missing"] is False
    assert revenue["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert [r["val"] for r in revenue["values"]] == [1000, 1050, 1100, 1200, 1300]
    two = summarize(FACTS, quarters=2)["metrics"]["revenue"]["values"]
    assert [r["val"] for r in two] == [1200, 1300]


def test_summarize_keeps_zero_and_negative_values():
    metrics = summarize(FACTS)["metrics"]
    assert metrics["operating_income"]["values"][0]["val"] == -20
    assert metrics["net_income"]["values"][0]["val"] == 0
    assert metrics["diluted_eps"]["unit"] == "USD/shares"
    assert metrics["diluted_eps"]["values"][0]["val"] == 1.25


def test_summarize_ytd_only_metric_is_missing_not_filled():
    ocf = summarize(FACTS)["metrics"]["operating_cash_flow"]
    assert ocf["missing"] is True
    assert ocf["values"] == []
    assert ocf["concept"] == "NetCashProvidedByUsedInOperatingActivities"
    assert "year-to-date" in ocf["note"]


def test_summarize_absent_metric_is_missing():
    capex = summarize(FACTS)["metrics"]["capex"]
    assert capex["missing"] is True
    assert capex["values"] == []
    assert capex["concept"] is None
    assert "PaymentsToAcquirePropertyPlantAndEquipment" in capex["note"]


def test_summarize_has_every_metric_and_is_json():
    summary = summarize(FACTS)
    assert set(summary["metrics"]) == {
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "diluted_eps",
        "operating_cash_flow",
        "capex",
    }
    json.dumps(summary)


# --- tool -------------------------------------------------------------------


def test_company_facts_tool_is_strict():
    tool = COMPANY_FACTS_TOOL
    assert tool["name"] == "get_company_facts"
    assert tool["strict"] is True
    assert set(tool) == {"name", "description", "input_schema", "strict"}
    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["ticker"]
    assert set(schema["properties"]) == {"ticker"}
    for sub in [schema, *schema["properties"].values()]:
        assert not (UNSUPPORTED_KEYWORDS & set(sub))


def test_run_company_facts_tool():
    result = run_company_facts_tool(make_client(), {"ticker": "exmp"})
    assert result["ticker"] == "EXMP"
    assert result["cik"] == 1234567
    assert result["entity_name"] == "Example Corp"
    assert len(result["metrics"]["revenue"]["values"]) == 5
    json.dumps(result)


def test_run_company_facts_tool_errors():
    with pytest.raises(ValueError, match="ticker"):
        run_company_facts_tool(make_client(), {})
    with pytest.raises(ValueError, match="unknown ticker"):
        run_company_facts_tool(make_client(), {"ticker": "NOPE"})
    offline = EdgarClient(FakeFetcher({edgar.TICKERS_URL: OSError("unreachable")}))
    with pytest.raises(EdgarError):
        run_company_facts_tool(offline, {"ticker": "EXMP"})
