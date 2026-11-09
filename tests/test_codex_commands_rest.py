"""The rest of codex's slash commands (D-140): `/cwd` is `/pwd`, as codex
spells it too, and each one Foundry has no counterpart for -- or has another
way to -- is told so rather than called unknown.
"""

from __future__ import annotations

import pytest

from foundry.cli.repl import CODEX_COMMANDS, COMMANDS

from test_resume import _wire, repo  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _shown

# codex's commands, as its popup spells them (codex-rs/tui/src/slash_command.rs).
CODEX_S = {
    "model", "presets", "daybreak", "ide", "permissions", "keymap", "vim",
    "setup-default-sandbox", "experimental", "approve", "memories", "skills", "import",
    "hooks", "review", "rename", "new", "archive", "delete", "resume", "fork", "worktree",
    "app", "init", "compact", "config", "recap", "plan", "voice", "goal", "agents", "side",
    "btw", "copy", "export", "raw", "tui", "diff", "mention", "status", "daemon",
    "warnings", "cd", "pwd", "cwd", "usage", "debug-config", "title", "statusline", "theme",
    "pets", "pet", "mcp", "apps", "plugins", "logout", "quit", "exit", "feedback",
    "rollout", "ps", "stop", "clean", "clear", "subagents",
}


def test_cwd_is_pwd(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert str(wiring.workspace.root) in _shown(_repl(wiring, "/cwd"))


@pytest.mark.parametrize("name, told", [
    ("config", "settings are in config.toml in the Foundry folder"),
    ("vim", "the prompt edits with your terminal's own keys"),
    ("keymap", "the prompt edits with your terminal's own keys"),
    ("raw", "the terminal's own scrollback"),
    ("theme", "the terminal's own colours"),
    ("statusline", "the prompt shows the context left; /status"),
    ("ide", "mention a file with @path"),
    ("app", "Foundry runs in the terminal only"),
    ("apps", "foundry mcp add"),
    ("plugins", "foundry mcp add"),
    ("daemon", "no background server"),
    ("voice", "no voice input"),
    ("pets", "no terminal pet"),
    ("pet", "no terminal pet"),
    ("import", "AGENTS.md"),
    ("tui", "one terminal interface"),
    ("presets", "~/.foundry/prompts/NAME.md is /NAME"),
    ("experimental", "no experimental features"),
    ("agents", "ask for one in a message"),
    ("subagents", "ask for one in a message"),
    ("setup-default-sandbox", "Foundry has no sandbox"),
    ("worktree", "git worktree add"),
    ("clean", "Foundry runs no background terminals"),
    ("daybreak", "Foundry has no Daybreak"),
])
def test_each_is_told(repo, tmp_path, name, told):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, f"/{name}"))
    assert f"/{name} is codex's; " in shown and told in shown
    assert "unknown command" not in shown


def test_every_one_of_codex_s_is_foundry_s_or_told():
    """The debugging ones (/test-approval, /debug-m-*) are codex's own."""
    assert CODEX_S <= set(COMMANDS) | set(CODEX_COMMANDS)
