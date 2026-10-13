"""Build real cases: documents from the web, the outcome from daily closes.

Documents are found and fetched through the Claude API's server-side web
tools (``fetch_pages``); an agent cuts the document proper out of each page
(``select_span``) and extracts the pre-report consensus
(``expectations_document``). Prices are never fetched here: they arrive as
JSON files of ``[["YYYY-MM-DD", close], ...]``.
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path

from tools.returns import abnormal_return, event_return

from researchos.cases import Case, Document, Outcome, save_case
from researchos.llm import BETAS, DEFAULT_MODEL, LLMError, _get, _is_api_error

TIMINGS = ("bmo", "amc")


# ---- prices and the outcome -------------------------------------------------------

def load_closes(path) -> list[tuple[date, float]]:
    """Read ``[["2026-07-16", 1234.5], ...]`` from a JSON file, sorted by date."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of [date, close] pairs")
    closes: list[tuple[date, float]] = []
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"{path}: expected [date, close], got {item!r}")
        day, close = item
        if not isinstance(day, str) or isinstance(close, bool) \
                or not isinstance(close, (int, float)):
            raise ValueError(f"{path}: expected [ISO date, number], got {item!r}")
        closes.append((date.fromisoformat(day), float(close)))
    closes.sort(key=lambda pair: pair[0])
    return closes


def event_date_for(report_date: date, timing: str, trading_days) -> date:
    """The first session that can react to a report.

    ``"bmo"`` (before the open): the first trading day on or after
    ``report_date``; ``"amc"`` (after the close): the first trading day
    strictly after it.
    """
    if timing not in TIMINGS:
        raise ValueError(f"timing must be 'bmo' or 'amc', got {timing!r}")
    days = sorted(trading_days)
    index = bisect_left(days, report_date) if timing == "bmo" else bisect_right(days, report_date)
    if index >= len(days):
        raise ValueError(
            f"no trading day {'on or after' if timing == 'bmo' else 'after'} {report_date}")
    return days[index]


def outcome_from_closes(stock, market, event_date: date, *, notes: str = "",
                        sources=()) -> Outcome:
    """The one-session stock, market and abnormal returns around ``event_date``."""
    stock_return = event_return(stock, event_date)
    if stock_return is None:
        raise ValueError(f"stock closes do not cover the session of {event_date}")
    market_return = event_return(market, event_date)
    if market_return is None:
        raise ValueError(f"market closes do not cover the session of {event_date}")
    abnormal = abnormal_return(stock_return, market_return)
    if abnormal is None:
        raise ValueError("could not compute the abnormal return")
    return Outcome(abnormal_return_1d=abnormal, stock_return_1d=stock_return,
                   market_return_1d=market_return, notes=notes, sources=list(sources))


# ---- fetching pages -----------------------------------------------------------------

@dataclass
class FetchedPage:
    url: str
    title: str
    text: str


FETCH_SYSTEM = """\
You are a research librarian collecting source documents about a company's \
quarterly earnings. Use web search to locate what the user asks for and web \
fetch to retrieve it.

Fetch primary documents: the company's own results release (from its \
investor-relations site, its newsroom or a wire service carrying the full \
release) and complete earnings call transcripts with the prepared remarks and \
the Q&A. Do not settle for news articles, blog posts or summaries that cover \
those documents; fetch the documents themselves. For analyst expectations, \
fetch pages that state the consensus as it stood before the results came out.

Do not summarize, paraphrase or rewrite anything: the fetched text is the \
product. Fetch the most relevant pages and then stop; a short note saying \
what you fetched is enough as your reply."""


def _web_tools(max_searches: int, max_fetches: int) -> list[dict]:
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_fetches},
    ]


def _page_from_block(block) -> FetchedPage | None:
    if _get(block, "type") != "web_fetch_tool_result":
        return None
    result = _get(block, "content")
    if _get(result, "type") != "web_fetch_result":
        return None
    document = _get(result, "content")
    source = _get(document, "source")
    if _get(source, "type") != "text":
        return None
    url = _get(result, "url")
    text = _get(source, "data")
    if not isinstance(url, str) or not isinstance(text, str):
        return None
    return FetchedPage(url=url, title=_get(document, "title") or "", text=text)


