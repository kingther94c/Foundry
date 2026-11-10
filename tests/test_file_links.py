"""A file the model cites opens in the user's editor (D-106), as codex's
``file_opener``: ``src/app.py:42`` in inline code becomes a terminal
hyperlink to that file at that line.

What carries it: the link is made only for an existing file inside the
workspace, and its target is built from that file's own path -- never taken
from the text -- so a citation cannot point anywhere but at the file it
shows. A Markdown link stays text, its address the model's (D-070).
"""

from __future__ import annotations

import io
import urllib.parse

import pytest
from rich.console import Console
from rich.text import Text

from foundry.cli.file_links import file_link
from foundry.cli.markdown import MarkdownLines, inline
from foundry.cli.render import Renderer, sanitize
from foundry.cli.repl import RunTask, run_task
from foundry.cli.wiring import _close
from foundry.core.config import load_config, settings_layer
from foundry.core.errors import ConfigError
from foundry.core.events import MessageDelta, Notice, TerminalStatus
from foundry.core.prompts import base_system_prompt

from test_resume import _wire, repo, turn  # noqa: F401  (repo is a fixture)


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "work"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "a#b%c.py").write_text("y = 1\n", encoding="utf-8")
    (root / "README").write_text("hi\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("z = 1\n", encoding="utf-8")
    return root


def _uri(path, suffix="", opener="vscode"):
    where = urllib.parse.quote(path.resolve().as_posix(), safe="/:")
    return f"{opener}://file/{where.lstrip('/')}{suffix}"


# --- what a citation links to ----------------------------------------------------------


@pytest.mark.parametrize("text, suffix", [
    ("src/app.py", ""),
    ("src/app.py:42", ":42"),
    ("src/app.py:42:7", ":42:7"),
    ("src/app.py:10-20", ":10"),
    ("src\\app.py:3", ":3"),
    ("./src/../src/app.py", ""),
])
def test_a_file_in_the_workspace_is_linked(workspace, text, suffix):
    assert file_link(text, workspace, "vscode") == _uri(workspace / "src" / "app.py", suffix)


def test_a_workspace_given_relative(workspace, monkeypatch):
    monkeypatch.chdir(workspace.parent)
    assert file_link("src/app.py", workspace.relative_to(workspace.parent),
                     "vscode") == _uri(workspace / "src" / "app.py")


def test_a_file_without_an_extension(workspace):
    assert file_link("README:1", workspace, "vscode") == _uri(workspace / "README", ":1")


def test_an_absolute_path_inside_the_workspace(workspace):
    path = (workspace / "src" / "app.py").resolve()
    assert file_link(f"{path}:5", workspace, "vscode") == _uri(path, ":5")
    assert file_link(f"{path.as_posix()}:5", workspace, "vscode") == _uri(path, ":5")


def test_the_target_is_quoted(workspace):
    link = file_link("src/a#b%c.py:2", workspace, "vscode")
    assert link.endswith("/src/a%23b%25c.py:2") and link == _uri(workspace / "src" / "a#b%c.py",
                                                                 ":2")


@pytest.mark.parametrize("opener", ["vscode-insiders", "windsurf", "cursor"])
def test_the_editor_is_the_users(workspace, opener):
    assert file_link("src/app.py:1", workspace, opener) == _uri(workspace / "src" / "app.py",
                                                                ":1", opener)


@pytest.mark.parametrize("text", [
    "src/missing.py",               # nothing there
    "src",                          # a directory
    "../outside.py",                # outside the workspace
    "x == y",                       # code, not a path
    "src/app.py:",                  # not a line
    "src/app.py:42:",
    "src/app.py:x",
    "C:src/app.py",                 # a drive's current directory
    "./" * 150 + "src/app.py",      # longer than a citation is
    "",
])
def test_anything_else_is_not(workspace, text):
    assert file_link(text, workspace, "vscode") is None


def test_an_absolute_path_outside_the_workspace_is_not(workspace):
    outside = (workspace.parent / "outside.py").resolve()
    assert file_link(str(outside), workspace, "vscode") is None


def test_none_links_nothing(workspace):
    assert file_link("src/app.py:1", workspace, "none") is None


def test_a_name_windows_cannot_take_is_not_a_file(workspace):
    assert file_link('src/app.py"', workspace, "vscode") is None
    assert file_link("src/*.py", workspace, "vscode") is None


# --- as the answer is shown -------------------------------------------------------------


def _links(text: Text):
    return [(text.plain[span.start:span.end], span.style.link) for span in text.spans
            if not isinstance(span.style, str) and span.style.link]


def test_inline_code_is_linked_when_it_cites_a_file():
    asked = []

    def link(code):
        asked.append(code)
        return "vscode://file/x" if code == "a.py" else None

    text = inline("see `a.py` and `b()` and [a.py](a.py)", link=link)
    assert text.plain == "see a.py and b() and a.py (a.py)"
    assert _links(text) == [("a.py", "vscode://file/x")]
    assert [span.style.color.name for span in text.spans
            if not isinstance(span.style, str)] == ["cyan"]
    assert asked == ["a.py", "b()"]
    assert [span.style for span in text.spans if text.plain[span.start:span.end] == "b()"] == ["cyan"]


def test_without_a_linker_nothing_is_linked():
    assert _links(inline("see `a.py`")) == []


def test_headings_quotes_and_lines_are_linked_but_not_code_blocks():
    markdown = MarkdownLines(sanitize, lambda code: f"vscode://file/{code}")
    out = markdown.feed("# In `a.py`\n> `b.py`\nthen `c.py`\n```\n`d.py`\n```\n")
    links = [link for renderable in out if isinstance(renderable, Text)
             for _, link in _links(renderable)]
    assert links == ["vscode://file/a.py", "vscode://file/b.py", "vscode://file/c.py"]


def _terminal():
    # As Windows Terminal is: rich writes no hyperlink to a console it takes
    # for the legacy one, which shows none.
    return Console(file=io.StringIO(), force_terminal=True, width=100, color_system="truecolor",
                   legacy_windows=False)


def _render(workspace, text, **settings):
    console = _terminal()
    renderer = Renderer(console=console, workspace=workspace, **settings)
    renderer.handle(MessageDelta(text=text))
    renderer.handle(Notice("next"))
    return console.file.getvalue()


def test_on_a_terminal_a_cited_file_is_a_hyperlink(workspace):
    shown = _render(workspace, "the fix is in `src/app.py:1`, not `src/gone.py`\n")
    assert shown.count("\x1b]8;") == 2           # one link: its start and its end
    assert _uri(workspace / "src" / "app.py", ":1") in shown


def test_the_editor_is_the_one_configured(workspace):
    shown = _render(workspace, "`src/app.py`\n", file_opener="cursor")
    assert _uri(workspace / "src" / "app.py", opener="cursor") in shown and "vscode:" not in shown


def test_no_links_when_the_opener_is_none(workspace):
    assert "\x1b]8;" not in _render(workspace, "`src/app.py`\n", file_opener="none")


def test_no_links_without_a_workspace(workspace, monkeypatch):
    monkeypatch.chdir(workspace)
    assert "\x1b]8;" not in _render(None, "`src/app.py`\n")


def test_a_markdown_link_is_still_never_a_hyperlink(workspace):
    shown = _render(workspace, "[app](src/app.py) [x](vscode://file/C:/Windows/win.ini)\n")
    assert "\x1b]8;" not in shown


def test_off_a_terminal_nothing_is_linked(workspace):
    console = Console(file=io.StringIO(), width=100, legacy_windows=False)
    renderer = Renderer(console=console, workspace=workspace)
    renderer.handle(MessageDelta(text="`src/app.py`\n"))
    renderer.handle(Notice("next"))
    assert "\x1b]8;" not in console.file.getvalue()


def test_a_session_links_what_its_model_cites(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="Changed `a.py:1`.")],
                   overrides={"file_opener": "windsurf"})
    wiring.renderer.console = _terminal()
    assert wiring.renderer.workspace == wiring.workspace.root
    run_task(wiring, RunTask("check"))
    assert _uri(repo / "a.py", ":1", "windsurf") in wiring.renderer.console.file.getvalue()
    _close(wiring, TerminalStatus.COMPLETED, "test")


