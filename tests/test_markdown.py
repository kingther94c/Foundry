"""The model's answer as Markdown, a line at a time (D-070), as codex renders
it while it streams.

What carries the feature: the text is the model's, so on its way to the
screen it is stripped of control sequences -- a whole line at a time, so a
sequence split across two chunks is still caught -- and printed as Text,
never as console markup or a terminal hyperlink. Off a terminal it is printed
exactly as the model wrote it.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from foundry.cli import app
from foundry.cli.markdown import MarkdownLines, inline
from foundry.cli.render import Renderer, sanitize
from foundry.cli.repl import RunTask, run_task
from foundry.core.events import MessageDelta, Notice, TerminalStatus

from test_resume import _wire, repo, turn  # noqa: F401  (repo is a fixture)


def _lines(*chunks, finish=True):
    markdown = MarkdownLines(sanitize)
    out = []
    for chunk in chunks:
        out.extend(markdown.feed(chunk))
    if finish:
        out.extend(markdown.finish())
    return out


def _plain(renderables):
    return [r.plain if isinstance(r, Text) else r for r in renderables]


def _spans(text: Text):
    return {(text.plain[s.start:s.end], str(s.style)) for s in text.spans}


# --- a line at a time ---------------------------------------------------------------------


def test_a_line_waits_for_its_end():
    markdown = MarkdownLines(sanitize)
    assert markdown.feed("Hel") == []
    assert _plain(markdown.feed("lo\nwor")) == ["Hello"]
    assert _plain(markdown.finish()) == ["wor"]
    assert markdown.finish() == []


def test_an_empty_line_stays_one():
    assert _plain(_lines("a\n\nb\n")) == ["a", "", "b"]


# --- what is rendered ---------------------------------------------------------------------


@pytest.mark.parametrize("line, text, style", [
    ("# Title", "Title", "bold underline"),
    ("## Title ##", "Title", "bold underline"),
    ("### Sub", "Sub", "bold"),
    ("###### Deep", "Deep", "bold"),
])
def test_headings(line, text, style):
    [rendered] = _lines(line + "\n")
    assert rendered.plain == text and str(rendered.style) == style


@pytest.mark.parametrize("line", ["#hashtag", "####### seven", "    # indented code"])
def test_what_is_not_a_heading(line):
    [rendered] = _lines(line + "\n")
    assert rendered.plain == line and str(rendered.style) == ""


def test_emphasis_code_and_links():
    text = inline("a **bold** and *it* and `x = *y*` see [docs](https://e.x/a_b)")
    assert text.plain == "a bold and it and x = *y* see docs (https://e.x/a_b)"
    assert _spans(text) == {("bold", "bold"), ("it", "italic"), ("x = *y*", "cyan"),
                            ("docs", "underline"), (" (https://e.x/a_b)", "dim")}


@pytest.mark.parametrize("line", [
    "snake_case_name and __init__ stay as they are",
    "2 * 3 * 4 is arithmetic",
    "a*b*c is a glob",
    "** not bold **",
    "a lone ` backtick",
])
def test_what_is_not_emphasis(line):
    text = inline(line)
    assert text.plain == line and text.spans == []


def test_lists_and_tables_are_left_as_written():
    assert _plain(_lines("- one **x**\n1. two\n| a | b |\n")) == ["- one x", "1. two", "| a | b |"]


def test_a_quote_and_a_rule():
    quote, rule = _lines("> careful *now*\n---\n")
    assert quote.plain == "| careful now" and rule.plain == "---" and str(rule.style) == "dim"


def test_a_code_block_is_highlighted_once_it_closes():
    markdown = MarkdownLines(sanitize)
    assert markdown.feed("```python\nx = [1]\n# not a heading\n") == []
    block, after = markdown.feed("```\nafter\n")
    assert isinstance(block, Syntax) and block.code == "x = [1]\n# not a heading"
    assert block.lexer.name == "Python" and after.plain == "after"


@pytest.mark.parametrize("opening, closing", [("~~~", "~~~"), ("````", "````"), ("```", "`````")])
def test_fences_close_with_their_own_kind(opening, closing):
    out = _lines(f"{opening}\ncode\n```not a close\n{closing}\ntext\n")
    assert isinstance(out[0], Syntax) and _plain(out[1:]) == ["text"]
    if opening == "~~~":
        assert "```not a close" in out[0].code


def test_a_fence_left_open_is_shown_when_the_text_ends():
    [block] = _lines("```\nunfinished\n")
    assert isinstance(block, Syntax) and "unfinished" in block.code


def test_an_unknown_language_is_plain_code():
    [block] = _lines("```no-such-lang;rm -rf\nx\n```\n")
    assert isinstance(block, Syntax)


# --- what the model cannot do to the screen -----------------------------------------------


def test_a_control_sequence_split_across_chunks_is_still_stripped():
    [line] = _lines("copy \x1b", "]52;c;aGVsbG8=\x07me\n")
    assert line.plain == "copy me"


def test_a_carriage_return_cannot_overwrite_a_line():
    assert _plain(_lines("rm -rf /\rls -l\n")) == ["rm -rf /ls -l"]


def _terminal(color=True):
    return Console(file=io.StringIO(), force_terminal=True, width=100,
                   color_system="truecolor" if color else None)


def _render(*chunks, console=None):
    console = console or _terminal()
    renderer = Renderer(console=console)
    for chunk in chunks:
        renderer.handle(MessageDelta(text=chunk))
    renderer.handle(Notice("next"))
    return console.file.getvalue()


def test_markup_is_never_read_and_a_link_is_never_a_hyperlink():
    shown = _render("[red]x[/red] [link=https://evil.example]y[/link] [docs](https://e.x)\n")
    assert "\x1b]8;" not in shown                       # no terminal hyperlink
    plain = _render("[red]x[/red] [link=https://evil.example]y[/link]\n",
                    console=_terminal(color=False))
    assert "[red]x[/red] [link=https://evil.example]y[/link]" in plain


# --- the renderer ---------------------------------------------------------------------------


def test_on_a_terminal_the_answer_is_rendered():
    shown = _render("# Done\nThe **tests** pass.\n", "```python\nx = 1\n```\n",
                    console=_terminal(color=False))
    assert shown.splitlines() == ["Done", "The tests pass.", "x = 1", "next"]


def test_off_a_terminal_it_is_as_the_model_wrote_it():
    console = Console(file=io.StringIO(), width=100)
    shown = _render("# Done\nuse list[int] and **x**", console=console)
    assert shown == "# Done\nuse list[int] and **x**\nnext\n"


def test_the_last_line_is_printed_when_the_task_settles():
    console = _terminal(color=False)
    renderer = Renderer(console=console)
    renderer.handle(MessageDelta(text="almost **done**"))
    assert console.file.getvalue() == ""
    renderer.settle()
    assert console.file.getvalue() == "almost done\n"
    renderer.settle()
    assert console.file.getvalue() == "almost done\n"


def test_a_task_that_fails_mid_answer_still_shows_what_came():
    renderer = Renderer(console=_terminal(color=False))

    def run_turn(text, images, typed=None):
        renderer.handle(MessageDelta(text="I found the **cause**"))
        raise RuntimeError("the connection dropped")

    wiring = SimpleNamespace(runtime=SimpleNamespace(policy=SimpleNamespace(read_only=""),
                                                     run_turn=run_turn),
                             note="", images=[], renderer=renderer)
    with pytest.raises(RuntimeError):
        run_task(wiring, RunTask("why?"))
    assert renderer.console.file.getvalue() == "I found the cause\n"


def test_each_answer_starts_afresh():
    console = _terminal(color=False)
    renderer = Renderer(console=console)
    renderer.handle(MessageDelta(text="```\nopen fence"))
    renderer.handle(Notice("between"))
    renderer.handle(MessageDelta(text="# Heading\n"))
    renderer.settle()
    assert console.file.getvalue().splitlines() == ["open fence", "between", "Heading"]


def test_a_session_on_a_terminal(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="## Summary\nAll **green**.")])
    wiring.renderer.console = _terminal(color=False)
    run_task(wiring, RunTask("check"))
    shown = wiring.renderer.console.file.getvalue()
    assert "Summary\nAll green." in shown and "**" not in shown and "##" not in shown
    app._close(wiring, TerminalStatus.COMPLETED, "test")
