"""``-C DIR`` / ``--cd DIR`` (D-072): the directory to work in, as codex
spells it, beside Foundry's own ``--workspace``.

What carries the feature: it is the same setting under another name, on
every command that takes one -- so the workspace a run uses, and every check
made against it, is the one the user named, whichever spelling they typed.
"""

from __future__ import annotations

import pytest

from foundry.cli import app
from foundry.core.session import EventType, SessionStore

from test_cli_flags import _exec_home
from test_resume import fake_model, repo  # noqa: F401  (fixtures)


@pytest.mark.parametrize("command", [
    ["run"], ["resume"], ["undo"], ["exec", "a task"], ["record", "a task", "--output", "f.json"],
])
@pytest.mark.parametrize("flag", ["-C", "--cd", "--workspace"])
def test_every_command_takes_it_by_every_name(command, flag):
    args = app.build_parser().parse_args([*command, flag, "D:/elsewhere"])
    assert args.workspace == "D:/elsewhere"
    assert app.build_parser().parse_args(command).workspace == "."


def test_it_is_not_the_setting_flag():
    args = app.build_parser().parse_args(["run", "-c", "runtime.max_tool_rounds=5", "-C", "x"])
    assert args.workspace == "x" and args.config == ["runtime.max_tool_rounds=5"]


def test_it_may_come_first(monkeypatch):
    seen = []
    monkeypatch.setattr("foundry.cli.app.cmd_session", lambda args: seen.append(args) or 0)
    assert app.main(["-C", "../other", "fix it"]) == 0
    assert seen[0].command == "run" and seen[0].workspace == "../other"
    assert seen[0].task == "fix it"


def test_help_names_all_three(capsys):
    with pytest.raises(SystemExit):
        app.build_parser().parse_args(["exec", "--help"])
    assert "-C WORKSPACE, --cd WORKSPACE, --workspace WORKSPACE" in capsys.readouterr().out


def test_exec_works_where_it_says(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    home = _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [text_turn("hi"), text_turn("still hi")], 0
    monkeypatch.chdir(tmp_path)                       # not the repository
    app.main(["exec", "hello", "-C", str(repo)])
    capsys.readouterr()
    [journal] = (home / "sessions").glob("*/events.jsonl")
    meta = next(r for r in SessionStore.read_records(journal)
                if r.type == EventType.SESSION_META)
    assert meta.payload["workspace"].lower() == str(repo.resolve()).lower()
