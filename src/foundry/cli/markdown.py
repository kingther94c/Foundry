"""The model's answer as Markdown, a line at a time (D-070).

codex renders the model's Markdown as it streams, committing a line when it
ends. This does the same with a small, fixed subset -- headings, emphasis,
inline code, links, quotes, rules, and fenced code highlighted once the fence
closes -- and leaves everything else as the model wrote it (lists, tables).

The text is the model's, so it is untrusted: control sequences are stripped
from each whole line, a carriage return is dropped (it would let a line
overwrite itself on screen), and what is printed is built as rich ``Text``,
never parsed as console markup. A link is shown as its text and its address;
it never becomes a terminal hyperlink, whose target the reader cannot see.
Inline code that cites a file may (D-106): that target is the file shown.
"""

from __future__ import annotations

import re
from typing import Callable

from rich.console import RenderableType
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]{0,40})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_QUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
_RULE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
# Inline code first, so nothing inside it is styled; then bold, links, and
# *italic* -- not _italic_, which would eat the underscores of snake_case.
_INLINE = re.compile(
    r"`([^`\n]+)`"
    r"|\*\*(?=\S)(.+?)(?<=\S)\*\*"
    r"|\[([^\]\n]+)\]\(([^)\s]+)\)"
    r"|(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"
)


# A citation's link, or None: the file a span of inline code names (D-106).
Linker = Callable[[str], "str | None"]


def inline(text: str, style: str = "", link: Linker | None = None) -> Text:
    """One line's emphasis, inline code and links, as styled Text; inline
    code that cites a file is a link to it when ``link`` says where."""
    out = Text(style=style)
    at = 0
    for match in _INLINE.finditer(text):
        out.append(text[at:match.start()])
        code, bold, label, address, italic = match.groups()
        if code is not None:
            target = link(code) if link is not None else None
            out.append(code, style=Style(color="cyan", link=target) if target else "cyan")
        elif bold is not None:
            out.append(bold, style="bold")
        elif label is not None:
            out.append(label, style="underline")
            out.append(f" ({address})", style="dim")
        else:
            out.append(italic, style="italic")
        at = match.end()
    out.append(text[at:])
    return out


class MarkdownLines:
    """Turns streamed text into renderables, a whole line at a time.
    ``sanitize`` strips control sequences (the renderer's own, passed in)."""

    def __init__(self, sanitize: Callable[[str], str], link: Linker | None = None) -> None:
        self.sanitize = sanitize
        self.link = link
        self.pending = ""
        # An open code fence: its marker, its language, and its lines so far.
        self.fence: tuple[str, str, list[str]] | None = None

    def feed(self, text: str) -> list[RenderableType]:
        """The lines ``text`` completes; the rest waits for its newline."""
        self.pending += text
        *lines, self.pending = self.pending.split("\n")
        out: list[RenderableType] = []
        for line in lines:
            out.extend(self._line(self._clean(line)))
        return out

    def finish(self) -> list[RenderableType]:
        """The text has ended: what is still held, and a fence left open."""
        out: list[RenderableType] = []
        if self.pending:
            out.extend(self._line(self._clean(self.pending)))
            self.pending = ""
        if self.fence is not None:
            out.append(self._code())
        return out

    def _clean(self, line: str) -> str:
        # A carriage return goes too, though not here: rich's Text drops
        # every control character it is given.
        return self.sanitize(line)

    def _line(self, line: str) -> list[RenderableType]:
        if self.fence is not None:
            marker = self.fence[0]
            if line.strip().startswith(marker) and set(line.strip()) == {marker[0]}:
                return [self._code()]
            self.fence[2].append(line)
            return []
        fence = _FENCE.match(line)
        if fence:
            self.fence = (fence.group(1), fence.group(2), [])
            return []
        heading = _HEADING.match(line)
        if heading:
            style = "bold underline" if len(heading.group(1)) <= 2 else "bold"
            return [inline(heading.group(2), style, self.link)]
        if _RULE.match(line):
            return [Text(line.strip(), style="dim")]
        quote = _QUOTE.match(line)
        if quote:
            return [Text("| ", style="dim") + inline(quote.group(1), "italic", self.link)]
        return [inline(line, link=self.link)]

    def _code(self) -> RenderableType:
        _, language, lines = self.fence
        self.fence = None
        return Syntax("\n".join(lines), language or "text", theme="ansi_dark",
                      word_wrap=True)