def test_the_model_is_told_how_to_cite():
    assert ("When you mention a file in your reply, put its workspace path in backticks, "
            "with the line if one matters -- `src/app.py:42` -- and the user can open it "
            "from the terminal.") in " ".join(base_system_prompt().split())


# --- the setting ------------------------------------------------------------------------


@pytest.fixture()
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    monkeypatch.delenv("FOUNDRY_PROFILE", raising=False)
    return path


def _config(home, text):
    (home / "config.toml").write_text(text, encoding="utf-8")
    return load_config(home=home)


def test_vscode_unless_said(home):
    assert load_config(home=home).file_opener == "vscode"


@pytest.mark.parametrize("opener", ["vscode", "vscode-insiders", "windsurf", "cursor", "none"])
def test_each_of_codexs_openers(home, opener):
    config = _config(home, f'[runtime]\nfile_opener = "{opener}"\n')
    assert config.file_opener == opener
    assert config.explain("file_opener") == f"file_opener = {opener!r} (from user)"


@pytest.mark.parametrize("value", ['"notepad"', "true", '"VSCode"'])
def test_another_is_refused(home, value):
    with pytest.raises(ConfigError, match="file_opener must be one of vscode, vscode-insiders, "
                                          "windsurf, cursor, none"):
        _config(home, f"[runtime]\nfile_opener = {value}\n")


def test_codexs_place_for_it_is_told_ours(home):
    with pytest.raises(ConfigError, match=r"no setting 'file_opener'; it is a runtime setting: "
                                          r"put it under \[runtime\]"):
        _config(home, 'file_opener = "cursor"\n')


@pytest.mark.parametrize("name", ["file_opener", "runtime.file_opener"])
def test_from_the_command_line_by_codexs_name_or_ours(home, name):
    settings = settings_layer({name: "cursor"})
    assert load_config(home=home, settings=settings).file_opener == "cursor"


def test_in_a_profile(home):
    _config(home, '[profiles.c.runtime]\nfile_opener = "cursor"\n')
    assert load_config(home=home, profile="c").file_opener == "cursor"
