"""What an interactive session says as it ends (D-059): the tokens it used,
and how to continue it, as codex prints them."""

from __future__ import annotations

import builtins

from foundry.cli import app
from foundry.cli.wiring import _close, farewell
from foundry.core.conversation import Usage
from foundry.core.events import TerminalStatus

from test_resume import _wire, repo, turn  # noqa: F401 (fixture)


def _after_a_turn(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="done")])
    wiring.runtime.run_turn("do it")
    return wiring


def test_it_says_the_tokens_and_how_to_continue(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(repo)
    wiring = _after_a_turn(repo, tmp_path)
    _close(wiring, TerminalStatus.CANCELLED, "done")
    assert farewell(wiring) == ("tokens used: 10 in, 5 out. To continue this session: "
                                    f"foundry resume {wiring.session.session_id}")


def test_from_another_folder_the_command_names_the_workspace(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiring = _after_a_turn(repo, tmp_path)
    _close(wiring, TerminalStatus.CANCELLED, "done")
    assert farewell(wiring).endswith(
        f'foundry resume {wiring.session.session_id} --workspace "{wiring.workspace.root}"')


def test_cached_input_is_said_when_there_is_some(repo, tmp_path):
    wiring = _after_a_turn(repo, tmp_path)
    wiring.runtime.context.session_usage = Usage(12_345, 678, 4_000)
    _close(wiring, TerminalStatus.CANCELLED, "done")
    assert farewell(wiring).startswith("tokens used: 12,345 in (4,000 cached), 678 out.")


def test_a_session_that_never_began_says_nothing(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    _close(wiring, TerminalStatus.CANCELLED, "done")
    assert farewell(wiring) == ""


def test_usage_alone_is_a_session_that_began(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.context.session_usage = Usage(5, 1)
    _close(wiring, TerminalStatus.CANCELLED, "done")
    assert farewell(wiring).startswith("tokens used: 5 in, 1 out.")


def test_an_incomplete_journal_is_not_offered_to_resume(repo, tmp_path):
    wiring = _after_a_turn(repo, tmp_path)
    _close(wiring, TerminalStatus.CANCELLED, "done")
    wiring.session.degraded = "OSError: the disk is full"
    text = farewell(wiring)
    assert "journal is incomplete (OSError: the disk is full), so it may not resume" in text
    assert "foundry resume" not in text


# --- through the command line ---------------------------------------------------------


def test_a_one_shot_run_says_it(repo, tmp_path, monkeypatch, capsys):
    wiring = _wire(repo, tmp_path / "home", [turn(text="done")])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    app.main(["run", "do it", "--workspace", str(repo)])
    assert f"foundry resume {wiring.session.session_id}" in capsys.readouterr().out


def test_a_session_left_at_once_ends_quietly(repo, tmp_path, monkeypatch, capsys):
    wiring = _wire(repo, tmp_path / "home", [])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: wiring)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "/exit")
    app.main(["run", "--workspace", str(repo)])
    assert capsys.readouterr().out.endswith("foundry> ")


def test_new_says_it_for_the_session_it_ends(repo, tmp_path, monkeypatch, capsys):
    first = _wire(repo, tmp_path / "home", [turn(text="done")])
    second = _wire(repo, tmp_path / "home", [])
    built = iter([first, second])
    monkeypatch.setattr("foundry.cli.session_commands.build", lambda *a, **k: next(built))
    lines = iter(["do it", "/new", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(lines))
    app.main(["run", "--workspace", str(repo)])
    out = capsys.readouterr().out
    [said] = [line for line in out.splitlines() if "To continue this session" in line]
    assert f"foundry resume {first.session.session_id} " in said
