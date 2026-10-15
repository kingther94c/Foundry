"""Fetching pages by address (v14): html_to_text, get_page, url_fetcher, build --*-url.

Nothing here touches the network: an autouse fixture makes urllib.request.urlopen
fail, and every test either passes its own transport or replaces urlopen with a fake.
"""

import http.client
import json
import logging
import urllib.error
import urllib.request

import pytest

from conftest import FakeClient, text_response
from test_sources import MARKET, SPEC, STOCK, agent_answer, build_agent, cli_client, cli_files

from researchos.__main__ import main
from researchos.cases import Document, list_cases, load_case, load_outcome
from researchos.sources import (
    FetchedPage,
    build_case,
    expectations_request,
    get_page,
    html_to_text,
    release_request,
    transcript_request,
    url_fetcher,
)

DEFAULT_UA = "ResearchOS/0.1 (research use)"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a real URL")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


class Site:
    """A transport serving {url: (body, content type) or an exception to raise}.

    Any other url fails as urllib reports a 404.
    """

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if url not in self.pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        served = self.pages[url]
        if isinstance(served, BaseException):
            raise served
        return served


class FakeResponse:
    def __init__(self, body, content_type=None):
        self.body = body
        self.headers = {} if content_type is None else {"Content-Type": content_type}
        self.closed = False

    def read(self):
        if isinstance(self.body, BaseException):
            raise self.body
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False


class FakeWeb:
    """Stands in for urllib.request.urlopen: serves {url: (body, content type or None)}.

    Records every (Request, timeout); any other url fails as urllib reports a 404.
    """

    def __init__(self, pages):
        self.pages = pages
        self.requests = []
        self.responses = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if request.full_url not in self.pages:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        response = FakeResponse(*self.pages[request.full_url])
        self.responses.append(response)
        return response

    def urls(self):
        return [request.full_url for request, _ in self.requests]

    def user_agents(self):
        return {request.get_header("User-agent") for request, _ in self.requests}


# ---- html_to_text

PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>
    Acme   Widgets &amp; Co:
    Q2 2026 results
  </title>
  <style>p { color: red; }</style>
  <script>var hidden = "<p>not text</p>";</script>
</head>
<body>
  <header><a href="/">Home</a> <a href="/ir">Investors</a></header>
  <noscript><p>Enable JavaScript</p></noscript>
  <article>
    <h1>Acme reports   Q2&nbsp;2026</h1>
    <p>Revenue rose 12%<br>to $1.2&nbsp;&nbsp;billion &#8212; a <b>record</b>.</p>
    <svg viewBox="0 0 10 10"><title>chart</title><text>42</text></svg>
    <template><p>template text</p></template>
    <ul><li>EPS $1.10</li><li>Margin&#x20;41%</li></ul>
    <hr>
    <blockquote>We are pleased.</blockquote>
  </article>
  <footer>&copy; Acme</footer>
</body>
</html>
"""


def test_html_to_text_title_and_visible_text():
    title, text = html_to_text(PAGE)
    assert title == "Acme Widgets & Co: Q2 2026 results"
    assert text == ("Home Investors\n"
                    "Acme reports Q2 2026\n"
                    "Revenue rose 12%\n"
                    "to $1.2 billion \N{EM DASH} a record.\n"
                    "EPS $1.10\n"
                    "Margin 41%\n"
                    "We are pleased.\n"
                    "\xa9 Acme")


TABLE = """
<p>Results</p>
<table>
  <tr>
    <th>Metric</th>
    <th>Q2 2026</th>
    <th>Q2 2025</th>
  </tr>
  <tr>
    <td>Revenue</td>
    <td>$&nbsp;1,200</td>
    <td><span>1,071</span></td>
  </tr>
  <tr><td>Note</td><td></td><td>n/a</td></tr>
