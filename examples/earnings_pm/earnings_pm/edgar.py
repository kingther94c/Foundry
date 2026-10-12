"""SEC EDGAR client over the companyfacts JSON API.

The HTTP fetcher is injected (``Fetcher = Callable[[str], bytes]``) so tests
never touch the network. The default fetcher uses urllib and sends the contact
User-Agent SEC requires, read from ``EARNINGS_PM_USER_AGENT``.

Only values SEC itself marks as covering exactly one calendar quarter (a
``frame`` of the form ``CY####Q#``) are reported. Nothing is derived: a metric
filed only year-to-date (common for cash-flow items) is reported as missing,
never back-filled by subtraction.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

Fetcher = Callable[[str], bytes]

USER_AGENT_ENV = "EARNINGS_PM_USER_AGENT"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_QUARTER_FRAME = re.compile(r"CY\d{4}Q[1-4]")


class EdgarError(RuntimeError):
    """EDGAR could not be reached or returned something unusable."""


def default_fetcher(url: str, timeout: float = 30.0) -> bytes:
    """Fetch ``url`` with urllib, sending the User-Agent SEC requires."""
    user_agent = os.environ.get(USER_AGENT_ENV, "").strip()
    if not user_agent:
        raise EdgarError(
            "SEC requires a contact User-Agent on every request (e.g. "
            f"'Your Name you@example.com'); set the {USER_AGENT_ENV} "
            "environment variable."
        )
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _normalize_ticker(ticker: str) -> str:
    # SEC lists share classes with a dash (BRK-B); people often type BRK.B.
    return ticker.strip().upper().replace(".", "-")


def _pad_cik(cik: int | str) -> int:
    if isinstance(cik, bool):
        raise ValueError(f"invalid CIK: {cik!r}")
    if isinstance(cik, str):
        text = cik.strip()
        if text.upper().startswith("CIK"):
            text = text[3:]
        if not text.isdigit():
            raise ValueError(f"invalid CIK: {cik!r}")
        cik = int(text)
    if not isinstance(cik, int) or cik <= 0 or cik >= 10**10:
        raise ValueError(f"invalid CIK: {cik!r}")
    return cik


class EdgarClient:
    """Looks up CIKs and company facts through an injectable fetcher."""

    def __init__(self, fetch: Fetcher = default_fetcher) -> None:
        self._fetch = fetch
        self._tickers: dict[str, int] | None = None

    def _get_json(self, url: str) -> Any:
        try:
            raw = self._fetch(url)
        except EdgarError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise EdgarError(f"could not fetch {url}: {exc}") from exc
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise EdgarError(f"{url} did not return valid JSON: {exc}") from exc

    def _ticker_map(self) -> dict[str, int]:
        if self._tickers is None:
            data = self._get_json(TICKERS_URL)
            if not isinstance(data, dict):
                raise EdgarError(f"{TICKERS_URL} has an unexpected shape")
            mapping: dict[str, int] = {}
            for entry in data.values():
                if not isinstance(entry, dict):
                    continue
                ticker = entry.get("ticker")
                cik = entry.get("cik_str")
                if isinstance(ticker, str) and isinstance(cik, (int, str)):
                    try:
                        mapping.setdefault(_normalize_ticker(ticker), _pad_cik(cik))
                    except ValueError:
                        continue
            self._tickers = mapping
        return self._tickers

    def cik_for(self, ticker: str) -> int:
        """CIK for ``ticker`` (case-insensitive). Raises ValueError if unknown."""
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        key = _normalize_ticker(ticker)
        cik = self._ticker_map().get(key)
        if cik is None:
            raise ValueError(f"unknown ticker {ticker!r}: not in SEC's company_tickers.json")
        return cik

    def company_facts(self, cik: int | str) -> dict[str, Any]:
        """The companyfacts JSON for ``cik`` (zero-padded to 10 digits in the URL)."""
        url = COMPANY_FACTS_URL.format(cik=_pad_cik(cik))
        data = self._get_json(url)
        if not isinstance(data, dict):
            raise EdgarError(f"{url} has an unexpected shape")
        return data


def _concept_units(facts: dict[str, Any], concept: str, unit: str) -> list[Any] | None:
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    entry = us_gaap.get(concept)
    if not isinstance(entry, dict):
        return None
    values = (entry.get("units") or {}).get(unit)
    return values if isinstance(values, list) else None


def _select_concept(
    facts: dict[str, Any], concepts: Sequence[str], unit: str
) -> tuple[str, list[dict[str, Any]]] | None:
    """Pick the candidate concept that is current, with its quarterly series.

    Companies switch concepts over time (e.g. "Revenues" until ~2018, then
    "RevenueFromContractWithCustomerExcludingAssessedTax"), and the stale one
    stays in companyfacts. So among the candidates that have quarterly values,
    the one whose most recent quarter ends latest wins; on a tie the earlier
    candidate wins. If no candidate has quarterly values, the first one that
    exists is returned with an empty series; if none exists, ``None``.
    """
    best: tuple[str, list[dict[str, Any]]] | None = None
    best_end = ""
    first_existing: str | None = None
    for concept in concepts:
        values = _concept_units(facts, concept, unit)
        if values is None:
            continue
        if first_existing is None:
            first_existing = concept
        series = _quarterly(values)
        if not series:
            continue
        latest_end = str(series[-1]["end"] or "")
        if best is None or latest_end > best_end:
            best, best_end = (concept, series), latest_end
    if best is not None:
        return best
    if first_existing is not None:
        return first_existing, []
    return None


def _quarterly(values: list[Any]) -> list[dict[str, Any]]:
    by_frame: dict[str, dict[str, Any]] = {}
    for fact in values:
        if not isinstance(fact, dict):
            continue
        frame = fact.get("frame")
        if not isinstance(frame, str) or not _QUARTER_FRAME.fullmatch(frame):
            continue
        val = fact.get("val")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        row = {
            "frame": frame,
            "end": fact.get("end"),
            "val": val,
            "form": fact.get("form"),
            "filed": fact.get("filed"),
        }
        # SEC already deduplicates frames; if one repeats anyway, keep the
        # most recently filed value.
        existing = by_frame.get(frame)
        if existing is None or str(row["filed"] or "") > str(existing["filed"] or ""):
            by_frame[frame] = row
    return sorted(by_frame.values(), key=lambda r: (str(r["end"] or ""), r["frame"]))


def quarterly_series(
    facts: dict[str, Any], concepts: Sequence[str], unit: str = "USD"
) -> list[dict[str, Any]]:
    """Single-calendar-quarter values of the current concept in ``concepts``
    (reported in the us-gaap section with ``unit``).

    Among candidates with quarterly values, the one whose latest quarter ends
    latest is used; ties go to the earlier candidate.

    Returns ``[{frame, end, val, form, filed}]`` sorted by end date; empty when
    no concept exists or none has quarterly frames.
    """
    found = _select_concept(facts, concepts, unit)
    return found[1] if found else []


# (metric, candidate concepts in priority order, unit)
SUMMARY_METRICS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "revenue",
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ),
        "USD",
    ),
    ("gross_profit", ("GrossProfit",), "USD"),
    ("operating_income", ("OperatingIncomeLoss",), "USD"),
    ("net_income", ("NetIncomeLoss",), "USD"),
    ("diluted_eps", ("EarningsPerShareDiluted",), "USD/shares"),
    ("operating_cash_flow", ("NetCashProvidedByUsedInOperatingActivities",), "USD"),
    ("capex", ("PaymentsToAcquirePropertyPlantAndEquipment",), "USD"),
)


def summarize(facts: dict[str, Any], quarters: int = 5) -> dict[str, Any]:
    """Entity name plus the last ``quarters`` single-quarter values per metric.

    A metric without quarterly frames is reported with ``missing: True`` and a
    note; it is never filled in or derived from year-to-date figures.
    """
    if quarters < 1:
        raise ValueError("quarters must be at least 1")
    metrics: dict[str, Any] = {}
    for name, concepts, unit in SUMMARY_METRICS:
        found = _select_concept(facts, concepts, unit)
        if found is None:
            metrics[name] = {
                "concept": None,
                "unit": unit,
                "missing": True,
                "note": f"none of {', '.join(concepts)} is reported in {unit}.",
                "values": [],
            }
            continue
        concept, series = found
        if not series:
            metrics[name] = {
                "concept": concept,
                "unit": unit,
                "missing": True,
                "note": (
                    f"{concept} has no single-quarter (CY####Q#) values; it is "
                    "likely filed only year-to-date, as cash-flow items often "
                    "are. Not derived -- read it from the earnings release."
                ),
                "values": [],
            }
            continue
        metrics[name] = {
            "concept": concept,
            "unit": unit,
            "missing": False,
            "note": None,
            "values": series[-quarters:],
        }
    return {
        "entity_name": facts.get("entityName"),
        "cik": facts.get("cik"),
        "quarters": quarters,
        "metrics": metrics,
    }


COMPANY_FACTS_TOOL: dict[str, Any] = {
    "name": "get_company_facts",
    "description": (
        "Look up a US-listed company's recent quarterly figures as filed with "
        "SEC EDGAR (XBRL company facts): revenue, gross profit, operating "
        "income, net income, diluted EPS, operating cash flow and capex for the "
        "last five calendar quarters. Only values covering exactly one calendar "
        "quarter are returned; a metric filed only year-to-date is marked "
        "missing. Figures are in USD as reported. If this tool returns an error, "
        "EDGAR is unavailable: use the web tools on the company's investor-"
        "relations page or a newswire instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": "Stock ticker, e.g. 'MSFT' or 'BRK-B'.",
            },
        },
        "required": ["ticker"],
        "additionalProperties": False,
    },
    "strict": True,
}


def run_company_facts_tool(client: EdgarClient, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a get_company_facts tool call.

    Raises ValueError for bad input or an unknown ticker and EdgarError when
    EDGAR is unreachable; the agent loop reports either as an error result.
    """
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("ticker"), str):
        raise ValueError("get_company_facts needs a 'ticker' string")
    ticker = tool_input["ticker"]
    cik = client.cik_for(ticker)
    summary = summarize(client.company_facts(cik))
    return {"ticker": _normalize_ticker(ticker), **summary, "cik": cik}