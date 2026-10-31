"""The main text of a fetched page (v31): html_to_text and get_page with main_only, and
the snapshot command asking for it.

Nothing here touches the network: an autouse fixture makes urllib.request.urlopen fail,
and every test either passes its own transport or replaces urlopen with a fake.
"""

import urllib.request

import pytest

from test_build_snapshot import CASE_ID, SEPTEMBER_URL, STATEMENT, cli_args, no_client
from test_fetch_by_address import PAGE, TABLE, FakeWeb, Site

from researchos.__main__ import main
from researchos.cases import load_case
from researchos.sources import FetchedPage, get_page, html_to_text, url_fetcher


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a real URL")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


# ---- html_to_text

FED_PAGE = ("<!DOCTYPE html><html><head><title>Federal Reserve issues FOMC statement</title>"
            "</head><body>"
            '<header><a href="/">Home</a> <a href="/newsevents.htm">News &amp; Events</a>'
            "</header>"
            "<nav><ul><li>Press Releases</li><li>Speeches</li></ul></nav>"
            "<main><h1>Federal Reserve issues FOMC statement</h1>"
            "<p>The Committee decided to lower the target range&nbsp;by 1/4 point.</p></main>"
            "<footer>Last Update: September 16, 2026</footer></body></html>")
FED_TITLE = "Federal Reserve issues FOMC statement"
FED_MAIN = ("Federal Reserve issues FOMC statement\n"
            "The Committee decided to lower the target range by 1/4 point.")
FED_WHOLE = ("Home News & Events\n"
             "Press Releases\n"
             "Speeches\n"
             + FED_MAIN + "\n"
             "Last Update: September 16, 2026")


def test_main_only_keeps_the_text_of_the_main_element():
    assert html_to_text(FED_PAGE, main_only=True) == (FED_TITLE, FED_MAIN)
    assert html_to_text(FED_PAGE) == (FED_TITLE, FED_WHOLE)
    assert html_to_text(FED_PAGE, main_only=False) == (FED_TITLE, FED_WHOLE)


@pytest.mark.parametrize("html, main_text, whole_text", [
    # role="main"; tag and attribute names as html.parser reports them, in lower case
    ('<div class="nav">Menu</div><DIV Role=" MAIN " id="content"><p>Body</p></DIV>'
     "<p>After</p>", "Body", "Menu\nBody\nAfter"),
    ("<P>Menu</P><MAIN><P>Body</P></MAIN><P>Footer</P>", "Body", "Menu\nBody\nFooter"),
    # a role without a value, or with another value, is not "main": the whole text
    ('<div role>Menu</div><div role="navigation main">Links</div><section role="">x</section>'
     "<p>Body</p>", "Menu\nLinks\nx\nBody", "Menu\nLinks\nx\nBody"),
    # nested elements of the main element's name
    ('<div>Menu</div><div role="main"><div>One</div><div><div>Two</div></div></div>'
     "<div>Footer</div>", "One\nTwo", "Menu\nOne\nTwo\nFooter"),
    # none closes it: it runs to the end of the page
    ("<p>Menu</p><main><p>Body</p><div>More</div>", "Body\nMore", "Menu\nBody\nMore"),
    ('<p>Menu</p><div role="main"><div>Body<p>More</div><p>Tail', "Body\nMore\nTail",
     "Menu\nBody\nMore\nTail"),
    # only end tags of its own name count
    ('<p>Menu</p><section role="main"><p>a</p></div></main><p>b</p>', "a\nb", "Menu\na\nb"),
    # a table cell does not begin with " | "; a row or a table keeps its cells joined
    ('<table><tr><td>Rate</td><td role="main">4.00 <b>to</b> 4.25%</td><td>Oct</td></tr>'
     "</table>", "4.00 to 4.25%", "Rate | 4.00 to 4.25% | Oct"),
    ('<table><tr><th>Target</th><th>Level</th></tr><tr role="main"><td>Rate</td>'
     "<td>4.00</td></tr></table>", "Rate | 4.00", "Target | Level\nRate | 4.00"),
    ('<p>Menu</p><table role="main"><tr><td>a<td>b<tr><td>c<td>d</table><p>Footer</p>',
     "a | b\nc | d", "Menu\na | b\nc | d\nFooter"),
    # a title, and what is in the head or in a dropped element, is not a main element
    ('<head><title role="main">T</title><meta role="main"></head><template><main>Draft'
     '</main></template><svg><g role="main"><text>Chart</text></g></svg><noscript>'
     '<div role="main">Enable JavaScript</div></noscript><p>Body</p>', "Body", "Body"),
    ("<template><main>Draft</main></template><main><p>Real</p></main><p>Footer</p>",
     "Real", "Real\nFooter"),
    # a main start tag ends a head left open, so the page keeps its text
    ("<head><title>T</title><main><p>Body</p></main><p>Footer</p>", "Body", "Body\nFooter"),
    # tags inside a dropped element count for nothing
    ('<div role="main"><p>a</p><template></div></template><svg></div></svg><p>b</p></div>'
     "<p>Footer</p>", "a\nb", "a\nb\nFooter"),
    ('<div role="main"><p>a</p><template><div></template></div><p>Footer</p>', "a",
     "a\nFooter"),
    # more than one, one inside another counting as two: the whole text
    ("<main><p>One</p></main><main><p>Two</p></main>", "One\nTwo", "One\nTwo"),
    ('<main><p>One</p></main><div role="main">Two</div>', "One\nTwo", "One\nTwo"),
    ('<div role="main"><main><p>Inner</p></main><p>Outer</p></div><p>Footer</p>',
     "Inner\nOuter\nFooter", "Inner\nOuter\nFooter"),
    # an empty main text, or a self-closing main element: the whole text
    ("<p>Menu</p><main> &nbsp; <p></p><script>render()</script></main><p>Footer</p>",
     "Menu\nFooter", "Menu\nFooter"),
    ('<p>Menu</p><div role="main"/><p>Body</p>', "Menu\nBody", "Menu\nBody"),
    # the same line rules as the whole text
    ("<p>Menu</p><main><pre>  a   b\n\n  c\r\n</pre><p>x &amp;&nbsp;&nbsp;y\n z</p></main>",
     "a b\nc\nx & y z", "Menu\na b\nc\nx & y z"),
    ('<p>Rate: <span role="main">  4.00% </span> today</p>', "4.00%", "Rate: 4.00% today"),
])
def test_main_only(html, main_text, whole_text):
    assert html_to_text(html, main_only=True)[1] == main_text
    assert html_to_text(html)[1] == whole_text
    assert html_to_text(html, main_only=False)[1] == whole_text


