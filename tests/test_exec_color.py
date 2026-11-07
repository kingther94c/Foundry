"""`exec --color always|never|auto` (D-136), as codex exec's: what an
unattended run says on stderr, coloured on a terminal by default, always
into a pipe or a CI log when asked, never when not wanted. stdout -- the
result, or the JSON stream -- is never coloured.
"""

from __future__ import annotations

import json
import re

import pytest

from foundry.cli import app

from test_cli_e2e import tool_turn
from test_cli_flags import _exec_home
from test_resume import fake_model, repo  # noqa: F401 (fixtures)

ESC = "\x1b["
COLOUR = re.compile(r"\x1b\[(?:\d+;)*3[0-7](?:;\d+)*m")
DONE = tool_turn("finish", {"status": "completed", "summary": "done", "claims": []}, "f1")


def _run(tmp_path, monkeypatch, capsys, fake_model, repo, *argv, command=("exec", "fix it")):
    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [DONE], 0
    assert app.main([*command, *argv, "-C", str(repo)]) == 0
    return capsys.readouterr()


def test_always_colours_a_pipe(repo, tmp_path, monkeypatch, capsys, fake_model):
    said = _run(tmp_path, monkeypatch, capsys, fake_model, repo, "--color", "always")
    assert ESC in said.err and "session" in said.err
    assert ESC not in said.out and said.out.strip() == "done"


def test_always_wins_over_no_color(repo, tmp_path, monkeypatch, capsys, fake_model):
    monkeypatch.setenv("NO_COLOR", "1")
    said = _run(tmp_path, monkeypatch, capsys, fake_model, repo, "--color", "always")
    assert COLOUR.search(said.err), "NO_COLOR keeps dim and bold, but drops colours"


@pytest.mark.parametrize("argv", [("--color", "never"), ("--color", "auto"), ()],
                         ids=["never", "auto", "default"])
def test_a_pipe_is_not_coloured_otherwise(repo, tmp_path, monkeypatch, capsys, fake_model,
                                          argv):
    said = _run(tmp_path, monkeypatch, capsys, fake_model, repo, *argv)
    assert ESC not in said.err and "session" in said.err


def test_never_on_a_terminal_too(repo, tmp_path, monkeypatch, capsys, fake_model):
    from rich.console import Console

    made = []
    real = Console.__init__

    def init(self, *args, **kwargs):
        made.append(kwargs)
        real(self, *args, **kwargs)

    monkeypatch.setattr(Console, "__init__", init)
    _run(tmp_path, monkeypatch, capsys, fake_model, repo, "--color", "never")
    assert made[0] == {"stderr": True, "color_system": None}


def test_the_json_stream_is_never_coloured(repo, tmp_path, monkeypatch, capsys, fake_model):
    said = _run(tmp_path, monkeypatch, capsys, fake_model, repo, "--color", "always", "--json")
    events = [json.loads(line) for line in said.out.splitlines() if line]
    assert events[-1]["status"] == "completed" and ESC not in said.out


def test_a_review_takes_it_too(repo, tmp_path, monkeypatch, capsys, fake_model):
    said = _run(tmp_path, monkeypatch, capsys, fake_model, repo, "--color", "always",
                command=("review", "--uncommitted"))
    [reviewing] = [line for line in said.err.splitlines() if "reviewing" in line]
    assert ESC in reviewing


def test_only_the_three(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path / "home"))
    with pytest.raises(SystemExit):
        app.main(["exec", "--color", "sometimes", "x", "-C", str(repo)])
