"""How much of the context window is left, above the prompt (D-054).

codex keeps it in view; here it is a dim line above `foundry>` once the
conversation has begun, by the same estimate /status shows, and yellow when
little is left.
"""

from __future__ import annotations

import re

from foundry.cli import repl as repl_module
from foundry.cli.repl import CONTEXT_LOW_PERCENT, PROMPT, context_left, prompt_for, repl
from foundry.cli.wiring import _close
from foundry.core.events import TerminalStatus

from test_resume import _wire, repo, turn  # noqa: F401 (fixture)
from test_slash_commands import _Recorder


class _Prompted(_Recorder):
    """Keeps every prompt it was shown."""

    def __init__(self, lines):
        super().__init__(lines)
        self.prompts: list[str] = []

    def input(self, prompt=""):
        self.prompts.append(prompt)
        return super().input(prompt)


def _after_a_turn(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="A is 1")])
    wiring.runtime.run_turn("what is A?")
    return wiring


def test_a_new_session_shows_the_plain_prompt(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    try:
        assert prompt_for(wiring) == PROMPT
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "done")


def test_once_begun_the_prompt_says_how_much_is_left(repo, tmp_path):
    wiring = _after_a_turn(repo, tmp_path)
    try:
        context = wiring.runtime.context
        used, limit = context.estimated_context_tokens(), context.max_context_tokens
        assert context_left(wiring) == 100 - 100 * used // limit
        context.max_context_tokens = used * 4
        assert context_left(wiring) == 75
        assert prompt_for(wiring) == "\n[dim]75% context left[/dim]" + PROMPT
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "done")


def test_status_and_the_prompt_agree(repo, tmp_path):
    wiring = _after_a_turn(repo, tmp_path)
    typist = _Recorder(["/status", "/exit"])
    status = repl(wiring, typist)
    _close(wiring, status, "done")
    shown = "\n".join(typist.printed)
    used_percent = int(re.search(r"tokens \((\d+)%\)", shown).group(1))
    assert used_percent + context_left(wiring) == 100


def test_little_left_is_yellow_and_none_left_is_zero(repo, tmp_path):
    wiring = _after_a_turn(repo, tmp_path)
    try:
        context = wiring.runtime.context
        used = context.estimated_context_tokens()
        context.max_context_tokens = used * 100 // (100 - CONTEXT_LOW_PERCENT + 5)
        assert context_left(wiring) < CONTEXT_LOW_PERCENT
        assert prompt_for(wiring).startswith("\n[yellow]")
        context.max_context_tokens = used * 100 // (100 - CONTEXT_LOW_PERCENT)
        assert context_left(wiring) >= CONTEXT_LOW_PERCENT
        assert prompt_for(wiring).startswith("\n[dim]")
        context.max_context_tokens = used // 2
        assert context_left(wiring) == 0
        assert "0% context left" in prompt_for(wiring)
    finally:
        _close(wiring, TerminalStatus.CANCELLED, "done")


def test_the_repl_shows_it_from_the_second_prompt_on(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(repl_module, "input_waiting", lambda: False)
    wiring = _wire(repo, tmp_path / "home", [turn(text="A is 1")])
    typist = _Prompted(["what is A?", "/exit"])
    status = repl(wiring, typist)
    _close(wiring, status, "done")
    assert typist.prompts[0] == PROMPT
    assert re.match(r"\n\[dim\]\d+% context left\[/dim\]\n", typist.prompts[1])
