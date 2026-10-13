import json
from datetime import date
from types import SimpleNamespace

import pytest

from conftest import FakeClient, FakeLLM, text_response

from researchos.__main__ import main
from researchos.cases import list_cases, load_case, load_outcome
from researchos.llm import LLMError
from researchos.sources import (
    EXPECT_SCHEMA,
    FETCH_SYSTEM,
    SPAN_SCHEMA,
    SPAN_SYSTEM,
    CaseSpec,
    FetchedPage,
    build_case,
    event_date_for,
    expectations_document,
    fetch_pages,
    load_closes,
    outcome_from_closes,
    select_span,
)

D = date.fromisoformat


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---- prices and the outcome

def test_load_closes_sorted(tmp_path):
    path = write_json(tmp_path / "c.json",
                      [["2026-07-17", 11], ["2026-07-15", 10.0], ["2026-07-16", 10.5]])
    assert load_closes(path) == [(D("2026-07-15"), 10.0), (D("2026-07-16"), 10.5),
                                 (D("2026-07-17"), 11.0)]


def test_load_closes_rejects_garbage(tmp_path):
    with pytest.raises(ValueError):
        load_closes(write_json(tmp_path / "a.json", {"x": 1}))
    with pytest.raises(ValueError):
        load_closes(write_json(tmp_path / "b.json", [["2026-07-17"]]))
    with pytest.raises(ValueError):
        load_closes(write_json(tmp_path / "c.json", [["not a date", 1.0]]))


DAYS = [D("2026-07-14"), D("2026-07-15"), D("2026-07-17")]  # 16th is a holiday


def test_event_date_bmo_and_amc():
    assert event_date_for(D("2026-07-15"), "bmo", DAYS) == D("2026-07-15")
    assert event_date_for(D("2026-07-16"), "bmo", DAYS) == D("2026-07-17")
    assert event_date_for(D("2026-07-14"), "amc", DAYS) == D("2026-07-15")
    assert event_date_for(D("2026-07-15"), "amc", DAYS) == D("2026-07-17")
    assert event_date_for(D("2026-07-15"), "amc", list(reversed(DAYS))) == D("2026-07-17")


def test_event_date_errors():
    with pytest.raises(ValueError):
        event_date_for(D("2026-07-15"), "during", DAYS)
    with pytest.raises(ValueError):
        event_date_for(D("2026-07-17"), "amc", DAYS)
    with pytest.raises(ValueError):
        event_date_for(D("2026-07-18"), "bmo", DAYS)
    with pytest.raises(ValueError):
        event_date_for(D("2026-07-15"), "bmo", [])


STOCK = [(D("2026-07-14"), 100.0), (D("2026-07-15"), 110.0), (D("2026-07-17"), 99.0)]
MARKET = [(D("2026-07-14"), 1000.0), (D("2026-07-15"), 1010.0), (D("2026-07-17"), 1000.0)]


def test_outcome_from_closes():
    out = outcome_from_closes(STOCK, MARKET, D("2026-07-15"), notes="n", sources=("u1",))
    assert out.stock_return_1d == pytest.approx(0.10)
    assert out.market_return_1d == pytest.approx(0.01)
    assert out.abnormal_return_1d == pytest.approx(0.09)
    assert out.notes == "n" and out.sources == ["u1"]


def test_outcome_from_closes_missing_data():
    with pytest.raises(ValueError):
        outcome_from_closes(STOCK, MARKET, D("2026-07-14"))  # no close before
    with pytest.raises(ValueError):
        outcome_from_closes(STOCK, MARKET[:1], D("2026-07-15"))  # market lacks the session


# ---- fetching pages

def fetch_block(url, text, title="T", source_type="text"):
    source = SimpleNamespace(type=source_type, data=text, media_type="text/plain")
    doc = SimpleNamespace(type="document", title=title, source=source)
    return SimpleNamespace(type="web_fetch_tool_result", tool_use_id="t",
                           content=SimpleNamespace(type="web_fetch_result", url=url,
                                                   content=doc, retrieved_at=None))


