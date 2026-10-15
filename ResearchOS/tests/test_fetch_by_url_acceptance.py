"""Acceptance tests for fetching pages by address (v14), written from SPEC.md independently of
the code (held back from the agent that built it).

Each case traces to the "Fetching pages by address (v14)" section of SPEC.md. Nothing touches the
network: the transport is a fake, or urllib.request.urlopen is replaced.
"""

from __future__ import annotations

import json
import logging
import urllib.request

import pytest
from test_sources_acceptance import MARKET, SPEC, STOCK, ScriptedClient, build_agent

from researchos import cases, sources

PAGE = """<!doctype html><html><head><title>  Acme   Q2 2026
 results </title><style>.x { color: red }</style><script>var a = 1;</script></head>
<body><header>Menu</header><div><h1>Acme reports&nbsp;results</h1>
<p>Revenue rose 12%&amp;margins   widened.<br>EPS $1.10</p>
<table><tr><th>Metric</th><th>Q2</th></tr><tr><td>Revenue</td><td>$12.5B</td></tr></table>
<ul><li>One</li><li>Two</li></ul><noscript>enable js</noscript><svg><text>logo</text></svg>
<template><p>hidden</p></template><p>Up <b>4%</b> on <a href="x">demand</a></p>
<p>caf&eacute; &#8212; done</p></div></body></html>"""


def test_html_to_text():
    title, text = sources.html_to_text(PAGE)
    assert title == "Acme Q2 2026 results"
    assert text.splitlines() == ["Menu", "Acme reports results", "Revenue rose 12%&margins widened.",
                                 "EPS $1.10", "Metric | Q2", "Revenue | $12.5B", "One", "Two",
                                 "Up 4% on demand", "café — done"]


def test_html_to_text_edges():
    assert sources.html_to_text("") == ("", "")
    assert sources.html_to_text("<p>  a\t\tb  </p><p></p><p>c</p>") == ("", "a b\nc")
    assert sources.html_to_text("<html><head><title>T</title></head></html>") == ("T", "")


def fake_transport(pages, seen=None):
    def transport(url, headers):
        if seen is not None:
            seen.append((url, dict(headers)))
        item = pages[url]
        if isinstance(item, Exception):
            raise item
        return item
    return transport


def test_get_page():
    seen = []
    pages = {
        "https://a.example/r": ("<html><head><title>T</title></head><body><p>caf\xe9</p></body></html>"
                                .encode("latin-1"), "text/html; charset=ISO-8859-1"),
        "https://a.example/u": (b"<p>ok \xff</p>", "text/html"),
        "https://a.example/t": ("line one\nline two".encode("utf-8"), "text/plain; charset=utf-8"),
        "https://a.example/n": (b"  \n <html><body><p>sniffed</p></body></html>", ""),
        "https://a.example/x": ("XHTML <b>page</b>".encode(), "application/xhtml+xml"),
    }
    t = fake_transport(pages, seen)
    page = sources.get_page("https://a.example/r", user_agent="Tester/1.0", transport=t)
    assert page == sources.FetchedPage("https://a.example/r", "T", "café")
    assert seen[0] == ("https://a.example/r", {"User-Agent": "Tester/1.0"})
    assert sources.get_page("https://a.example/u", user_agent="u", transport=t).text == "ok �"
    assert sources.get_page("https://a.example/t", user_agent="u", transport=t) == \
        sources.FetchedPage("https://a.example/t", "", "line one\nline two")
    assert sources.get_page("https://a.example/n", user_agent="u", transport=t).text == "sniffed"
    assert sources.get_page("https://a.example/x", user_agent="u", transport=t).text == "XHTML page"


@pytest.mark.parametrize("body, ctype", [(b"%PDF-1.7 ...", "application/pdf"), (b"\x89PNG", "image/png"),
                                         (b"plain words, no markup", "")])
def test_get_page_refuses_what_is_not_a_page(body, ctype):
    t = fake_transport({"https://a.example/doc.bin": (body, ctype)})
    with pytest.raises(ValueError) as info:
        sources.get_page("https://a.example/doc.bin", user_agent="u", transport=t)
    assert "https://a.example/doc.bin" in str(info.value)


def test_get_page_lets_transport_errors_through():
    t = fake_transport({"https://a.example/gone": OSError("HTTP Error 404: Not Found")})
    with pytest.raises(OSError):
        sources.get_page("https://a.example/gone", user_agent="u", transport=t)


class FakeResponse:
    def __init__(self, body, ctype):
        self.body, self.headers = body, ({"Content-Type": ctype} if ctype else {})

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(served, calls):
    def urlopen(request, timeout=None):
        calls.append({"url": request.full_url, "agent": request.get_header("User-agent"), "timeout": timeout})
        body, ctype = served[request.full_url]
        return FakeResponse(body, ctype)
    return urlopen


def test_default_transport_uses_urlopen(monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        fake_urlopen({"https://a.example/p": (b"<p>hello</p>", "text/html")}, calls))
    page = sources.get_page("https://a.example/p", user_agent="Default Transport Test")
    assert page.text == "hello"
    assert calls == [{"url": "https://a.example/p", "agent": "Default Transport Test", "timeout": 30}]


RELEASE_HTML = ("<html><head><title>Acme Q2 2026 results</title></head><body><p>nav</p><p>nav</p>"
                "<p>RELEASE-START Acme results</p><p>revenue, margins and the financial tables</p>"
                "<p>RELEASE-END</p><p>footer</p></body></html>").encode()
