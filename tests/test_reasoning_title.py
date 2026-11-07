"""After a reasoning summary the working line goes back up, saying what the
summary says the model is doing (D-109), as codex's status line shows
``Exploring the parser`` while the model goes on.

What carries it: the title is the summary's first bold words, stripped of
control sequences and shown as text, never as markup; the line it is on is
the working line (D-069), so it is down before anything else reaches the
screen, and never drawn off a terminal.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from foundry.cli.render import Renderer, reasoning_title
from foundry.core.events import MessageDelta, Reasoning, ToolBegin, TurnStarted


def _terminal():
    return Console(file=io.StringIO(), force_terminal=True, width=100, color_system=None)


@pytest.fixture()
def renderer():
    renderer = Renderer(console=_terminal())
    yield renderer
    renderer.idle()


@pytest.mark.parametrize("text, title", [
    ("**Exploring the parser**\n\nI will read the tokenizer first.", "Exploring the parser"),
    ("Plain thoughts, no title.", ""),
    ("first **this** then **that**", "this"),
    ("**  spaced \t out  **", "spaced out"),
    ("**across\ntwo lines**", ""),
    ("**" + "x" * 80 + "**", "x" * 80),
    ("**" + "x" * 81 + "**", ""),
    ("****", ""),
    ("**Fix \x1b[31mthe test**", "Fix the test"),
    ("**[red]Loud[/red]**", "[red]Loud[/red]"),
])
def test_the_title_is_the_first_bold_words(text, title):
    assert reasoning_title(text) == title


def test_the_line_goes_back_up_with_the_title(renderer):
    renderer.handle(TurnStarted(1))
    before = renderer._live
    renderer.handle(Reasoning("**Exploring the parser**\n\nThe tokenizer first."))
    assert not before.is_started
    assert renderer._live is not None and renderer._live.is_started
    assert renderer._working.what == "Exploring the parser"
    renderer.idle()
    shown = renderer.console.file.getvalue()
    assert shown.index("thinking") < shown.index("Exploring the parser (0s, Ctrl+C to stop)")


def test_without_a_title_it_says_working(renderer):
    renderer.handle(Reasoning("Just thinking."))
    assert renderer._working.what == "working"


def test_markup_in_the_title_is_text(renderer):
    renderer.handle(Reasoning("**[red]Loud[/red]**"))
    renderer.idle()
    assert "[red]Loud[/red] (0s, Ctrl+C to stop)" in renderer.console.file.getvalue()


def test_what_comes_next_takes_it_down(renderer):
    renderer.handle(Reasoning("**Planning**"))
    renderer.handle(MessageDelta(text="Here is the plan."))
    assert renderer._live is None
    renderer.handle(Reasoning("**Checking**"))
    renderer.handle(ToolBegin("c1", "run_command", "pytest -q"))
    assert renderer._working.what == "running"


def test_never_off_a_terminal():
    renderer = Renderer(console=Console(file=io.StringIO(), width=100))
    renderer.handle(Reasoning("**Exploring**\n\nbody"))
    assert renderer._live is None
    assert "Exploring" in renderer.console.file.getvalue()
