## The main text of a fetched page (v31)

A page that `snapshot` fetches by address (an FOMC statement, the minutes)
brings its site with it: the menus, the links to other releases, the
footer. On the Fed's pages that is about 8,300 characters a page, most of
a statement's text. Pages mark the content itself with a `main` element or
with `role="main"`. This version lets `get_page` keep only that element's
text, and `snapshot` asks for it. Every other caller, `build` among them,
gets a page's whole text as before.

### In `researchos.sources`

- `html_to_text(html, *, main_only=False) -> tuple[str, str]`. With
  `main_only` false, everything is as in v14. With `main_only` true, the
  title is as before, and the text is that of the page's main element
  when the page has exactly one:
  - A *main element* is a start tag whose text the page's text keeps (not
    `title`, not one of the dropped elements `script`, `style`, `noscript`,
    `head`, `svg` and `template`, and not inside one of them), and whose
    name is `main` or that has a `role` attribute with the value `"main"`
    after `str.strip()` and `str.lower()`. A `role` attribute written
    without a value (`<div role>`) is not `"main"`. Tag and attribute
    names are compared as `html.parser` reports them, in lower case.
  - The element runs from its start tag to the end tag that closes it.
    Counting from its start tag, each start tag of the same name adds one
    and each end tag of that name takes one away, counting only tags whose
    text the page keeps (an end tag inside a dropped element counts for
    nothing). The element ends at the end tag that brings the count to
    zero. If none does, it runs to the end of the page. A start tag
    written as self-closing (`<div role="main"/>`) ends at once, since
    `html.parser` reports it as a start tag and then an end tag.
  - Its text is the page's text between its start tag and that end tag,
    built by the same rules as the whole text (line breaks, table cells
    joined by " | ", `pre`, character references, runs of spaces, stripped
    lines, empty lines dropped). What its own start and end tags add is
    left out: an element that is a table cell does not begin with " | ".
  - When the page has no main element, or more than one (one inside
    another counts as two), or when its main element's text is empty, the
    text is the page's whole text, as with `main_only` false.
- `get_page(url, *, user_agent, transport=None, main_only=False) ->
  FetchedPage`: an HTML page becomes `FetchedPage(url,
  *html_to_text(body, main_only=main_only))`. Everything else is as in
  v14; plain text is kept whole whatever `main_only` is.
- `url_fetcher` is unchanged, so `build` reads whole pages.

### CLI

`snapshot` fetches with `fetch_page = lambda url: sources.get_page(url,
user_agent=..., main_only=True)`. Nothing else changes: `build_snapshot`
still takes `fetch_page`, and a page that cannot be fetched is still left
out with a warning.

A snapshot built before v31 holds each page's whole text, so the same
snapshot built again now has other bytes. A run uses its snapshots as they
were built; `runs/2026-11-macro-calibration-run` hashed its own in
`inputs.sha256`.
