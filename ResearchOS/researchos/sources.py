"""Build real cases: documents from the web, the outcome from daily closes.

Documents are found and fetched through the Claude API's server-side web
tools (``fetch_pages``) or, when their addresses are already known, fetched
directly with one GET each (``url_fetcher``, ``get_page``, ``html_to_text``);
an agent cuts the document proper out of each page (``select_span``) and
extracts the pre-report consensus (``expectations_document``). Prices are
never fetched here: they arrive as JSON files of ``[["YYYY-MM-DD", close], ...]``.
"""

from __future__ import annotations

import http.client
import logging
import re
import urllib.request
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, fields
from datetime import date
from html.parser import HTMLParser

from tools.returns import abnormal_return, event_return

from researchos.cases import Case, Document, Outcome, save_case
from researchos.jsonio import read_json
from researchos.llm import BETAS, DEFAULT_MODEL, LLMError, _get, _is_api_error

TIMINGS = ("bmo", "amc")

log = logging.getLogger("researchos")


# ---- prices and the outcome -------------------------------------------------------

def load_closes(path) -> list[tuple[date, float]]:
    """Read ``[["2026-07-16", 1234.5], ...]`` from a JSON file, sorted by date."""
    data = read_json(path)
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


# ---- fetching pages by address --------------------------------------------------------

# Elements whose contents are never shown.
_HIDDEN_TAGS = frozenset({"script", "style", "noscript", "head", "svg", "template"})
# What the head may hold: any other start tag there means it ended without its end tag.
_HEAD_CONTENT = frozenset({"title", "meta", "link", "base", "style", "script", "noscript",
                           "template"})
# Each starts a new line, and so does its end tag (what follows is on a line of its own).
_LINE_TAGS = frozenset({"p", "div", "br", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5",
                        "h6", "section", "article", "header", "footer", "pre", "blockquote",
                        "ul", "ol", "dl", "dt", "dd", "hr"})
_CELL_TAGS = frozenset({"td", "th"})
_CELL_SEPARATOR = " | "
# Every character str.splitlines() breaks a line at. In a page's source outside <pre> such
# a break is only a space; inside <pre> it is a new line. Either way none is left within a
# line, so select_span numbers exactly the lines of the text.
_LINE_BREAKS = re.compile(
    "\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]")
_BLANKS = re.compile("[ \t\xa0]+")


