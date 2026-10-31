"""Acceptance tests for the main text of a fetched page (v31), written from SPEC.md independently
of the code (held back from the agent that built it).

Each case traces to "The main text of a fetched page (v31)" in SPEC.md. Nothing touches the
network: transports are fakes, or urllib.request.urlopen is replaced.
"""

from __future__ import annotations

import inspect
import json
import urllib.error
import urllib.request

import pytest

from researchos import cases, sources
from researchos.__main__ import main
from researchos.sources import get_page, html_to_text, url_fetcher

FED_LIKE = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    "<title>Federal Reserve Board - Federal Reserve issues FOMC statement</title>"
    "<script>var menu = 1;</script></head><body>"
    '<div class="header"><a href="#content">Skip to main content</a>'
    '<ul class="nav"><li><a>About the Fed</a></li><li><a>News &amp; Events</a></li>'
    "<li><a>Monetary Policy</a></li></ul></div>"
    '<div id="content" class="container container__main" role="main">'
    '<ol class="breadcrumb"><li>Home</li><li>Press Releases</li></ol>'
    '<div id="article"><div><h3 class="title">Federal Reserve issues FOMC statement</h3></div>'
    "<div><p>Recent indicators suggest that economic activity has been expanding at a solid "
    "pace.</p><p>The Committee decided to maintain the target range for the federal funds rate "
    "at 3-1/2 to 3-3/4&nbsp;percent.</p></div></div>"
    '<div class="lastUpdate">Last Update: October 28, 2026</div></div>'
    '<div class="footer"><ul><li>Contact</li><li>Accessibility</li></ul></div>'
    "</body></html>")
FED_TITLE = "Federal Reserve Board - Federal Reserve issues FOMC statement"
FED_MAIN = "\n".join([
    "Home", "Press Releases", "Federal Reserve issues FOMC statement",
    "Recent indicators suggest that economic activity has been expanding at a solid pace.",
    "The Committee decided to maintain the target range for the federal funds rate at 3-1/2 to "
    "3-3/4 percent.",
    "Last Update: October 28, 2026"])
FED_WHOLE = "\n".join(["Skip to main content", "About the Fed", "News & Events", "Monetary Policy",
                       FED_MAIN, "Contact", "Accessibility"])


def text(html, main_only=True):
    return html_to_text(html, main_only=main_only)[1]


# --- html_to_text ---------------------------------------------------------------------------------

def test_without_main_only_the_whole_page_as_before():
    assert html_to_text(FED_LIKE) == (FED_TITLE, FED_WHOLE)
    assert html_to_text(FED_LIKE, main_only=False) == (FED_TITLE, FED_WHOLE)


def test_main_only_keeps_the_main_element():
    assert html_to_text(FED_LIKE, main_only=True) == (FED_TITLE, FED_MAIN)


def test_the_keyword_defaults():
    for func in (html_to_text, get_page):
        param = inspect.signature(func).parameters["main_only"]
        assert param.default is False and param.kind is inspect.Parameter.KEYWORD_ONLY


def test_a_main_tag():
    page = ("<html><head><title>Page</title></head><body><nav><p>Menu</p></nav>"
            "<main><h1>Head</h1><p>Text &amp; more</p></main><footer>Foot</footer></body></html>")
    assert html_to_text(page, main_only=True) == ("Page", "Head\nText & more")
    assert html_to_text(page) == ("Page", "Menu\nHead\nText & more\nFoot")


@pytest.mark.parametrize("attr", ['role="main"', 'role=" MAIN "', "role='Main'", "role=main",
                                  'ROLE="main"', 'class="x" role="main"',
                                  'role="banner" role="main"'])
def test_role_main(attr):
    assert text(f"<p>Nav</p><div {attr}><p>Body</p></div><p>Foot</p>") == "Body"


@pytest.mark.parametrize("attr", ['role="main navigation"', 'role=""', "role", 'role="mainly"',
                                  'aria-role="main"', 'id="main"', 'class="main"',
                                  'data-role="main"'])
def test_not_role_main(attr):
    assert text(f"<p>Nav</p><div {attr}><p>Body</p></div><p>Foot</p>") == "Nav\nBody\nFoot"