def test_main_only_keeps_the_title():
    html = "<head><title> FOMC \n minutes </title></head><div>Menu</div><main>Minutes</main>"
    assert html_to_text(html, main_only=True) == ("FOMC minutes", "Minutes")
    assert html_to_text(html) == ("FOMC minutes", "Menu\nMinutes")


@pytest.mark.parametrize("html", [PAGE, TABLE, "", "<p>x</p>", "plain words"])
def test_main_only_without_a_main_element_is_the_whole_text(html):
    assert html_to_text(html, main_only=True) == html_to_text(html)


# ---- get_page and url_fetcher

URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"


def test_get_page_main_only():
    site = Site({URL: (FED_PAGE.encode("utf-8"), "text/html; charset=utf-8")})
    assert get_page(URL, user_agent="UA", transport=site, main_only=True) == \
        FetchedPage(URL, FED_TITLE, FED_MAIN)
    assert get_page(URL, user_agent="UA", transport=site) == \
        FetchedPage(URL, FED_TITLE, FED_WHOLE)
    assert get_page(URL, user_agent="UA", transport=site, main_only=False) == \
        FetchedPage(URL, FED_TITLE, FED_WHOLE)
    assert site.calls == [(URL, {"User-Agent": "UA"})] * 3


def test_get_page_main_only_without_a_content_type():
    site = Site({URL: (b"  \n" + FED_PAGE.encode("utf-8"), "")})
    assert get_page(URL, user_agent="UA", transport=site, main_only=True) == \
        FetchedPage(URL, FED_TITLE, FED_MAIN)


def test_get_page_main_only_keeps_plain_text_whole():
    text = "<main>Body</main>\r\n  Menu\tand more"
    site = Site({URL: (text.encode("utf-8"), "text/plain; charset=utf-8")})
    assert get_page(URL, user_agent="UA", transport=site, main_only=True) == \
        FetchedPage(URL, "", text)


def test_get_page_main_only_with_the_default_transport(monkeypatch):
    web = FakeWeb({URL: (FED_PAGE.encode("utf-8"), "text/html")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    assert get_page(URL, user_agent="UA", main_only=True) == \
        FetchedPage(URL, FED_TITLE, FED_MAIN)


def test_url_fetcher_reads_whole_pages():
    site = Site({URL: (FED_PAGE.encode("utf-8"), "text/html")})
    fetch = url_fetcher({"release": [URL]}, user_agent="UA", transport=site)
    assert fetch("the results release") == [FetchedPage(URL, FED_TITLE, FED_WHOLE)]


# ---- the snapshot command

STATEMENT_PAGE = ("<html><head><title>Federal Reserve issues FOMC statement</title></head>"
                  "<body><nav><ul><li>News &amp; Events</li><li>Press Releases</li></ul></nav>"
                  f'<div id="article" role="main"><p>{STATEMENT}</p></div>'
                  "<footer><p>Last Update: September 16, 2026</p></footer></body></html>")


def snapshot_document(tmp_path, capsys, monkeypatch, page):
    web = FakeWeb({SEPTEMBER_URL: (page.encode("utf-8"), "text/html; charset=utf-8")})
    monkeypatch.setattr(urllib.request, "urlopen", web)
    code = main(cli_args(tmp_path), client_factory=no_client)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert web.urls() == [SEPTEMBER_URL]
    doc = load_case(tmp_path / "root", CASE_ID).documents[4]
    assert (doc.kind, doc.source) == ("fomc_statement", SEPTEMBER_URL)
    return doc.text


def test_cli_snapshot_keeps_the_main_text_of_a_fetched_page(tmp_path, capsys, monkeypatch):
    assert snapshot_document(tmp_path, capsys, monkeypatch, STATEMENT_PAGE) == STATEMENT
    assert html_to_text(STATEMENT_PAGE)[1] != STATEMENT  # the whole text has the menus


def test_cli_snapshot_keeps_the_whole_text_without_one_main_element(tmp_path, capsys,
                                                                    monkeypatch):
    page = f"<div>Menu</div><main><p>{STATEMENT}</p></main><main><p>More</p></main>"
    assert snapshot_document(tmp_path, capsys, monkeypatch, page) == \
        f"Menu\n{STATEMENT}\nMore"
