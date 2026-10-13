"""Acceptance tests for researchos.sources, written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "researchos.sources" section of SPEC.md. Fakes only.
"""

from __future__ import annotations

import json
import types
from datetime import date

import pytest

from researchos import cases, llm, sources

NS = types.SimpleNamespace


# --- prices and the outcome ---------------------------------------------------

def test_load_closes_sorts(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps([["2026-07-17", 11.0], ["2026-07-15", 10.0], ["2026-07-16", 10.5]]),
                 encoding="utf-8")
    assert sources.load_closes(p) == [(date(2026, 7, 15), 10.0), (date(2026, 7, 16), 10.5),
                                      (date(2026, 7, 17), 11.0)]


DAYS = [date(2026, 7, 16), date(2026, 7, 17), date(2026, 7, 20), date(2026, 7, 21)]


@pytest.mark.parametrize("report,timing,expected", [
    (date(2026, 7, 16), "amc", date(2026, 7, 17)),
    (date(2026, 7, 16), "bmo", date(2026, 7, 16)),
    (date(2026, 7, 17), "amc", date(2026, 7, 20)),     # over the weekend
    (date(2026, 7, 18), "bmo", date(2026, 7, 20)),     # a Saturday report
])
def test_event_date_for(report, timing, expected):
    assert sources.event_date_for(report, timing, DAYS) == expected


@pytest.mark.parametrize("report,timing", [
    (date(2026, 7, 16), "during"),
    (date(2026, 7, 21), "amc"),                         # no later trading day
])
def test_event_date_for_errors(report, timing):
    with pytest.raises(ValueError):
        sources.event_date_for(report, timing, DAYS)


STOCK = [(date(2026, 7, 16), 100.0), (date(2026, 7, 17), 92.0), (date(2026, 7, 20), 95.0)]
MARKET = [(date(2026, 7, 16), 500.0), (date(2026, 7, 17), 505.0), (date(2026, 7, 20), 506.0)]


def test_outcome_from_closes():
    o = sources.outcome_from_closes(STOCK, MARKET, date(2026, 7, 17), notes="n", sources=["u"])
    assert o.stock_return_1d == pytest.approx(-0.08)
    assert o.market_return_1d == pytest.approx(0.01)
    assert o.abnormal_return_1d == pytest.approx(-0.09)
    assert o.notes == "n" and o.sources == ["u"]


def test_outcome_from_closes_without_a_base_raises():
    with pytest.raises(ValueError):
        sources.outcome_from_closes(STOCK, MARKET, date(2026, 7, 16))


# --- fetch_pages --------------------------------------------------------------

def fetched(url, text, title="T", source_type="text"):
    return NS(type="web_fetch_tool_result", tool_use_id="srv",
              content=NS(type="web_fetch_result", url=url, retrieved_at="2026-10-01",
                         content=NS(type="document", title=title,
                                    source=NS(type=source_type, media_type="text/plain", data=text))))


def fetch_error(url):
    return NS(type="web_fetch_tool_result", tool_use_id="srv",
              content=NS(type="web_fetch_tool_result_error", error_code="url_not_allowed"))


def reply(blocks, stop="end_turn"):
    return NS(content=blocks, stop_reason=stop, stop_details=None,
              usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                       cache_creation_input_tokens=0))


