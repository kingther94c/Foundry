"""codex's slash commands, answered with what Foundry has instead (D-101).

`/approvals`, `/mention`, `/logout` and `/feedback` -- codex's -- were each
"unknown command"; most of codex's others (`/model`, `/review`, `/new`,
`/init`, `/compact`, `/undo`, `/diff`, `/status`, `/mcp`, `/quit`) are
Foundry's too. What it may not do: send the line to the model, or take a name
from the user's own prompts.
"""

from __future__ import annotations

import pytest

from foundry.cli.repl import CODEX_COMMANDS, COMMANDS
from foundry.core.policy.engine import Mode

from test_resume import _wire, repo  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _shown


@pytest.mark.parametrize("name, told", [
    ("approvals", "/approvals is codex's; here it is /mode NAME"),
    ("mention", "/mention is codex's; here a file is mentioned by writing @path"),
    ("logout", "/logout is codex's; here it is foundry logout, run in your shell"),
    ("feedback", "/feedback is codex's; Foundry has no feedback channel"),
])
def test_codex_s_command_is_told_what_to_use(repo, tmp_path, name, told):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, f"/{name}"))
    assert told in shown and "unknown command" not in shown
    assert wiring.runtime.backend.seen == []


def test_the_mode_names_told_are_foundry_s():
    for mode in Mode:
        assert mode.value in CODEX_COMMANDS["approvals"]


def test_none_of_them_is_a_command_here():
    """Were Foundry to have one, its hint would never be read."""
    assert not set(CODEX_COMMANDS) & set(COMMANDS)


def test_the_user_s_own_prompt_of_that_name_comes_first(repo, tmp_path):
    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "approvals.md").write_text("List what needs my approval.",
                                                   encoding="utf-8")
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/approvals"))
    assert "codex's" not in shown
    assert "sending your prompt" in shown


def test_anything_else_is_still_unknown(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "unknown command /approvalz; /help lists the commands" in _shown(
        _repl(wiring, "/approvalz"))