CALL_HTML = ("<html><head><title>Acme Q2 call</title></head><body><p>ads</p><p>CALL-START Operator: welcome.</p>"
             "<p>CALL-END</p><p>comments</p></body></html>").encode()
PREVIEW_HTML = b"<html><head><title>Preview</title></head><body><p>Consensus EPS $1.10</p></body></html>"


def test_url_fetcher_routes_by_kind_and_skips_dead_links(caplog):
    served = {"https://ir.example/r": (RELEASE_HTML, "text/html"),
              "https://ir.example/dead": OSError("HTTP Error 404: Not Found"),
              "https://ir.example/r.pdf": (b"%PDF", "application/pdf"),
              "https://t.example/c": (CALL_HTML, "text/html")}
    fetch = sources.url_fetcher({"release": ["https://ir.example/dead", "https://ir.example/r", "https://ir.example/r.pdf"],
                                 "transcript": ["https://t.example/c"]},
                                user_agent="u", transport=fake_transport(served))
    with caplog.at_level(logging.WARNING, logger="researchos"):
        release = fetch(sources.release_request(SPEC))
    assert [p.url for p in release] == ["https://ir.example/r"]
    assert release[0].title == "Acme Q2 2026 results"
    logged = caplog.text
    assert "https://ir.example/dead" in logged and "https://ir.example/r.pdf" in logged
    assert [p.url for p in fetch(sources.transcript_request(SPEC))] == ["https://t.example/c"]
    assert fetch(sources.expectations_request(SPEC)) == []


def test_build_case_from_addresses(tmp_path):
    served = {"https://ir.example/r": (RELEASE_HTML, "text/html; charset=utf-8"),
              "https://t.example/c": (CALL_HTML, "text/html"),
              "https://n.example/p": (PREVIEW_HTML, "text/html")}
    fetch = sources.url_fetcher({"release": ["https://ir.example/r"], "transcript": ["https://t.example/c"],
                                 "expectations": ["https://n.example/p"]},
                                user_agent="u", transport=fake_transport(served))
    case = sources.build_case(tmp_path, SPEC, fetch=fetch, agent=build_agent(),
                              stock_closes=STOCK, market_closes=MARKET)
    assert [d.kind for d in case.documents] == ["release", "transcript", "expectations"]
    assert case.documents[0].text == "RELEASE-START Acme results\nrevenue, margins and the financial tables\nRELEASE-END"
    assert case.documents[0].source == "https://ir.example/r"
    assert case.documents[1].text == "CALL-START Operator: welcome.\nCALL-END"
    assert "https://n.example/p" in cases.load_outcome(tmp_path, SPEC.case_id).sources


class RecordingClient(ScriptedClient):
    def __init__(self):
        super().__init__()
        self.tool_requests = 0
        inner = self.create

        def create(**kwargs):
            if "tools" in kwargs:
                self.tool_requests += 1
            return inner(**kwargs)
        self.beta.messages.create = create


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def cli_files(tmp_path):
    spec = write(tmp_path / "spec.json", {"case_id": SPEC.case_id, "company": SPEC.company, "ticker": SPEC.ticker,
                                          "aliases": SPEC.aliases, "fiscal_period": SPEC.fiscal_period,
                                          "report_date": SPEC.report_date, "timing": SPEC.timing})
    closes = write(tmp_path / "s.json", [[d.isoformat(), c] for d, c in STOCK])
    market = write(tmp_path / "m.json", [[d.isoformat(), c] for d, c in MARKET])
    return ["--spec", spec, "--closes", closes, "--market-closes", market]


SERVED = {"https://ir.example/r": (RELEASE_HTML, "text/html"), "https://t.example/c": (CALL_HTML, "text/html")}


def test_cli_build_by_address(tmp_path, monkeypatch, capsys):
    from researchos.__main__ import main
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(SERVED, calls))
    monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    client = RecordingClient()
    root = tmp_path / "cases"
    code = main(["build", "--root", str(root), *cli_files(tmp_path),
                 "--release-url", "https://ir.example/r", "--transcript-url", "https://t.example/c",
                 "--user-agent", "Research Desk research@example.org"], client_factory=lambda: client)
    assert code == 0, capsys.readouterr().err
    assert client.tool_requests == 0                      # no web tools
    assert sorted(c["url"] for c in calls) == sorted(SERVED)
    assert {c["agent"] for c in calls} == {"Research Desk research@example.org"}
    assert [d.kind for d in cases.load_case(root, SPEC.case_id).documents] == ["release", "transcript"]


@pytest.mark.parametrize("env, expected", [(None, "ResearchOS/0.1 (research use)"), ("Env Agent 2.0", "Env Agent 2.0")])
def test_cli_build_default_user_agent(tmp_path, monkeypatch, capsys, env, expected):
    from researchos.__main__ import main
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen(SERVED, calls))
    if env is None:
        monkeypatch.delenv("RESEARCHOS_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("RESEARCHOS_USER_AGENT", env)
    code = main(["build", "--root", str(tmp_path / "cases"), *cli_files(tmp_path),
                 "--release-url", "https://ir.example/r", "--transcript-url", "https://t.example/c"],
                client_factory=RecordingClient)
    assert code == 0, capsys.readouterr().err
    assert {c["agent"] for c in calls} == {expected}
