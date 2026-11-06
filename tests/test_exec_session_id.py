"""`exec` says which session it records, first (D-060): what `exec --resume`
takes, as codex's exec reports its session id."""

from __future__ import annotations

import io
import json

from rich.console import Console

from foundry.cli import app
from foundry.cli.render import Renderer
from foundry.core.events import SessionStarted

from test_resume import fake_model, repo  # noqa: F401 (fixtures)


def _configure(home, httpd, monkeypatch):
    host, port = httpd.server_address
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))


def test_the_first_json_event_names_the_session_and_resume_takes_it(
        repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import text_turn, tool_turn

    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch)
    workspace = ["--workspace", str(repo)]

    handler.turns, handler.index = [text_turn("A is 1")], 0
    app.main(["exec", "what is A?", "--json", *workspace])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    first = events[0]
    assert first["kind"] == "session_started" and first["resumed"] is False
    assert first["workspace"] == str(repo.resolve()) and first["model"] == "fake"
    assert sum(e["kind"] == "session_started" for e in events) == 1
    session_id = first["session_id"]
    assert (tmp_path / "home" / "sessions" / session_id / "events.jsonl").is_file()

    handler.turns, handler.index = [tool_turn("finish", {"status": "completed",
                                                         "summary": "A is 1",
                                                         "claims": []}, "f1")], 0
    assert app.main(["exec", "--resume", session_id, "and B?", "--json", *workspace]) == 0
    again = json.loads(capsys.readouterr().out.splitlines()[0])
    assert again == {**first, "resumed": True}


def test_without_json_it_is_said_on_stderr(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch)
    handler.turns, handler.index = [text_turn("A is 1")], 0
    app.main(["exec", "what is A?", "--workspace", str(repo)])
    captured = capsys.readouterr()
    [session_dir] = (tmp_path / "home" / "sessions").iterdir()
    said = captured.err.index(f"session {session_dir.name}\n")
    assert said < captured.err.index("A is 1")       # before the run says anything
    assert session_dir.name not in captured.out


def test_the_renderer_says_a_resumed_one_is_resumed():
    out = io.StringIO()
    renderer = Renderer(console=Console(file=out, width=100))
    renderer.handle(SessionStarted(session_id="s1", workspace="w", model="m"))
    renderer.handle(SessionStarted(session_id="s1", workspace="w", model="m", resumed=True))
    assert out.getvalue() == "session s1\nresumed session s1\n"