def fetch_pages(client, request: str, *, model: str = DEFAULT_MODEL, max_searches: int = 5,
                max_fetches: int = 6, max_continuations: int = 4) -> list[FetchedPage]:
    """Ask the model to find and fetch what ``request`` describes; return the text pages."""
    messages: list[dict] = [{"role": "user", "content": request}]
    tools = _web_tools(max_searches, max_fetches)
    pages: list[FetchedPage] = []
    seen: set[str] = set()
    continuations = 0
    while True:
        try:
            response = client.beta.messages.create(
                model=model,
                max_tokens=16000,
                betas=list(BETAS),
                fallbacks="default",
                output_config={"effort": "medium"},
                system=FETCH_SYSTEM,
                tools=tools,
                messages=list(messages),
            )
        except Exception as exc:
            if _is_api_error(exc):
                raise LLMError(f"Claude API request failed: {exc}") from exc
            raise
        stop_reason = _get(response, "stop_reason")
        if stop_reason == "refusal":
            category = _get(_get(response, "stop_details"), "category")
            if category:
                raise LLMError(f"model refused the fetch request (category: {category})")
            raise LLMError("model refused the fetch request")
        content = _get(response, "content") or []
        for block in content:
            page = _page_from_block(block)
            if page is not None and page.url not in seen:
                seen.add(page.url)
                pages.append(page)
        if stop_reason != "pause_turn" or continuations >= max_continuations:
            return pages
        continuations += 1
        messages.append({"role": "assistant", "content": content})


# ---- cutting the document out of the page --------------------------------------------

SPAN_SYSTEM = """\
You locate one document inside a web page that also carries navigation, \
menus, ads, cookie notices, related links and other clutter.

Find the document proper. For a results release: from its first line \
(headline or dateline) to the end of its financial tables, leaving out \
boilerplate that follows (contact details, "about the company" blurbs are \
fine to drop). For an earnings call transcript: the prepared remarks and the \
Q&A, leaving out any commentary about the share price or how the stock \
traded, and any editorial introduction or wrap-up by the publisher.

Report the first ten or so words and the last ten or so words of that \
document, copied exactly as they appear in the page text (same spelling, \
punctuation and spacing), so they can be located by exact string search. If \
the page does not contain the requested document (for example it is only a \
news story about it, or a teaser of a paywalled page), set found to false \
and leave both strings empty."""

SPAN_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "first_words": {"type": "string"},
        "last_words": {"type": "string"},
    },
    "required": ["found", "first_words", "last_words"],
    "additionalProperties": False,
}

_KIND_LABELS = {"release": "earnings results release",
                "transcript": "earnings call transcript"}


def select_span(agent, page: FetchedPage, kind: str) -> str | None:
    """Cut the ``kind`` document out of ``page``; None if it is not there."""
    label = _KIND_LABELS.get(kind, kind)
    prompt = (f"Document kind: {kind} ({label})\n"
              f"Page title: {page.title}\n\n"
              f"Page text:\n<<<\n{page.text}\n>>>")
    answer = agent.json(SPAN_SYSTEM, prompt, SPAN_SCHEMA)
    if not answer.get("found"):
        return None
    first_words = answer.get("first_words")
    last_words = answer.get("last_words")
    # An empty marker would "match" anywhere and cut nothing meaningful.
    if not isinstance(first_words, str) or not isinstance(last_words, str) \
            or not first_words.strip() or not last_words.strip():
        return None
    i = page.text.find(first_words)
    if i < 0:
        return None
    j = page.text.find(last_words, i)
    if j < 0:
        return None
    span = page.text[i: j + len(last_words)].strip()
    return span or None


# ---- expectations ----------------------------------------------------------------------

EXPECT_SYSTEM = """\
You extract what analysts expected from a company's quarterly results \
before those results were published: consensus EPS, consensus revenue, \
consensus segment figures or key metrics, and the company's own prior \
guidance where the pages state it as the bar to beat.

Report only expectations as they stood before the report. Never report the \
actual results, whether the company beat or missed, guidance issued with the \
results, or anything about how the stock traded or reacted. Use only figures \
stated in the pages you are given; do not add figures from memory. Copy each \
figure as stated, with its unit. For each item give the metric, the \
consensus value, and the source (publication or site named in the page). If \
the pages state no pre-report expectations, return an empty list."""