</table>
<p>After</p>
"""


def test_html_to_text_joins_the_cells_of_a_row():
    assert html_to_text(TABLE) == ("", "Results\n"
                                       "Metric | Q2 2026 | Q2 2025\n"
                                       "Revenue | $ 1,200 | 1,071\n"
                                       "Note | | n/a\n"
                                       "After")


def test_html_to_text_rows_without_end_tags_or_table():
    assert html_to_text("<table><tr><td>a<td>b<tr><th>c<td>d</table>")[1] == "a | b\nc | d"
    assert html_to_text("<tr><td>a</td><td>b</td></tr><p>after</p>")[1] == "a | b\nafter"


def test_html_to_text_whitespace_and_pre():
    html = ("<div>\n  one\ttwo \xa0 three\n  four\n</div>"
            "<pre>  a   b\n\n  c\r\n</pre><p>   </p><span>x</span><span>y</span>")
    assert html_to_text(html) == ("", "one two three four\na b\nc\nxy")


def test_html_to_text_lines_are_the_lines_select_span_numbers():
    title, text = html_to_text("<p>a\x0cb\N{LINE SEPARATOR}c\x85d\re</p><pre>f\x0cg</pre>")
    assert text == "a b c d e\nf\ng"
    assert text.splitlines() == text.split("\n")


@pytest.mark.parametrize("html, expected", [
    ("<p>x</p>", ("", "x")),
    ("", ("", "")),
    ("<title>  T \n one </title><p>x</p>", ("T one", "x")),  # no head: still not text
    ("<html><head><title>T</title><body><p>x</p></body></html>", ("T", "x")),  # no </head>
    ("<head><title>T</title><meta charset=utf-8><p>x", ("T", "x")),  # no </head>, no <body>
    ("<body><svg><title>icon</title></svg><p>x</p></body>", ("", "x")),  # an SVG's title
    ("<title>First</title><title>Second</title><p>x</p>", ("First", "x")),
])
def test_html_to_text_title(html, expected):
    assert html_to_text(html) == expected


# ---- get_page

URL = "https://acme.example/p"
CAFE = "<p>caf\xe9</p>"


def test_get_page_sends_the_user_agent_and_reads_html():
    site = Site({URL: (b"<html><head><title>Q2</title></head><body><p>Revenue up</p></body>"
                       b"</html>", "text/html; charset=utf-8")})
    page = get_page(URL, user_agent="Acme Research ops@acme.example", transport=site)
    assert page == FetchedPage(URL, "Q2", "Revenue up")
    assert site.calls == [(URL, {"User-Agent": "Acme Research ops@acme.example"})]


@pytest.mark.parametrize("body, content_type, text", [
    (CAFE.encode("utf-8"), "text/html", "caf\xe9"),  # no charset: UTF-8
    (CAFE.encode("latin-1"), "text/html; charset=ISO-8859-1", "caf\xe9"),
    (CAFE.encode("cp1252"), 'Text/HTML; Charset="windows-1252"', "caf\xe9"),
    (CAFE.encode("utf-16"), "application/xhtml+xml; charset=utf-16", "caf\xe9"),
    (CAFE.encode("latin-1"), "text/html", "caf\N{REPLACEMENT CHARACTER}"),  # not UTF-8
    (CAFE.encode("utf-8"), "text/html; charset=no-such-charset", "caf\xe9"),
    (b"\xef\xbb\xbf" + CAFE.encode("utf-8"), "text/html; charset=utf-8", "caf\xe9"),
    (b"\xef\xbb\xbf \n" + CAFE.encode("utf-8"), "", "caf\xe9"),  # no type, but HTML
    (b"\r\n  <title>T</title>" + CAFE.encode("utf-8"), "", "caf\xe9"),
])
def test_get_page_decodes_html(body, content_type, text):
    page = get_page(URL, user_agent="UA", transport=Site({URL: (body, content_type)}))
    assert page.url == URL and page.text == text


def test_get_page_keeps_plain_text_as_it_is():
    text = "Line one\r\n  Line  two\t\n\ncaf\xe9"
    site = Site({URL: (text.encode("cp1252"), "text/plain; charset=windows-1252")})
    assert get_page(URL, user_agent="UA", transport=site) == FetchedPage(URL, "", text)
    site = Site({URL: (text.encode("utf-8"), "text/plain")})
    assert get_page(URL, user_agent="UA", transport=site) == FetchedPage(URL, "", text)


@pytest.mark.parametrize("body, content_type", [
    (b"%PDF-1.7\n...", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b'{"eps": 1.1}', "application/json; charset=utf-8"),
    (b"%PDF-1.7\n...", ""),  # no content type, and not HTML
])
def test_get_page_refuses_what_is_not_html_or_text(body, content_type):
    url = "https://acme.example/q2.pdf"
    with pytest.raises(ValueError) as info:
        get_page(url, user_agent="UA", transport=Site({url: (body, content_type)}))
    assert url in str(info.value)
    assert content_type.split(";")[0] in str(info.value)


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError(URL, 403, "Forbidden", {}, None),
    urllib.error.URLError("no route to host"),
    TimeoutError("timed out"),
])
def test_get_page_lets_transport_errors_through(error):
    with pytest.raises(type(error)):
        get_page(URL, user_agent="UA", transport=Site({URL: error}))


def test_get_page_default_transport_is_urlopen(monkeypatch):
    web = FakeWeb({"https://acme.example/ir/q2": (b"<title>Q2</title><p>Revenue up</p>", None),
                   "https://acme.example/t.txt": (b"plain", "text/plain")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    page = get_page("https://acme.example/ir/q2", user_agent="Acme Research ops@acme.example")
    assert page == FetchedPage("https://acme.example/ir/q2", "Q2", "Revenue up")
    assert get_page("https://acme.example/t.txt", user_agent="UA") == \
        FetchedPage("https://acme.example/t.txt", "", "plain")
    (request, timeout), _ = web.requests
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == "https://acme.example/ir/q2" and request.get_method() == "GET"
    assert request.get_header("User-agent") == "Acme Research ops@acme.example"
    assert timeout == 30
    assert all(response.closed for response in web.responses)


def test_get_page_default_transport_errors_are_oserrors(monkeypatch):
    web = FakeWeb({"https://acme.example/cut": (http.client.IncompleteRead(b"<p>Reven"),
                                                "text/html")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    with pytest.raises(urllib.error.HTTPError):
        get_page("https://acme.example/missing", user_agent="UA")
    with pytest.raises(OSError):
        get_page("https://acme.example/cut", user_agent="UA")


# ---- url_fetcher

def test_url_fetcher_routes_build_case_requests_by_kind():
    site = Site({
        "https://r/1": (b"<title>R1</title><p>one</p>", "text/html"),
        "https://r/2": (b"<title>R2</title><p>two</p>", "text/html"),
        "https://t/1": (b"<p>call</p>", "text/html"),
        "https://e/1": (b"EPS seen at $1.10", "text/plain"),
    })
    fetch = url_fetcher({"release": ["https://r/1", "https://r/2"],
                         "transcript": ["https://t/1"], "expectations": ["https://e/1"]},
                        user_agent="Acme Research ops@acme.example", transport=site)
    release = [FetchedPage("https://r/1", "R1", "one"), FetchedPage("https://r/2", "R2", "two")]
    transcript = [FetchedPage("https://t/1", "", "call")]
    expectations = [FetchedPage("https://e/1", "", "EPS seen at $1.10")]
    assert fetch(release_request(SPEC)) == release
    assert fetch(transcript_request(SPEC)) == transcript
    assert fetch(expectations_request(SPEC)) == expectations
    assert [url for url, _ in site.calls] == ["https://r/1", "https://r/2", "https://t/1",
                                              "https://e/1"]
    # "transcript" comes before "consensus"; anything else is the release's.
    assert fetch("the consensus view in the call transcript") == transcript
    assert fetch("analyst consensus before the report") == expectations
    assert fetch("anything else") == release
    assert all(headers == {"User-Agent": "Acme Research ops@acme.example"}
               for _, headers in site.calls)


def test_url_fetcher_kind_without_addresses_gives_nothing():
    site = Site({"https://r/1": (b"<p>one</p>", "text/html")})
    fetch = url_fetcher({"release": ["https://r/1"]}, user_agent="UA", transport=site)
    assert fetch(transcript_request(SPEC)) == []
    assert fetch(expectations_request(SPEC)) == []
    assert site.calls == []
    assert fetch(release_request(SPEC)) == [FetchedPage("https://r/1", "", "one")]


def test_url_fetcher_leaves_out_what_cannot_be_fetched_with_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="researchos")
    site = Site({
        "https://ok/1": (b"<p>one</p>", "text/html"),
        "https://refused": urllib.error.URLError("connection refused"),
        "https://slow": TimeoutError("timed out"),
        "https://pdf": (b"%PDF-1.7", "application/pdf"),
        "https://ok/2": (b"<p>two</p>", "text/html"),
    })
    fetch = url_fetcher({"release": ["https://refused", "https://ok/1", "https://gone",
                                     "https://slow", "https://pdf", "https://ok/2"]},
                        user_agent="UA", transport=site)
    pages = fetch(release_request(SPEC))
    assert pages == [FetchedPage("https://ok/1", "", "one"), FetchedPage("https://ok/2", "", "two")]
    warnings = [r.getMessage() for r in caplog.records
                if r.name == "researchos" and r.levelno == logging.WARNING]
    dead = ["https://refused", "https://gone", "https://slow", "https://pdf"]
    assert len(warnings) == len(dead)
    for url, message in zip(dead, warnings):
        assert url in message


def test_url_fetcher_lets_other_errors_through():
    def broken(url, headers):
        raise RuntimeError("a bug, not a dead link")

    fetch = url_fetcher({"release": ["https://r/1"]}, user_agent="UA", transport=broken)
    with pytest.raises(RuntimeError):
        fetch(release_request(SPEC))


# ---- build_case and the CLI with known addresses

Q2_URL = "https://acme.example/ir/q2"
DEAD_URL = "https://acme.example/ir/dead"
CALL_URL = "https://acme.example/call"
PREVIEW_URL = "https://news.example/preview"

RELEASE_HTML = """<!DOCTYPE html>
<html><head><title>Acme Q2 2026 results</title><script>track("<p>")</script></head>
<body><nav><a href="/">Home</a></nav>
<h1>START Acme reports Q2 2026</h1>
<table>
  <tr><td>Revenue</td><td>100</td></tr>
  <tr><td>Net income</td><td>10 END</td></tr>