class _TextParser(HTMLParser):
    """Collects a page's first <title> and its visible text, broken into lines."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.title_seen = False  # the page's <title> has ended
        self.in_title = False
        self.capture_title = False
        self.hidden: list[str] = []  # open hidden elements, innermost last
        self.pre = 0  # open <pre> elements
        # Per open table, the cells so far in its current row (None between rows).
        self.rows: list[int | None] = []

    def handle_starttag(self, tag, attrs):
        if self.hidden and self.hidden[-1] == "head" and tag not in _HEAD_CONTENT:
            self.hidden.pop()  # the head ended without its end tag
        elif tag == "body" and "head" in self.hidden:
            del self.hidden[self.hidden.index("head"):]
        if tag == "title":
            self.in_title = True
            # An SVG's <title> is a tooltip, not the page's.
            self.capture_title = not (self.title_seen or "svg" in self.hidden
                                      or "template" in self.hidden)
            return
        if tag in _HIDDEN_TAGS:
            self.hidden.append(tag)
            return
        if self.hidden:
            return
        if tag in _LINE_TAGS:
            self.parts.append("\n")
        if tag == "pre":
            self.pre += 1
        elif tag == "table":
            self.rows.append(None)
        elif tag == "tr":
            if not self.rows:  # a row outside any table
                self.rows.append(None)
            self.rows[-1] = 0
        elif tag in _CELL_TAGS:
            if not self.rows:  # a cell outside any table
                self.rows.append(None)
            cells = self.rows[-1] or 0
            if cells:
                self.parts.append(_CELL_SEPARATOR)
            self.rows[-1] = cells + 1

    def handle_endtag(self, tag):
        if tag == "title":
            if self.in_title:
                self.in_title = False
                self.title_seen = self.title_seen or self.capture_title
                self.capture_title = False
            return
        if tag in _HIDDEN_TAGS:
            if tag in self.hidden:  # close it, and whatever was left open inside it
                del self.hidden[len(self.hidden) - 1 - self.hidden[::-1].index(tag):]
            return
        if self.hidden:
            return
        if tag in _LINE_TAGS:
            self.parts.append("\n")
        if tag == "pre":
            self.pre = max(self.pre - 1, 0)
        elif tag == "table":
            if self.rows:
                self.rows.pop()
        elif tag == "tr":
            if self.rows:
                self.rows[-1] = None

    def handle_data(self, data):
        if self.in_title:
            if self.capture_title:
                self.title.append(data)
            return
        if not self.hidden:
            self.parts.append(_LINE_BREAKS.sub("\n" if self.pre else " ", data))


def html_to_text(html: str) -> tuple[str, str]:
    """``(title, text)`` of an HTML page, using only ``html.parser``.

    The title is the text of the page's <title>, whitespace collapsed ("" when
    there is none). The text is the visible text: script, style, noscript,
    head, svg and template are dropped; block elements (p, div, br, li, tr,
    table, h1-h6, ...) start new lines; the cells of a table row are joined by
    " | "; character references are decoded; runs of spaces, tabs and
    non-breaking spaces become one space; lines are stripped, empty ones dropped.
    """
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    title = " ".join("".join(parser.title).split())
    lines = (_BLANKS.sub(" ", line).strip() for line in "".join(parser.parts).split("\n"))
    return title, "\n".join(line for line in lines if line)


_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


def _urlopen(url: str, headers: dict) -> tuple[bytes, str]:
    """The default transport: one GET through urllib; the body and its Content-Type."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=30) as response:
            return response.read(), response.headers.get("Content-Type") or ""
    except OSError:  # urllib's HTTP and URL errors, timeouts, refused connections
        raise
    except http.client.HTTPException as exc:
        # A garbled status line or a body cut short: a network failure like the
        # OSErrors above, so it is reported as one.
        raise OSError(f"{url}: {exc!r}") from exc


def _parse_content_type(content_type: str) -> tuple[str, str]:
    """The media type (lower case) and the charset ("" when none) of a Content-Type."""
    media_type, *params = content_type.split(";")
    charset = ""
    for param in params:
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset" and not charset:
            charset = value.strip().strip("\"'").strip()
    return media_type.strip().lower(), charset


def _decode(body: bytes, charset: str) -> str:
    try:
        text = body.decode(charset or "utf-8", errors="replace")
    except LookupError:  # a charset Python does not know: read it as UTF-8
        text = body.decode("utf-8", errors="replace")
    return text.removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}")  # a byte order mark is not text


def get_page(url: str, *, user_agent: str, transport=None) -> FetchedPage:
    """One GET of ``url`` with ``User-Agent: <user_agent>``; the page it returns.

    ``transport(url, headers) -> (body, content_type)`` defaults to urllib. HTML
    goes through ``html_to_text``; plain text is kept as it is; any other content
    type (a PDF, an image) raises ValueError. The transport's errors (OSError,
    which includes urllib's HTTP and URL errors) propagate.
    """
    body, content_type = (transport or _urlopen)(url, {"User-Agent": user_agent})
    media_type, charset = _parse_content_type(content_type or "")
    if media_type and media_type not in _HTML_TYPES and media_type != "text/plain":
        raise ValueError(f"{url}: cannot read content type {content_type!r}; only HTML and "
                         f"plain text pages are read")
    text = _decode(body, charset)
    if media_type == "text/plain":
        return FetchedPage(url, "", text)
    if not media_type and not text.lstrip().startswith("<"):
        raise ValueError(f"{url}: no content type, and the body is not HTML")
    return FetchedPage(url, *html_to_text(text))


URL_KINDS = ("release", "transcript", "expectations")


