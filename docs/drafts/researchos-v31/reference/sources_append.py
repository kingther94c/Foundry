

# --- v31 reference: the main text of a fetched page ----------------------------------------------

def _is_main_tag(tag, attrs) -> bool:
    if tag == "main":
        return True
    return any(name == "role" and value is not None and value.strip().lower() == "main"
               for name, value in attrs)


class _MainTextParser(_TextParser):
    """_TextParser that also notes where the page's main elements start and end."""

    def __init__(self) -> None:
        super().__init__()
        self.mains = 0
        self.main_tag = None    # the first main element's tag name, while it is open
        self.main_depth = 0
        self.main_start = None  # index into parts after its start tag's own additions
        self.main_end = None    # index into parts before its end tag's own additions

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        kept = tag != "title" and tag not in _HIDDEN_TAGS and not self.hidden
        if not kept:
            return
        if self.main_tag is not None and tag == self.main_tag:
            self.main_depth += 1
        if _is_main_tag(tag, attrs):
            self.mains += 1
            if self.mains == 1:
                self.main_tag, self.main_depth = tag, 1
                self.main_start = len(self.parts)

    def handle_endtag(self, tag):
        kept = tag != "title" and tag not in _HIDDEN_TAGS and not self.hidden
        if kept and self.main_tag is not None and tag == self.main_tag:
            self.main_depth -= 1
            if self.main_depth == 0:
                self.main_end = len(self.parts)
                self.main_tag = None
        super().handle_endtag(tag)


def _text_lines(parts) -> str:
    lines = (_BLANKS.sub(" ", line).strip() for line in "".join(parts).split("\n"))
    return "\n".join(line for line in lines if line)


def html_to_text(html: str, *, main_only: bool = False) -> tuple[str, str]:
    parser = _MainTextParser()
    parser.feed(html)
    parser.close()
    title = " ".join("".join(parser.title).split())
    if main_only and parser.mains == 1:
        end = parser.main_end if parser.main_end is not None else len(parser.parts)
        text = _text_lines(parser.parts[parser.main_start:end])
        if text:
            return title, text
    return title, _text_lines(parser.parts)


def get_page(url: str, *, user_agent: str, transport=None, main_only: bool = False) -> FetchedPage:
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
    return FetchedPage(url, *html_to_text(text, main_only=main_only))