</table>
<footer>Contact us</footer></body></html>
"""
RELEASE_SPAN = "START Acme reports Q2 2026\nRevenue | 100\nNet income | 10 END"
TRANSCRIPT_HTML = ("<title>ACMW call</title><div>ads</div><p>START Operator: welcome</p>"
                   "<p>CEO: we did well END</p><div>ads</div>")
TRANSCRIPT_SPAN = "START Operator: welcome\nCEO: we did well END"
WEB = {
    Q2_URL: (RELEASE_HTML.encode("utf-8"), "text/html; charset=utf-8"),
    CALL_URL: (TRANSCRIPT_HTML.encode("utf-8"), "text/html"),
    PREVIEW_URL: (b"Analysts expect EPS of $1.10 for Q2 2026.", "text/plain"),
}
URL_ARGS = ["--release-url", DEAD_URL, "--release-url", Q2_URL, "--transcript-url", CALL_URL,
            "--expectations-url", PREVIEW_URL]


def test_build_case_from_addresses(tmp_path):
    site = Site(WEB)
    fetch = url_fetcher({"release": [DEAD_URL, Q2_URL], "transcript": [CALL_URL],
                         "expectations": [PREVIEW_URL]}, user_agent="UA", transport=site)
    case = build_case(tmp_path, SPEC, fetch=fetch, agent=build_agent(),
                      stock_closes=STOCK, market_closes=MARKET)
    release, transcript, expectations = case.documents
    assert release == Document("release", "Acme Q2 2026 results", RELEASE_SPAN, Q2_URL)
    assert transcript == Document("transcript", "ACMW call", TRANSCRIPT_SPAN, CALL_URL)
    assert expectations.text == "- EPS: $1.10 (source: Zacks)"
    assert load_outcome(tmp_path, "acme-q2").sources == [Q2_URL, CALL_URL, PREVIEW_URL]
    assert [url for url, _ in site.calls] == [DEAD_URL, Q2_URL, CALL_URL, PREVIEW_URL]


def structured_client():
    """Serves the span and expectations calls; a request with web tools fails the test."""
    def responder(kw):
        assert "tools" not in kw, "no web tools may be used when the addresses are given"
        schema = kw["output_config"]["format"]["schema"]
        prompt = kw["messages"][0]["content"][-1]["text"]
        return text_response(json.dumps(agent_answer(prompt, schema)))

    return FakeClient(responder)


def test_cli_build_from_addresses(tmp_path, capsys, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="researchos")
    monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    web = FakeWeb(WEB)
    monkeypatch.setattr(urllib.request, "urlopen", web)
    client = structured_client()
    code = main(cli_files(tmp_path) + URL_ARGS, client_factory=lambda: client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    out = json.loads(captured.out)
    assert out["case_id"] == "acme-q2"
    assert out["outcome"]["abnormal_return_1d"] == pytest.approx(0.09)
    assert out["outcome"]["sources"] == [Q2_URL, CALL_URL, PREVIEW_URL]
    case = load_case(tmp_path / "root", "acme-q2")
    assert [d.text for d in case.documents[:2]] == [RELEASE_SPAN, TRANSCRIPT_SPAN]
    assert web.urls() == [DEAD_URL, Q2_URL, CALL_URL, PREVIEW_URL]
    assert web.user_agents() == {DEFAULT_UA}
    assert client.requests and all("tools" not in r for r in client.requests)
    assert all(r["output_config"]["effort"] == "medium" for r in client.requests)
    assert any(DEAD_URL in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("env, extra, expected", [
    (None, [], DEFAULT_UA),
    ("   ", [], DEFAULT_UA),
    ("Acme Research ops@acme.example", [], "Acme Research ops@acme.example"),
    ("Acme Research ops@acme.example", ["--user-agent", "Cli Tester cli@example.com"],
     "Cli Tester cli@example.com"),
])
def test_cli_build_user_agent(tmp_path, capsys, monkeypatch, env, extra, expected):
    if env is None:
        monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("RESEARCHOS_USER_AGENT", env)
    web = FakeWeb(WEB)
    monkeypatch.setattr(urllib.request, "urlopen", web)
    code = main(cli_files(tmp_path) + URL_ARGS + extra, client_factory=structured_client)
    assert code == 0, capsys.readouterr().err
    assert web.user_agents() == {expected}


@pytest.mark.parametrize("urls, missing", [
    (["--release-url", Q2_URL], "transcript"),
    (["--release-url", DEAD_URL, "--transcript-url", CALL_URL], "release"),
])
def test_cli_build_from_addresses_missing_document_exits_1(tmp_path, capsys, monkeypatch,
                                                           urls, missing):
    monkeypatch.setattr(urllib.request, "urlopen", FakeWeb(WEB))
    client = structured_client()
    code = main(cli_files(tmp_path) + urls, client_factory=lambda: client)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "" and f"no {missing}" in captured.err
    assert list_cases(tmp_path / "root") == []
    assert all("tools" not in r for r in client.requests)


def test_cli_build_without_addresses_still_searches_the_web(tmp_path, capsys):
    client = cli_client()
    code = main(cli_files(tmp_path) + ["--user-agent", "Cli Tester cli@example.com"],
                client_factory=lambda: client)
    assert code == 0, capsys.readouterr().err
    assert sum("tools" in r for r in client.requests) == 3


def test_cli_build_url_options_need_a_value(tmp_path, capsys):
    assert main(cli_files(tmp_path) + ["--release-url"]) == 2
    assert main(cli_files(tmp_path) + ["--user-agent"]) == 2
    assert capsys.readouterr().out == ""
