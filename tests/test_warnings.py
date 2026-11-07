"""`/warnings` (D-138), as codex's: the warnings and errors this session has
shown, again -- one may have scrolled away before anyone read it.
"""

from __future__ import annotations

import re

from foundry.cli.render import KEPT_WARNINGS
from foundry.core.events import ErrorEvent, Notice

from test_resume import _wire, repo, turn  # noqa: F401  (fixtures)
from test_slash_commands import _repl, _shown

STAMP = re.compile(r"\[dim\]\d\d:\d\d:\d\d\[/dim\] ")


def _quiet(repo, tmp_path):
    """A session with none kept yet: the one saying no credential is set
    is let go."""
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.renderer.warnings.clear()
    return wiring


def test_none_yet(repo, tmp_path):
    wiring = _quiet(repo, tmp_path)
    assert "no warnings or errors in this session" in _shown(_repl(wiring, "/warnings"))


def test_each_is_shown_again_in_order_with_its_time(repo, tmp_path):
    wiring = _quiet(repo, tmp_path)
    wiring.runtime._emit(Notice("the hook took too long", level="warning"))
    wiring.runtime._emit(Notice("just so you know"))
    wiring.runtime._emit(ErrorEvent("the endpoint refused the key", category="auth",
                                    fatal=False))
    shown = _shown(_repl(wiring, "/warnings"))
    lines = [line for line in shown.splitlines() if STAMP.match(line)]
    assert len(lines) == 2
    assert lines[0].endswith("[yellow]the hook took too long[/yellow]")
    assert lines[1].endswith("[red]error[/red] the endpoint refused the key")
    assert "just so you know" not in "\n".join(lines)


def test_one_from_the_start_of_the_session_is_kept(repo, tmp_path, monkeypatch):
    """No credential is set here: the session says so as it starts."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    wiring = _wire(repo, tmp_path / "home", [])
    assert [level for _, level, text in wiring.renderer.warnings
            if "no credentials" in text] == ["warning"]
    assert "no credentials" in _shown(_repl(wiring, "/warnings"))


def test_markup_in_one_is_shown_as_written(repo, tmp_path):
    wiring = _quiet(repo, tmp_path)
    wiring.runtime._emit(Notice("rule [bold]x[/bold] is unknown", level="warning"))
    assert "rule \\[bold]x\\[/bold] is unknown" in _shown(_repl(wiring, "/warnings"))


def test_only_the_latest_are_kept(repo, tmp_path):
    wiring = _quiet(repo, tmp_path)
    for n in range(KEPT_WARNINGS + 3):
        wiring.runtime._emit(Notice(f"warning number {n}", level="warning"))
    shown = _shown(_repl(wiring, "/warnings"))
    assert "3 earlier ones are not kept" in shown
    assert "warning number 2[" not in shown and "warning number 3[" in shown
    assert f"warning number {KEPT_WARNINGS + 2}[" in shown
    assert len(wiring.renderer.warnings) == KEPT_WARNINGS


def test_help_lists_it(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "/warnings" in _shown(_repl(wiring, "/help"))
