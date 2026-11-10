"""The terminal's title (D-139), as codex's: an interactive session says in
the title what it is doing -- working, or waiting on your answer -- with its
name and its project's, from items you pick; `/title` picks them for one
session.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from foundry.cli.render import Renderer
from foundry.cli.title import ACTION_REQUIRED, MAX_TITLE, clean, compose
from foundry.cli.wiring import _close, build
from foundry.core.events import ApprovalChoice, ApprovalKind, ApprovalRequest, TerminalStatus
from foundry.core.config import load_config
from foundry.core.config_sections import DEFAULT_TERMINAL_TITLE
from foundry.core.errors import ConfigError

from test_resume import OVERRIDES, _wire, repo  # noqa: F401  (fixtures)
from test_slash_commands import _repl, _shown

OSC = "\x1b]0;"
FACTS = {"app-name": "Foundry", "project-name": "shop", "current-dir": "C:\\src\\shop",
         "thread-name": "fix-login", "thread-id": "20261108T101010-abcd", "model": "gpt-5"}


def _titles(out: io.StringIO) -> list[str]:
    return [part.split("\x07", 1)[0] for part in out.getvalue().split(OSC)[1:]]


def _renderer(items=DEFAULT_TERMINAL_TITLE, facts=FACTS, terminal=True):
    out = io.StringIO()
    console = Console(file=out, force_terminal=terminal, legacy_windows=False, width=100)
    renderer = Renderer(console=console, title_items=tuple(items),
                        title_facts=lambda: dict(facts))
    return renderer, out


# --- what the title says ------------------------------------------------------


@pytest.mark.parametrize("state, title", [
    ("ready", "fix-login | shop"),
    ("working", "Working fix-login | shop"),
    ("action", f"{ACTION_REQUIRED} fix-login | shop"),
])
def test_the_default_says_what_it_is_doing_then_names(state, title):
    assert compose(DEFAULT_TERMINAL_TITLE, FACTS, state) == title


def test_an_unnamed_session_leaves_its_name_out():
    assert compose(DEFAULT_TERMINAL_TITLE, {**FACTS, "thread-name": None}, "ready") == "shop"


def test_the_other_items():
    items = ("app-name", "run-state", "model", "thread-id", "current-dir")
    assert compose(items, FACTS, "action") == (
        "Foundry | Waiting | gpt-5 | 20261108T101010-abcd | C:\\src\\shop")
    assert compose(("run-state",), FACTS, "ready") == "Ready"
    assert compose(("run-state",), FACTS, "working") == "Working"


def test_nothing_steers_the_sequence_it_is_written_in():
    assert clean("evil\x07\x1b]0;owned\x1b\\ name") == "evil]0;owned\\ name"
    assert clean("a\u202ecba\u200b\n\tb  c") == "acba b c"
    assert clean("  padded  name  ") == "padded name"
    assert len(clean("x" * 1000)) == MAX_TITLE
    assert compose(("thread-name", "project-name"),
                   {"thread-name": "\x1b\x07", "project-name": "shop"}, "ready") == "shop"


# --- when it is written -------------------------------------------------------


def test_written_as_a_task_runs_and_once_for_each_change():
    renderer, out = _renderer()
    renderer.title("ready")
    renderer.work("working")
    renderer.work("running")
    renderer.idle()
    renderer.title("ready")
    assert _titles(out) == ["fix-login | shop", "Working fix-login | shop", "fix-login | shop"]


def test_an_approval_asks_for_you_in_the_title(monkeypatch):
    renderer, out = _renderer()
    renderer.work("working")
    monkeypatch.setattr(renderer.console, "input", lambda *a, **k: "n")
    request = ApprovalRequest(request_id="r1", tool="run_command", display="rm -rf build",
                              reason="asks", approval_kind=ApprovalKind.COMMAND)
    assert renderer.ask_approval(request) is ApprovalChoice.DENY
    assert _titles(out) == ["Working fix-login | shop", f"{ACTION_REQUIRED} fix-login | shop",
                            "Working fix-login | shop"]


def test_not_on_a_terminal():
    renderer, out = _renderer(terminal=False)
    renderer.title("ready")
    renderer.work("working")
    assert _titles(out) == []


def test_not_on_a_dumb_terminal():
    """Rich would write it there: a dumb terminal prints the sequence."""
    out = io.StringIO()
    console = Console(file=out, force_terminal=True, legacy_windows=False,
                      _environ={"TERM": "dumb"})
    renderer = Renderer(console=console, title_items=DEFAULT_TERMINAL_TITLE,
                        title_facts=lambda: dict(FACTS))
    renderer.title("ready")
    assert _titles(out) == []


def test_cleared_as_it_ends_only_if_it_was_set():
    renderer, out = _renderer()
    renderer.clear_title()
    assert _titles(out) == []
    renderer.title("ready")
    renderer.clear_title()
    renderer.clear_title()
    assert _titles(out) == ["fix-login | shop", ""]


def test_no_items_no_title():
    renderer, out = _renderer(items=())
    renderer.title("ready")
    renderer.work("working")
    assert _titles(out) == []


# --- which items --------------------------------------------------------------


def _config(tmp_path, text):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return load_config(None, home=home)


def test_the_default_is_codex_s(tmp_path):
    assert _config(tmp_path, "").terminal_title == ("activity", "thread-name", "project-name")


def test_codex_s_ids_and_aliases(tmp_path):
    config = _config(tmp_path, '[runtime]\nterminal_title = ["spinner", "project", '
                               '"status", "session-name", "model", "project-name"]\n')
    assert config.terminal_title == ("activity", "project-name", "run-state", "thread-name",
                                     "model")
    assert _config(tmp_path, "[runtime]\nterminal_title = []\n").terminal_title == ()


@pytest.mark.parametrize("value, said", [
    ('["git-branch"]', "no item 'git-branch'; the items are app-name"),
    ('"project-name"', "terminal_title must be a list of items"),
    ("[1]", "terminal_title must be a list of items"),
])
def test_refused(tmp_path, value, said):
    with pytest.raises(ConfigError, match=said):
        _config(tmp_path, f"[runtime]\nterminal_title = {value}\n")


def test_codex_s_table_is_told_where_it_lives(tmp_path):
    with pytest.raises(ConfigError, match="runtime.terminal_title"):
        _config(tmp_path, '[tui]\nterminal_title = ["project"]\n')


def test_a_session_at_a_terminal_writes_one_and_exec_does_not(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert wiring.renderer.title_items == DEFAULT_TERMINAL_TITLE
    facts = wiring.renderer.title_facts()
    assert facts["project-name"] == repo.name and not facts["thread-name"]
    assert facts["thread-id"] == wiring.session.session_id
    assert facts["model"] == wiring.runtime.model
    unattended = build(repo, home=tmp_path / "home", interactive=False,
                           overrides=OVERRIDES, console=Console(file=io.StringIO()))
    try:
        assert unattended.renderer.title_items == ()
    finally:
        _close(unattended, TerminalStatus.CANCELLED, "done")


# --- /title -------------------------------------------------------------------


def test_title_alone_says_what_it_shows_and_what_it_can(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, "/title"))
    assert "activity, thread-name, project-name" in shown
    assert "run-state" in shown and "[runtime] terminal_title" in shown


def test_title_picks_them_for_the_session(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, "/title model project"))
    assert wiring.renderer.title_items == ("model", "project-name")
    assert "model, project-name" in shown


def test_title_none_turns_it_off(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    _repl(wiring, "/title none")
    assert wiring.renderer.title_items == ()


def test_title_refuses_what_it_does_not_know(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, "/title model git-branch"))
    assert "no item 'git-branch'" in shown
    assert wiring.renderer.title_items == DEFAULT_TERMINAL_TITLE


def test_the_title_follows_a_rename(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    _repl(wiring, "/rename fix-login")
    assert wiring.renderer.title_facts()["thread-name"] == "fix-login"
