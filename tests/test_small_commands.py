"""codex's smaller commands (D-130): /recap, /clear, /rollout and /pwd,
and what Foundry says of those it has another way for.
"""

from __future__ import annotations

import builtins

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.slash_commands import RECAP, RECAP_READ_ONLY
from foundry.core.session import EventType, SessionStore

from test_fork import _model
from test_resume import PATCH_A, _wire, call, fake_model, repo, turn  # noqa: F401  (fixtures)
from test_slash_commands import _repl, _shown


def _typed(monkeypatch, *lines):
    left = iter(lines)
    monkeypatch.setattr(builtins, "input", lambda *a: next(left))


def test_recap_sums_the_conversation_up_and_changes_nothing(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(text="A is 1"),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="you asked what A is; it is 1"),
    ])
    _repl(wiring, "what is A?", "/recap")
    seen = wiring.runtime.backend.seen
    assert seen[1].messages[-1].blocks[0].text == RECAP
    refused = seen[2].messages[-1].blocks[0]
    assert refused.is_error and RECAP_READ_ONLY in refused.content
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    typed = [r.payload.get("typed") for r in SessionStore.read_records(wiring.session.path)
             if r.type == EventType.USER_INPUT]
    assert typed == [None, "/recap"]
    # An answer in the conversation, unlike a compaction.
    assert wiring.runtime.context.history[-1].text_content == "you asked what A is; it is 1"


def test_clear_clears_the_terminal_and_starts_afresh(repo, tmp_path, monkeypatch, capsys,
                                                     fake_model):
    cleared: list[bool] = []
    monkeypatch.setattr(Console, "clear", lambda self, *a, **k: cleared.append(True))
    home, _ = _model(tmp_path, monkeypatch, fake_model, "A is 1")
    _typed(monkeypatch, "what is A?", "/clear", "/exit")
    assert app.main(["run", "--workspace", str(repo)]) == 13
    assert cleared == [True]
    out = capsys.readouterr().out
    [first] = [s for s in (home / "sessions").iterdir() if f"new session {s.name}" not in out]
    assert len(list((home / "sessions").iterdir())) == 2
    reasons = [r.payload["reason"] for r in SessionStore.read_records(first / "events.jsonl")
               if r.type == EventType.TERMINATION]
    assert reasons == ["the user started a new session"]


def test_rollout_and_pwd(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, "/rollout", "/pwd"))
    assert str(wiring.session.path) in shown and str(wiring.workspace.root) in shown


@pytest.mark.parametrize("name, said", [
    ("permissions", "here it is /mode NAME"),
    ("cd", "foundry -C DIR starts one in another"),
    ("usage", "/status shows this session's tokens"),
    ("debug-config", "foundry doctor checks the configuration"),
    ("ps", "Foundry runs no background terminals"),
    ("stop", "Ctrl+C stops the running task"),
])
def test_codex_s_other_commands_are_told_theirs(repo, tmp_path, name, said):
    shown = _shown(_repl(_wire(repo, tmp_path / "home", []), f"/{name}"))
    assert f"/{name} is codex's; " in shown and said in shown


def test_help_lists_them(repo, tmp_path):
    shown = _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))
    for name in ("recap", "clear", "rollout", "pwd"):
        assert f"/{name} " in shown