def test_upper_case_names():
    assert text("<P>Nav</P><MAIN><P>Body</P></MAIN><P>Foot</P>") == "Body"
    assert text('<p>Nav</p><DIV ROLE="MAIN">Body</DIV><p>Foot</p>') == "Body"


def test_elements_of_the_same_name_inside_it():
    page = '<div>Nav</div><div role="main">A<div>B</div>C<div><div>D</div></div>E</div><div>Side</div>'
    assert text(page) == "A\nB\nC\nD\nE"


def test_an_unclosed_main_runs_to_the_end():
    assert text('<p>Nav</p><div role="main"><p>A</p><p>B</p>') == "A\nB"
    assert text('<p>Nav</p><div role="main">A<div>B</div>') == "A\nB"
    assert text("<p>Nav</p><main>A<p>B") == "A\nB"


def test_an_end_tag_of_another_name_does_not_close_it():
    assert text('<section role="main">A<div>B</section>C') == "A\nB"
    # a stray end tag of a block element still breaks the line, inside the element too
    assert text('<span role="main">A</div>B</span>C') == "A\nB"


def test_end_tags_inside_dropped_elements_count_for_nothing():
    page = '<p>Nav</p><div role="main">A<template></div></template>B</div>C'
    assert text(page) == "AB"
    page = '<p>Nav</p><div role="main">A<script>"</div>"</script>B</div>C'
    assert text(page) == "AB"


def test_start_tags_inside_dropped_elements_count_for_nothing():
    page = '<p>Nav</p><div role="main">A<template><div></template>B</div>C'
    assert text(page) == "AB"


@pytest.mark.parametrize("page", [
    "<p>Nav</p><main>A</main><main>B</main><p>Foot</p>",
    '<p>Nav</p><main>A</main><div role="main">B</div><p>Foot</p>',
    '<p>Nav</p><main>A<div role="main">B</div></main><p>Foot</p>',
    '<p>Nav</p><div role="main">A<div role="main">B</div></div><p>Foot</p>',
])
def test_more_than_one_main_element_gives_the_whole_page(page):
    assert text(page) == text(page, main_only=False)
    assert text(page).startswith("Nav") and text(page).endswith("Foot")


def test_no_main_element_gives_the_whole_page():
    page = "<html><head><title>T</title></head><body><p>Nav</p><div>Body</div></body></html>"
    assert html_to_text(page, main_only=True) == html_to_text(page) == ("T", "Nav\nBody")


@pytest.mark.parametrize("main", ["<main></main>", "<main> \t </main>",
                                  "<main><script>x()</script><style>p{}</style></main>",
                                  "<main><p> </p><br><div>&nbsp;</div></main>",
                                  '<div role="main"/>', "<main/>"])
def test_an_empty_main_element_gives_the_whole_page(main):
    assert text(f"<p>Nav</p>{main}<p>Rest</p>") == "Nav\nRest"


@pytest.mark.parametrize("hidden", [
    "<template><main>T</main></template>",
    '<noscript><div role="main">N</div></noscript>',
    '<svg><g role="main">S</g></svg>',
    '<script>document.write("<main>J</main>")</script>',
    '<style>main { color: red }</style>',
])
def test_main_elements_inside_dropped_elements_do_not_count(hidden):
    assert text(f"<p>Nav</p>{hidden}<main>Real</main><p>Foot</p>") == "Real"
    assert text(f"<p>Nav</p>{hidden}<p>Foot</p>") == "Nav\nFoot"


def test_main_elements_in_the_head():
    page = ('<html><head><title>Title</title><noscript><main>N</main></noscript></head>'
            "<body><p>Nav</p><main>Body</main></body></html>")
    assert html_to_text(page, main_only=True) == ("Title", "Body")
    # A start tag the head cannot hold ends the head, so this one is kept.
    page = '<html><head><title>Title</title><div role="main">X</div></head><body>Y</body></html>'
    assert html_to_text(page, main_only=True) == ("Title", "X")


def test_the_title_is_the_pages():
    page = "<html><head><title> The   page </title></head><body><main><p>A</p></main></body></html>"
    assert html_to_text(page, main_only=True) == ("The page", "A")
    page = "<p>Nav</p><main><title>Inner</title><p>A</p></main>"
    assert html_to_text(page, main_only=True) == ("Inner", "A")