class Client:
    def __init__(self, replies):
        self.replies = list(replies)
        self.kwargs = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.kwargs.append(json.loads(json.dumps(kwargs, default=lambda o: "<block>")))
        self.kwargs[-1]["_messages"] = kwargs["messages"]
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def test_fetch_pages_collects_text_pages_across_pause_turn():
    search = NS(type="web_search_tool_result", tool_use_id="s", content=[])
    first = reply([NS(type="server_tool_use", name="web_search", input={}), search,
                   fetched("https://a.example/1", "page one", "A"), fetch_error("https://x")],
                  stop="pause_turn")
    second = reply([fetched("https://a.example/1", "again"), fetched("https://b.example/2", "pdf", source_type="base64"),
                    fetched("https://c.example/3", "page three", ""), NS(type="text", text="done")])
    client = Client([first, second])
    pages = sources.fetch_pages(client, "find the release", max_searches=3, max_fetches=4)
    assert [(p.url, p.title, p.text) for p in pages] == [
        ("https://a.example/1", "A", "page one"), ("https://c.example/3", "", "page three")]
    assert len(client.kwargs) == 2
    kw = client.kwargs[0]
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "medium"}
    assert kw["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 4}]
    assert kw["system"] == sources.FETCH_SYSTEM
    for forbidden in ("thinking", "temperature", "top_p", "top_k", "tool_choice"):
        assert forbidden not in kw
    msgs = client.kwargs[1]["_messages"]
    assert msgs[0] == {"role": "user", "content": "find the release"}
    assert msgs[1]["role"] == "assistant" and list(msgs[1]["content"]) == list(first.content)


def test_fetch_pages_stops_after_max_continuations():
    client = Client([reply([], stop="pause_turn")])
    assert sources.fetch_pages(client, "r", max_continuations=2) == []
    assert len(client.kwargs) == 3


def test_fetch_pages_refusal():
    client = Client([reply([], stop="refusal")])
    with pytest.raises(llm.LLMError):
        sources.fetch_pages(client, "r")


# --- select_span ----------------------------------------------------------------

class Agent:
    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append((system, prompt, schema))
        return self.fn(system, prompt, schema)


PAGE = sources.FetchedPage("https://ir.example/q2", "Q2 results",
                           "Home | Investors | Menu\nACME reports second quarter results.\nRevenue rose.\n"
                           "| Table | end |\nRelated: Acme stock jumps")


def lines_agent(first, last, found=True):
    return Agent(lambda *a: {"found": found, "first_line": first, "last_line": last})


def test_select_span_returns_the_numbered_lines():
    agent = lines_agent(2, 4)
    assert sources.select_span(agent, PAGE, "release") == \
        "ACME reports second quarter results.\nRevenue rose.\n| Table | end |"
    system, prompt, schema = agent.calls[0]
    assert system == sources.SPAN_SYSTEM
    assert "release" in prompt and "Q2 results" in prompt
    assert "1: Home | Investors | Menu" in prompt and "5: Related: Acme stock jumps" in prompt
    assert schema["properties"].keys() == {"found", "first_line", "last_line"}
    assert schema["properties"]["first_line"]["type"] == "integer"
    assert schema["additionalProperties"] is False


def test_select_span_single_line_and_strip():
    page = sources.FetchedPage("u", "t", "a\n   body   \nb")
    assert sources.select_span(lines_agent(2, 2), page, "transcript") == "body"


@pytest.mark.parametrize("first,last,found", [
    (2, 4, False), (0, 3, True), (3, 2, True), (2, 6, True), (-1, 2, True),
])
def test_select_span_none(first, last, found):
    assert sources.select_span(lines_agent(first, last, found), PAGE, "release") is None


def test_select_span_blank_lines_give_none():
    page = sources.FetchedPage("u", "t", "a\n\n  \nb")
    assert sources.select_span(lines_agent(2, 3), page, "release") is None


# --- expectations ---------------------------------------------------------------

def test_expectations_document():
    agent = Agent(lambda *a: {"items": [
        {"metric": "EPS", "consensus": "$1.10", "source": "LSEG"},
        {"metric": "Revenue", "consensus": "$12.5B", "source": "FactSet"}]})
    pages = [sources.FetchedPage("u1", "t", "Analysts expect EPS of $1.10")]
    doc = sources.expectations_document(agent, pages, "Acme Corp", "Q2 2026")
    assert doc.kind == "expectations" and doc.source == ""
    assert doc.title == "Consensus expectations for Q2 2026"
    assert doc.text.splitlines() == ["- EPS: $1.10 (source: LSEG)",
                                     "- Revenue: $12.5B (source: FactSet)"]
    assert "Analysts expect EPS of $1.10" in agent.calls[0][1]
    assert sources.expectations_document(Agent(lambda *a: {"items": []}), pages, "A", "Q") is None


# --- build_case -----------------------------------------------------------------

RELEASE_PAGE = sources.FetchedPage("https://ir.acme.example/q2", "Acme Q2 2026 results",
                                   "nav\nnav\nRELEASE-START Acme results\nrevenue, margins and the financial tables\nRELEASE-END\nfooter")
STUB_PAGE = sources.FetchedPage("https://ir.acme.example/stub", "Acme Q2 (PDF)",
                                "menu\nSTUB Acme has released its results; see the PDF.\nfooter")
NEWS_PAGE = sources.FetchedPage("https://news.example/acme", "Acme stock falls", "Acme stock fell 8%.")
TRANSCRIPT_PAGE = sources.FetchedPage("https://t.example/acme-q2", "Acme Q2 call",
                                      "ads\nCALL-START Operator: welcome.\nCALL-END\ncomments")
CONSENSUS_PAGE = sources.FetchedPage("https://n.example/preview", "Preview", "Consensus EPS $1.10")

SPEC = sources.CaseSpec(case_id="acme-2026q2", company="Acme Corp", ticker="ACME",
                        aliases=["Acme"], fiscal_period="Q2 2026", report_date="2026-07-16",
                        timing="amc")


def make_fetch(transcript=True):
    requests = []

    def fetch(request):
        requests.append(request)
        low = request.lower()
        if "transcript" in low:
            return [TRANSCRIPT_PAGE] if transcript else []
        if "consensus" in low:
            return [CONSENSUS_PAGE]
        return [NEWS_PAGE, STUB_PAGE, RELEASE_PAGE]

    fetch.requests = requests
    return fetch


def span_answer(prompt):
    """What a sensible span agent says about whichever fake page is in the prompt."""
    if "RELEASE-START" in prompt:
        return {"found": True, "first_line": 3, "last_line": 5}
    if "STUB" in prompt:   # a stub the agent wrongly accepts: one line
        return {"found": True, "first_line": 2, "last_line": 2}
    if "CALL-START" in prompt:
        return {"found": True, "first_line": 2, "last_line": 3}
    return {"found": False, "first_line": 0, "last_line": 0}


def build_agent():
    def answer(system, prompt, schema):
        if "items" in schema["properties"]:
            return {"items": [{"metric": "EPS", "consensus": "$1.10", "source": "LSEG"}]}
        return span_answer(prompt)
    return Agent(answer)


def test_build_case(tmp_path):
    fetch = make_fetch()
    case = sources.build_case(tmp_path, SPEC, fetch=fetch, agent=build_agent(),
                              stock_closes=STOCK, market_closes=MARKET)
    release_req, transcript_req, consensus_req = fetch.requests
    for req in fetch.requests:
        assert "Acme Corp" in req and "ACME" in req and "Q2 2026" in req and "2026-07-16" in req
    assert "release" in release_req.lower() and "transcript" not in release_req.lower()
    assert "consensus" not in release_req.lower()
    assert "transcript" in transcript_req.lower()
    assert "consensus" in consensus_req.lower() and "transcript" not in consensus_req.lower()
    assert [d.kind for d in case.documents] == ["release", "transcript", "expectations"]
    assert case.documents[0].text == "RELEASE-START Acme results\nrevenue, margins and the financial tables\nRELEASE-END"  # not the stub
    assert case.documents[0].source == RELEASE_PAGE.url and case.documents[0].title == RELEASE_PAGE.title
    assert case.documents[1].text == "CALL-START Operator: welcome.\nCALL-END"
    assert case.event_date == "2026-07-17"
    assert (case.case_id, case.company, case.ticker, case.aliases, case.fiscal_period) == \
        ("acme-2026q2", "Acme Corp", "ACME", ["Acme"], "Q2 2026")
    loaded = cases.load_case(tmp_path, "acme-2026q2")
    assert loaded == case
    outcome = cases.load_outcome(tmp_path, "acme-2026q2")
    assert outcome.abnormal_return_1d == pytest.approx(-0.09)
    assert RELEASE_PAGE.url in outcome.sources and TRANSCRIPT_PAGE.url in outcome.sources


def test_build_case_without_a_transcript(tmp_path):
    with pytest.raises(LookupError, match="transcript"):
        sources.build_case(tmp_path, SPEC, fetch=make_fetch(transcript=False), agent=build_agent(),
                           stock_closes=STOCK, market_closes=MARKET)
    assert cases.list_cases(tmp_path) == []


# --- cli ------------------------------------------------------------------------

class ScriptedClient:
    """Serves both fetch requests (tools present) and agent requests (json_schema output)."""

    def __init__(self):
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        if "tools" in kwargs:
            request = kwargs["messages"][0]["content"].lower()
            page = (TRANSCRIPT_PAGE if "transcript" in request
                    else CONSENSUS_PAGE if "consensus" in request else RELEASE_PAGE)
            return reply([fetched(page.url, page.text, page.title)])
        schema = kwargs["output_config"]["format"]["schema"]
        prompt = kwargs["messages"][0]["content"][-1]["text"]
        body = {"items": []} if "items" in schema["properties"] else span_answer(prompt)
        return reply([NS(type="text", text=json.dumps(body))])


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_cli_build(tmp_path, capsys):
    from researchos.__main__ import main
    spec = write(tmp_path / "spec.json", {
        "case_id": "acme-2026q2", "company": "Acme Corp", "ticker": "ACME", "aliases": ["Acme"],
        "fiscal_period": "Q2 2026", "report_date": "2026-07-16", "timing": "amc"})
    closes = write(tmp_path / "s.json", [[d.isoformat(), c] for d, c in STOCK])
    market = write(tmp_path / "m.json", [[d.isoformat(), c] for d, c in MARKET])
    root = tmp_path / "cases"
    code = main(["build", "--root", str(root), "--spec", spec, "--closes", closes,
                 "--market-closes", market], client_factory=ScriptedClient)
    assert code == 0
    out = capsys.readouterr().out
    assert "acme-2026q2" in out
    assert [d.kind for d in cases.load_case(root, "acme-2026q2").documents] == ["release", "transcript"]


def test_cli_build_missing_file(tmp_path, capsys):
    from researchos.__main__ import main
    code = main(["build", "--root", str(tmp_path), "--spec", str(tmp_path / "nope.json"),
                 "--closes", str(tmp_path / "x.json"), "--market-closes", str(tmp_path / "y.json")],
                client_factory=ScriptedClient)
    assert code == 1 and capsys.readouterr().err.strip()