def response(blocks, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(stop_reason=stop_reason, stop_details=stop_details,
                           content=blocks, usage=None)


def test_fetch_pages_request_and_collection():
    noise = [
        SimpleNamespace(type="server_tool_use", id="s", name="web_search", input={}),
        SimpleNamespace(type="web_search_tool_result", tool_use_id="s", content=[]),
        SimpleNamespace(type="web_fetch_tool_result", tool_use_id="e",
                        content=SimpleNamespace(type="web_fetch_tool_error",
                                                error_code="url_not_accessible")),
        fetch_block("https://x/pdf", "JVBERi0=", source_type="base64"),
        SimpleNamespace(type="text", text="done"),
    ]
    blocks = [fetch_block("https://x/a", "A text", title=None), *noise,
              fetch_block("https://x/b", "B text", title="B")]
    client = FakeClient(lambda kw: response(blocks))
    pages = fetch_pages(client, "find the release", max_searches=2, max_fetches=3)
    assert pages == [FetchedPage("https://x/a", "", "A text"),
                     FetchedPage("https://x/b", "B", "B text")]
    (req,) = client.requests
    assert req["model"] == "claude-opus-5-5" and req["max_tokens"] == 16000
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["fallbacks"] == "default"
    assert req["output_config"] == {"effort": "medium"}
    assert req["system"] == FETCH_SYSTEM
    assert req["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 2},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}]
    assert req["messages"] == [{"role": "user", "content": "find the release"}]
    for key in ("thinking", "temperature", "top_p", "top_k", "tool_choice"):
        assert key not in req


def test_fetch_pages_resumes_pause_turn_and_dedupes():
    first = [fetch_block("https://x/a", "A")]
    second = [fetch_block("https://x/a", "A again"), fetch_block("https://x/b", "B")]
    queue = [response(first, "pause_turn"), response(second, "end_turn")]
    client = FakeClient(lambda kw: queue.pop(0))
    pages = fetch_pages(client, "req")
    assert [(p.url, p.text) for p in pages] == [("https://x/a", "A"), ("https://x/b", "B")]
    assert len(client.requests) == 2
    assert client.requests[0]["messages"] == [{"role": "user", "content": "req"}]
    assert client.requests[1]["messages"] == [{"role": "user", "content": "req"},
                                              {"role": "assistant", "content": first}]

    def rest(r):
        return {k: v for k, v in r.items() if k != "messages"}
    assert rest(client.requests[0]) == rest(client.requests[1])


def test_fetch_pages_caps_continuations():
    n = iter(range(100))
    client = FakeClient(lambda kw: response([fetch_block(f"https://x/{next(n)}", "t")],
                                            "pause_turn"))
    pages = fetch_pages(client, "req", max_continuations=2)
    assert len(client.requests) == 3
    assert [p.url for p in pages] == ["https://x/0", "https://x/1", "https://x/2"]


def test_fetch_pages_refusal():
    client = FakeClient(lambda kw: response([], "refusal",
                                            SimpleNamespace(category="cyber")))
    with pytest.raises(LLMError, match="cyber"):
        fetch_pages(client, "req")
    client = FakeClient(lambda kw: response([], "refusal", None))
    with pytest.raises(LLMError):
        fetch_pages(client, "req")


# ---- select_span

PAGE_TEXT = ("Home | Investors | Menu\n"
             "  Acme reports Q2 2026 results.\n"
             "Revenue rose 10%.\n"
             "Table: revenue 100 net income 10  \n"
             "Related: shares jump!")
PAGE = FetchedPage("https://acme/ir", "Acme Q2", PAGE_TEXT)


def span_answer(first_line, last_line, found=True):
    return {"found": found, "first_line": first_line, "last_line": last_line}


def span_of(text, first_line, last_line):
    agent = FakeLLM(responses=[span_answer(first_line, last_line)])
    return select_span(agent, FetchedPage("u", "t", text), "release")


def test_select_span_cuts_lines():
    agent = FakeLLM(responses=[span_answer(2, 4)])
    span = select_span(agent, PAGE, "release")
    assert span == ("Acme reports Q2 2026 results.\n"
                    "Revenue rose 10%.\n"
                    "Table: revenue 100 net income 10")
    (call,) = agent.calls
    assert call["system"] == SPAN_SYSTEM
    assert call["schema"] is SPAN_SCHEMA and call["prefix"] is None
    assert "release" in call["prompt"] and "Acme Q2" in call["prompt"]


def test_select_span_prompt_numbers_every_line():
    agent = FakeLLM(responses=[span_answer(0, 0, found=False)])
    select_span(agent, PAGE, "transcript")
    prompt = agent.calls[0]["prompt"]
    assert "transcript" in prompt
    numbered = "\n".join(f"{n}: {line}" for n, line in enumerate(PAGE_TEXT.splitlines(), 1))
    assert numbered in prompt
    assert "1: Home | Investors | Menu\n2:   Acme reports Q2 2026 results." in prompt
    assert "5: Related: shares jump!" in prompt


def test_select_span_bounds_are_inclusive():
    assert span_of(PAGE_TEXT, 1, 1) == "Home | Investors | Menu"
    assert span_of(PAGE_TEXT, 5, 5) == "Related: shares jump!"
    assert span_of(PAGE_TEXT, 1, 5) == PAGE_TEXT.strip()
    assert span_of(PAGE_TEXT, 3, 3) == "Revenue rose 10%."


def test_select_span_splits_any_line_ending():
    text = "nav\r\nline one\r\nline two\rline three\nfooter"
    assert span_of(text, 2, 4) == "line one\nline two\nline three"


def test_select_span_none_cases():
    for answer in [
        span_answer(2, 4, found=False),
        span_answer(0, 4),  # lines start at 1
        span_answer(-1, 2),
        span_answer(2, 6),  # past the last line
        span_answer(6, 6),
        span_answer(4, 2),  # reversed
        span_answer(0, 0),
        span_answer(2.0, 4),  # not integers
        span_answer("2", "4"),
        span_answer(True, True),
        {"found": True},
    ]:
        assert select_span(FakeLLM(responses=[answer]), PAGE, "transcript") is None, answer


def test_select_span_blank_span_and_empty_page():
    assert span_of("text\n   \n\t\nmore", 2, 3) is None
    assert span_of("", 1, 1) is None


def test_schemas_are_strict():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert not isinstance(node.get("type"), list) and "anyOf" not in node
            for value in node.values():
                walk(value)
    walk(SPAN_SCHEMA)
    walk(EXPECT_SCHEMA)


# ---- expectations

def test_expectations_document():
    pages = [FetchedPage("https://p/1", "Preview", "Analysts expect EPS of $1.10."),
             FetchedPage("https://p/2", "Preview 2", "Revenue seen at $5.2B.")]
    agent = FakeLLM(responses=[{"items": [
        {"metric": "EPS", "consensus": "$1.10", "source": "Zacks"},
        {"metric": "Revenue", "consensus": "$5.2B", "source": "LSEG"}]}])
    doc = expectations_document(agent, pages, "Acme", "Q2 2026")
    assert doc.kind == "expectations"
    assert doc.title == "Consensus expectations for Q2 2026"
    assert doc.source == ""
    assert doc.text == ("- EPS: $1.10 (source: Zacks)\n"
                        "- Revenue: $5.2B (source: LSEG)")
    (call,) = agent.calls
    assert call["schema"] is EXPECT_SCHEMA
    assert "Analysts expect EPS of $1.10." in call["prompt"]
    assert "Revenue seen at $5.2B." in call["prompt"]


def test_expectations_document_none():
    agent = FakeLLM(responses=[{"items": []}])
    assert expectations_document(agent, [FetchedPage("u", "t", "x")], "Acme", "Q2") is None
    assert len(agent.calls) == 1
    assert expectations_document(agent, [], "Acme", "Q2") is None
    assert len(agent.calls) == 1  # no pages: nothing to ask about


# ---- build_case

SPEC = CaseSpec(case_id="acme-q2", company="Acme Widgets Inc.", ticker="ACMW",
                aliases=["Acme", "Jane Roe"], fiscal_period="Q2 2026",
                report_date="2026-07-15", timing="bmo")

# A stub that only links to the release comes first and does yield a (short)
# span; the full release further down must win.
STUB_RELEASE = FetchedPage("https://acme/ir/stub", "Acme Q2 2026 results",
                           "menu\nSTART Acme Q2 2026 results: see the PDF END\nfooter")
FULL_RELEASE = FetchedPage("https://acme/ir/q2", "Acme Q2 2026 full release",
                           "nav\nnav\nSTART Acme reports Q2 2026\nrevenue 100\n"
                           "net income 10\ntotal assets 900 END\nfooter")
RELEASE_PAGES = [STUB_RELEASE,
                 FetchedPage("https://news/story", "Story", "A news story about results."),
                 FULL_RELEASE]
FULL_RELEASE_TEXT = "START Acme reports Q2 2026\nrevenue 100\nnet income 10\ntotal assets 900 END"
TRANSCRIPT_PAGES = [FetchedPage("https://t/acmw", "ACMW call",
                                "ads\nSTART Operator: welcome\nCEO: we did well END\nads")]
EXPECT_PAGES = [FetchedPage("https://preview/acmw", "Preview", "EPS seen at $1.10")]
EPS_ITEM = {"metric": "EPS", "consensus": "$1.10", "source": "Zacks"}


def route(request):
    low = request.lower()
    if "transcript" in low:
        return "transcript"
    if "consensus" in low:
        return "expectations"
    return "release"


class FakeFetch:
    def __init__(self, release=RELEASE_PAGES, transcript=TRANSCRIPT_PAGES,
                 expectations=EXPECT_PAGES):
        self.pages = {"release": release, "transcript": transcript,
                      "expectations": expectations}
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.pages[route(request)]


def agent_answer(prompt, schema, items=(EPS_ITEM,)):
    """Expectations items, or the numbered lines from START to END of the page."""
    if "items" in schema["properties"]:
        return {"items": list(items)}
    first = last = 0
    for line in prompt.splitlines():
        number, _, text = line.partition(": ")
        if not number.isdigit():
            continue
        if "START" in text and not first:
            first = int(number)
        if "END" in text:
            last = int(number)
    if first and last:
        return span_answer(first, last)
    return span_answer(0, 0, found=False)


def build_agent(items=(EPS_ITEM,)):
    return FakeLLM(lambda system, prompt, schema, prefix: agent_answer(prompt, schema, items))


def test_build_case(tmp_path):
    fetch = FakeFetch()
    agent = build_agent()
    case = build_case(tmp_path, SPEC, fetch=fetch, agent=agent,
                      stock_closes=STOCK, market_closes=MARKET)
    assert [d.kind for d in case.documents] == ["release", "transcript", "expectations"]
    release, transcript, expectations = case.documents
    assert release.text == FULL_RELEASE_TEXT
    assert release.title == "Acme Q2 2026 full release"
    assert release.source == "https://acme/ir/q2"
    assert transcript.text == "START Operator: welcome\nCEO: we did well END"
    assert transcript.source == "https://t/acmw"
    # select_span ran on every fetched page: 3 release, 1 transcript, + expectations.
    span_calls = [c for c in agent.calls if c["schema"] is SPAN_SCHEMA]
    assert len(span_calls) == len(RELEASE_PAGES) + len(TRANSCRIPT_PAGES)
    assert len(agent.calls) == len(span_calls) + 1
    assert expectations.text == "- EPS: $1.10 (source: Zacks)"
    assert case.event_date == "2026-07-15"
    assert case.aliases == ["Acme", "Jane Roe"] and case.ticker == "ACMW"

    assert load_case(tmp_path, "acme-q2") == case
    outcome = load_outcome(tmp_path, "acme-q2")
    assert outcome.abnormal_return_1d == pytest.approx(0.09)
    assert outcome.sources == ["https://acme/ir/q2", "https://t/acmw", "https://preview/acmw"]

    assert len(fetch.requests) == 3
    for r in fetch.requests:
        for part in ("Acme Widgets Inc.", "ACMW", "Q2 2026", "2026-07-15"):
            assert part in r
    rel, tra, exp = (r.lower() for r in fetch.requests)
    assert "release" in rel and "transcript" not in rel and "consensus" not in rel
    assert "transcript" in tra and "release" not in tra and "consensus" not in tra
    assert "consensus" in exp and "transcript" not in exp and "release" not in exp


def test_build_case_keeps_longest_span_earliest_among_equals(tmp_path):
    # Longer than STUB_RELEASE's span, shorter than FULL_RELEASE's.
    later_longer = FetchedPage("https://acme/ir/later", "Later",
                               "x\nSTART a longer release text with its financial tables END")
    stub_span = "START Acme Q2 2026 results: see the PDF END"
    assert len(stub_span) < len("START a longer release text with its financial tables END") \
        < len(FULL_RELEASE_TEXT)
    same_a = FetchedPage("https://acme/a", "A", "START same END\nfooter")
    same_b = FetchedPage("https://acme/b", "B", "nav\nSTART same END")
    fetch = FakeFetch(release=[FULL_RELEASE, later_longer],
                      transcript=[same_a, same_b])
    case = build_case(tmp_path, SPEC, fetch=fetch, agent=build_agent(),
                      stock_closes=STOCK, market_closes=MARKET)
    release, transcript, _ = case.documents
    assert release.source == "https://acme/ir/q2" and release.text == FULL_RELEASE_TEXT
    assert transcript.source == "https://acme/a" and transcript.text == "START same END"

    fetch = FakeFetch(release=[STUB_RELEASE, later_longer])
    case = build_case(tmp_path / "2", SPEC, fetch=fetch, agent=build_agent(),
                      stock_closes=STOCK, market_closes=MARKET)
    assert case.documents[0].source == "https://acme/ir/later"


def test_build_case_amc_without_expectations(tmp_path):
    spec = CaseSpec(**{**SPEC.__dict__, "timing": "amc"})
    case = build_case(tmp_path, spec, fetch=FakeFetch(), agent=build_agent(items=()),
                      stock_closes=STOCK, market_closes=MARKET)
    assert [d.kind for d in case.documents] == ["release", "transcript"]
    assert case.event_date == "2026-07-17"
    outcome = load_outcome(tmp_path, "acme-q2")
    assert outcome.stock_return_1d == pytest.approx(99 / 110 - 1)
    assert outcome.sources == ["https://acme/ir/q2", "https://t/acmw"]


@pytest.mark.parametrize("missing", ["release", "transcript"])
def test_build_case_missing_document_writes_nothing(tmp_path, missing):
    fetch = FakeFetch(**{missing: [FetchedPage("https://n", "n", "nothing useful")]})
    with pytest.raises(LookupError, match=missing):
        build_case(tmp_path / "root", SPEC, fetch=fetch, agent=build_agent(),
                   stock_closes=STOCK, market_closes=MARKET)
    assert not (tmp_path / "root").exists()


def test_build_case_bad_prices_write_nothing(tmp_path):
    with pytest.raises(ValueError):
        build_case(tmp_path / "root", SPEC, fetch=FakeFetch(), agent=build_agent(),
                   stock_closes=STOCK, market_closes=MARKET[:1])
    assert list_cases(tmp_path / "root") == []


# ---- CLI

def cli_files(tmp_path, spec=None):
    spec_path = write_json(tmp_path / "spec.json", spec or SPEC.__dict__)
    closes = write_json(tmp_path / "closes.json", [[d.isoformat(), c] for d, c in STOCK])
    market = write_json(tmp_path / "market.json", [[d.isoformat(), c] for d, c in MARKET])
    return ["build", "--root", str(tmp_path / "root"), "--spec", str(spec_path),
            "--closes", str(closes), "--market-closes", str(market)]


def cli_client():
    """A fake client serving both the web-fetch calls and the structured-output calls."""
    first = {"release": FULL_RELEASE, "transcript": TRANSCRIPT_PAGES[0],
             "expectations": EXPECT_PAGES[0]}

    def responder(kw):
        if "tools" in kw:
            page = first[route(kw["messages"][0]["content"])]
            return response([fetch_block(page.url, page.text, page.title)])
        schema = kw["output_config"]["format"]["schema"]
        prompt = kw["messages"][0]["content"][-1]["text"]
        return text_response(json.dumps(agent_answer(prompt, schema)))

    return FakeClient(responder)


def test_cli_build(tmp_path, capsys):
    client = cli_client()
    code = main(cli_files(tmp_path), client_factory=lambda: client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    out = json.loads(captured.out)
    assert out["case_id"] == "acme-q2"
    assert out["outcome"]["abnormal_return_1d"] == pytest.approx(0.09)
    assert load_case(tmp_path / "root", "acme-q2").event_date == "2026-07-15"
    structured = [r for r in client.requests if "tools" not in r]
    assert structured and all(r["output_config"]["effort"] == "medium" for r in structured)
    assert sum("tools" in r for r in client.requests) == 3


def test_cli_build_bad_arguments(tmp_path, capsys):
    args = cli_files(tmp_path)
    assert main(args[:-2]) == 2  # no --market-closes
    assert main(["build", "--root", "r"]) == 2
    assert capsys.readouterr().out == ""


def test_cli_build_errors_exit_1(tmp_path, capsys):
    args = cli_files(tmp_path)
    missing = list(args)
    missing[missing.index("--closes") + 1] = str(tmp_path / "nope.json")
    assert main(missing, client_factory=cli_client) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "nope.json" in captured.err

    bad_spec = cli_files(tmp_path, spec={**SPEC.__dict__, "timing": "noon"})
    assert main(bad_spec, client_factory=cli_client) == 1
    assert capsys.readouterr().out == ""

    refusing = FakeClient(lambda kw: response([], "refusal"))
    assert main(cli_files(tmp_path), client_factory=lambda: refusing) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "refused" in captured.err

    empty = FakeClient(lambda kw: response([]) if "tools" in kw
                       else text_response(json.dumps({"items": []})))
    assert main(cli_files(tmp_path), client_factory=lambda: empty) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "release" in captured.err
    assert list_cases(tmp_path / "root") == []