EXPECT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "consensus": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["metric", "consensus", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _render_pages(pages: list[FetchedPage]) -> str:
    parts = []
    for n, page in enumerate(pages, 1):
        parts.append(f"### Page {n}: {page.title}\nURL: {page.url}\n\n{page.text.strip()}\n")
    return "\n".join(parts)


def expectations_document(agent, pages: list[FetchedPage], company: str,
                          fiscal_period: str) -> Document | None:
    """The pre-report consensus for ``fiscal_period`` as a Document; None if there is none."""
    if not pages:
        # Nothing to extract from: asking anyway would invite figures from memory.
        return None
    prompt = (f"Company: {company}\nFiscal period: {fiscal_period}\n\n"
              f"List the analyst consensus expectations for this period as they stood "
              f"before the results were published.\n\n{_render_pages(pages)}")
    answer = agent.json(EXPECT_SYSTEM, prompt, EXPECT_SCHEMA)
    items = [item for item in answer.get("items") or [] if isinstance(item, dict)]
    if not items:
        return None
    lines = [f"- {item.get('metric', '')}: {item.get('consensus', '')} "
             f"(source: {item.get('source', '')})" for item in items]
    return Document("expectations", f"Consensus expectations for {fiscal_period}",
                    "\n".join(lines))


# ---- building a case -------------------------------------------------------------------

@dataclass
class CaseSpec:
    case_id: str
    company: str
    ticker: str
    aliases: list[str]
    fiscal_period: str  # "Q2 2026"
    report_date: str  # ISO date the results came out
    timing: str  # "bmo" | "amc"


def case_spec_from_dict(data) -> CaseSpec:
    """A CaseSpec from parsed JSON; ValueError on missing, unknown or mistyped fields."""
    if not isinstance(data, dict):
        raise ValueError("a case spec must be a JSON object")
    names = [f.name for f in fields(CaseSpec)]
    missing = [n for n in names if n not in data]
    unknown = [k for k in data if k not in names]
    if missing or unknown:
        raise ValueError(f"bad case spec: missing {missing}, unknown {unknown}")
    for name in names:
        value = data[name]
        if name == "aliases":
            if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
                raise ValueError("case spec 'aliases' must be a list of strings")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"case spec {name!r} must be a non-empty string")
    date.fromisoformat(data["report_date"])  # ValueError if malformed
    if data["timing"] not in TIMINGS:
        raise ValueError(f"case spec 'timing' must be 'bmo' or 'amc', got {data['timing']!r}")
    return CaseSpec(**{n: (list(data[n]) if n == "aliases" else data[n]) for n in names})


def load_case_spec(path) -> CaseSpec:
    return case_spec_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _who(spec: CaseSpec) -> str:
    return (f"{spec.company} (ticker {spec.ticker}), fiscal period {spec.fiscal_period}, "
            f"reported on {spec.report_date}")


def release_request(spec: CaseSpec) -> str:
    return (f"Find and fetch the official earnings results release issued by "
            f"{_who(spec)}. I need the company's own press release with the full text "
            f"and financial tables, from its investor-relations site or a wire service, "
            f"not news coverage of it.")


def transcript_request(spec: CaseSpec) -> str:
    return (f"Find and fetch the full earnings call transcript for {_who(spec)}. I need "
            f"the complete transcript with the prepared remarks and the Q&A, not a "
            f"summary or news coverage of the call.")


def expectations_request(spec: CaseSpec) -> str:
    return (f"Find and fetch pages stating the analyst consensus expectations for "
            f"{_who(spec)}, as they stood before the report: consensus EPS, revenue and "
            f"any key metrics analysts were watching. Prefer earnings previews published "
            f"before {spec.report_date}.")


def _first_document(fetch, agent, request: str, kind: str) -> Document | None:
    for page in fetch(request):
        span = select_span(agent, page, kind)
        if span is not None:
            return Document(kind, page.title, span, page.url)
    return None


def build_case(root, spec: CaseSpec, *, fetch, agent, stock_closes, market_closes) -> Case:
    """Fetch the documents, compute the outcome, save both, return the Case."""
    release = _first_document(fetch, agent, release_request(spec), "release")
    transcript = _first_document(fetch, agent, transcript_request(spec), "transcript")
    expectation_pages = fetch(expectations_request(spec))
    expectations = expectations_document(agent, expectation_pages, spec.company,
                                         spec.fiscal_period)

    missing = [name for name, doc in (("release", release), ("transcript", transcript))
               if doc is None]
    if missing:
        raise LookupError(f"{spec.case_id}: no {' and no '.join(missing)} found for "
                          f"{spec.company} {spec.fiscal_period}")

    event_date = event_date_for(date.fromisoformat(spec.report_date), spec.timing,
                                [d for d, _ in stock_closes])
    sources: list[str] = []
    used = [release.source, transcript.source]
    if expectations is not None:
        used.extend(page.url for page in expectation_pages)
    for url in used:
        if url and url not in sources:
            sources.append(url)
    outcome = outcome_from_closes(stock_closes, market_closes, event_date, sources=sources)

    documents = [release, transcript]
    if expectations is not None:
        documents.append(expectations)
    case = Case(case_id=spec.case_id, company=spec.company, ticker=spec.ticker,
                aliases=list(spec.aliases), fiscal_period=spec.fiscal_period,
                event_date=event_date.isoformat(), documents=documents)
    save_case(root, case, outcome)
    return case