def test_tables_inside_the_main_element():
    page = ("<p>Nav</p><main><table><tr><th>k</th><th>v</th></tr><tr><td>a</td><td>1</td></tr>"
            "</table></main>")
    assert text(page) == "k | v\na | 1"


def test_a_main_element_that_is_a_table_cell():
    page = '<table><tr><td>x</td><td role="main">y<b>z</b></td><td>w</td></tr></table>'
    assert text(page) == "yz"
    assert text(page, main_only=False) == "x | yz | w"


def test_a_main_element_that_is_a_row():
    page = '<table><tr><td>x</td></tr><tr role="main"><td>a</td><td>b</td></tr><tr><td>w</td></tr></table>'
    assert text(page) == "a | b"


def test_pre_inside_and_as_the_main_element():
    assert text("<p>Nav</p><main><pre>a\n   b\nc  d</pre></main>") == "a\nb\nc d"
    assert text('<p>Nav</p><pre role="main">a\n   b</pre><p>Foot</p>') == "a\nb"


def test_spaces_and_character_references_inside_it():
    assert text("<p>Nav</p><main>a&nbsp;&nbsp; b&lt;c&#x26;d\n e\t\tf</main>") == "a b<c&d e f"


def test_text_around_the_main_element_on_its_lines_is_left_out():
    assert text('<span>Before</span><span role="main">Inside</span><span>After</span>') == "Inside"
    assert text('<span>Before</span><span role="main">Inside</span><span>After</span>',
                main_only=False) == "BeforeInsideAfter"


# --- get_page ---------------------------------------------------------------------------------------

class Transport:
    def __init__(self, body, content_type):
        self.body, self.content_type, self.calls = body, content_type, []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        return self.body, self.content_type


def test_get_page_main_only():
    transport = Transport(FED_LIKE.encode("utf-8"), "text/html; charset=utf-8")
    page = get_page("https://fed.example/s", user_agent="ua", transport=transport,
                    main_only=True)
    assert (page.url, page.title, page.text) == ("https://fed.example/s", FED_TITLE, FED_MAIN)
    assert transport.calls == [("https://fed.example/s", {"User-Agent": "ua"})]


def test_get_page_default_is_the_whole_page():
    transport = Transport(FED_LIKE.encode("utf-8"), "text/html")
    assert get_page("u", user_agent="ua", transport=transport).text == FED_WHOLE
    assert get_page("u", user_agent="ua", transport=transport, main_only=False).text == FED_WHOLE


def test_get_page_main_only_without_a_content_type():
    transport = Transport(b"  " + FED_LIKE.encode("utf-8"), "")
    assert get_page("u", user_agent="ua", transport=transport, main_only=True).text == FED_MAIN


def test_get_page_main_only_xhtml_and_charset():
    body = '<p>Nav</p><main>café</main>'.encode("latin-1")
    transport = Transport(body, "application/xhtml+xml; charset=latin-1")
    assert get_page("u", user_agent="ua", transport=transport, main_only=True).text == "café"


def test_plain_text_is_kept_whole():
    body = "<main>not HTML</main>\nline two"
    transport = Transport(body.encode("utf-8"), "text/plain; charset=utf-8")
    page = get_page("u", user_agent="ua", transport=transport, main_only=True)
    assert (page.title, page.text) == ("", body)


@pytest.mark.parametrize("content_type", ["application/pdf", "image/png"])
def test_other_content_types_are_still_refused(content_type):
    transport = Transport(b"%PDF", content_type)
    with pytest.raises(ValueError, match="u"):
        get_page("u", user_agent="ua", transport=transport, main_only=True)


def test_no_content_type_and_not_html_is_still_refused():
    with pytest.raises(ValueError):
        get_page("u", user_agent="ua", transport=Transport(b"plain", ""), main_only=True)


def test_transport_errors_still_propagate():
    def transport(url, headers):
        raise urllib.error.URLError("down")

    with pytest.raises(OSError):
        get_page("u", user_agent="ua", transport=transport, main_only=True)