def _kind_of_request(request: str) -> str:
    """Which document a build_case request asks for (see release_request and the others)."""
    low = request.lower()
    if "transcript" in low:
        return "transcript"
    if "consensus" in low:
        return "expectations"
    return "release"


def url_fetcher(urls, *, user_agent: str, transport=None):
    """A ``fetch(request) -> list[FetchedPage]`` for build_case that GETs known addresses.

    ``urls`` maps "release", "transcript" and "expectations" to lists of
    addresses; a kind with none gives ``[]``. An address whose fetch raises
    OSError or ValueError is left out, with a warning naming it, so one dead
    link does not stop the case.
    """
    addresses = {kind: list(urls.get(kind) or ()) for kind in URL_KINDS}

    def fetch(request: str) -> list[FetchedPage]:
        pages: list[FetchedPage] = []
        for url in addresses[_kind_of_request(request)]:
            try:
                pages.append(get_page(url, user_agent=user_agent, transport=transport))
            except (OSError, ValueError) as exc:
                log.warning("left out %s: %s", url, exc)
        return pages

    return fetch


# ---- cutting the document out of the page --------------------------------------------

SPAN_SYSTEM = """\
You locate one document inside a web page that also carries navigation, \
menus, ads, cookie notices, related links and other clutter. The page is \
given with its lines numbered from 1, each line written as "N: text".

Find the document proper and report the number of its first line and of its \
last line, read off the numbered page.

For a results release: from its first line (headline or dateline) to the \
last line of its financial tables. Releases run far past their highlights: \
the narrative, the outlook and then the income statement, balance sheet, \
cash flow and segment tables follow, so do not stop at the end of the \
highlights or the first table; the last line is the last line of the last \
financial table.

For an earnings call transcript: from the opening of the call (the operator \
or the first speaker) to the end of the closing remarks, prepared remarks and \
Q&A included, without any commentary about the share price or how the stock \
traded and without any editorial introduction or wrap-up by the publisher.

A page that only announces or links to the document (for example a stub \
pointing to a PDF, a news story about the results, or a teaser of a \
paywalled page) does not contain it: set found to false and both line \
numbers to 0."""

SPAN_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "first_line": {"type": "integer"},
        "last_line": {"type": "integer"},
    },
    "required": ["found", "first_line", "last_line"],
    "additionalProperties": False,
}

_KIND_LABELS = {"release": "earnings results release",
                "transcript": "earnings call transcript"}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def select_span(agent, page: FetchedPage, kind: str) -> str | None:
    """Cut the ``kind`` document out of ``page``; None if it is not there."""
    lines = page.text.splitlines()
    label = _KIND_LABELS.get(kind, kind)
    numbered = "\n".join(f"{n}: {line}" for n, line in enumerate(lines, 1))
    prompt = (f"Document kind: {kind} ({label})\n"
              f"Page title: {page.title}\n\n"
              f"Page ({len(lines)} numbered lines):\n{numbered}")
    answer = agent.json(SPAN_SYSTEM, prompt, SPAN_SCHEMA)
    if not answer.get("found"):
        return None
    first = answer.get("first_line")
    last = answer.get("last_line")
    if not _is_int(first) or not _is_int(last):
        return None
    if not 1 <= first <= last <= len(lines):
        return None
    span = "\n".join(lines[first - 1:last]).strip()
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
    return case_spec_from_dict(read_json(path))


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


def _longest_document(fetch, agent, request: str, kind: str) -> Document | None:
    """The longest ``kind`` span over every fetched page (earliest among equals).

    Taking the first span found would let a stub that only links to the
    document stand in for it.
    """
    best: Document | None = None
    for page in fetch(request):
        span = select_span(agent, page, kind)
        if span is not None and (best is None or len(span) > len(best.text)):
            best = Document(kind, page.title, span, page.url)
    return best


def build_case(root, spec: CaseSpec, *, fetch, agent, stock_closes, market_closes) -> Case:
    """Fetch the documents, compute the outcome, save both, return the Case."""
    release = _longest_document(fetch, agent, release_request(spec), "release")
    transcript = _longest_document(fetch, agent, transcript_request(spec), "transcript")
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