def test_url_fetcher_reads_whole_pages():
    transport = Transport(FED_LIKE.encode("utf-8"), "text/html")
    fetch = url_fetcher({"release": ["https://ir.example/8k"]}, user_agent="ua",
                        transport=transport)
    pages = fetch("Find the results release")
    assert [page.text for page in pages] == [FED_WHOLE]


# --- snapshot -------------------------------------------------------------------------------------

SERIES = {
    "DGS10": [["2026-07-30", 4.40], ["2026-09-30", 4.31], ["2026-10-23", 4.00], ["2026-10-29", 3.98]],
}


def config(url):
    return {
        "case_prefix": "macro",
        "horizons": ["1w", "1m"],
        "targets": [{"id": "DGS10", "name": "10-year Treasury yield", "unit": "percent",
                     "kind": "bp"}],
        "documents": [{"kind": "statement", "title": "FOMC statement", "dated": "2026-10-28",
                       "url": url}],
    }


class FakeResponse:
    def __init__(self, body, content_type):
        self.body, self.headers = body, {"Content-Type": content_type}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(monkeypatch, served):
    calls = []

    def urlopen(request, timeout=None):
        calls.append(request.full_url)
        if request.full_url not in served:
            raise urllib.error.URLError("no route")
        return FakeResponse(served[request.full_url].encode("utf-8"), "text/html; charset=utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def snapshot_cli(tmp_path, capsys, url):
    series = tmp_path / "series"
    series.mkdir()
    for name, pairs in SERIES.items():
        (series / f"{name}.json").write_text(json.dumps(pairs), encoding="utf-8")
    path = tmp_path / "macro-config.json"
    path.write_text(json.dumps(config(url)), encoding="utf-8")

    def no_client():
        raise AssertionError("snapshot never builds a client")

    code = main(["snapshot", "--root", str(tmp_path / "cases"), "--config", str(path),
                 "--as-of", "2026-10-30", "--series-dir", str(series)], client_factory=no_client)
    out, err = capsys.readouterr()
    return code, out, err


def statement(tmp_path):
    case = cases.load_case(tmp_path / "cases", "macro-2026-10-30")
    [doc] = [d for d in case.documents if d.source]
    return doc


def test_snapshot_keeps_the_main_text(tmp_path, monkeypatch, capsys):
    calls = serve(monkeypatch, {"https://fed.example/1028": FED_LIKE})
    code, out, err = snapshot_cli(tmp_path, capsys, "https://fed.example/1028")
    assert code == 0, err
    assert calls == ["https://fed.example/1028"]
    doc = statement(tmp_path)
    assert doc.text == FED_MAIN and doc.source == "https://fed.example/1028"
    assert "Skip to main content" not in json.dumps(json.loads(out))


def test_snapshot_of_a_page_without_a_main_element(tmp_path, monkeypatch, capsys):
    serve(monkeypatch, {"https://fed.example/1028": "<p>Nav</p><div>Lower the range.</div>"})
    code, _, err = snapshot_cli(tmp_path, capsys, "https://fed.example/1028")
    assert code == 0, err
    assert statement(tmp_path).text == "Nav\nLower the range."


def test_snapshot_passes_main_only(tmp_path, monkeypatch, capsys):
    seen = []
    real = sources.get_page

    def spy(url, **kw):
        seen.append(kw)
        return real(url, **kw)

    import researchos.__main__ as cli
    serve(monkeypatch, {"https://fed.example/1028": FED_LIKE})
    # the command may call it by either name
    monkeypatch.setattr(sources, "get_page", spy)
    if hasattr(cli, "get_page"):
        monkeypatch.setattr(cli, "get_page", spy)
    code, _, err = snapshot_cli(tmp_path, capsys, "https://fed.example/1028")
    assert code == 0, err
    assert len(seen) == 1 and seen[0]["main_only"] is True and seen[0]["user_agent"]


def test_a_dead_link_is_still_left_out(tmp_path, monkeypatch, capsys):
    serve(monkeypatch, {})
    code, out, err = snapshot_cli(tmp_path, capsys, "https://fed.example/1028")
    assert code == 0, err
    assert "statement" not in json.loads(out)["documents"]
