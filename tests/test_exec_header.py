"""An unattended run says what it runs with (D-092), as codex's exec opens
with its settings: in a CI log, which model, endpoint protocol, profile, mode
and effort produced what follows.

Said first, on stderr (never in the result on stdout), and in the
``session_started`` event of ``--json``. What it may not do: show a setting's
source text (a base URL, a header), or let a path's brackets or escape
sequences reach the terminal as markup.
"""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.render import Renderer, run_settings
from foundry.core.events import SessionStarted

from test_resume import fake_model, repo  # noqa: F401 (fixtures)


def _configure(home, httpd, monkeypatch, extra=""):
    host, port = httpd.server_address
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n'
        + extra, encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    return f"{host}:{port}"


def _exec(handler, *argv):
    from test_cli_e2e import text_turn

    handler.turns, handler.index = [text_turn("A is 1")], 0
    return app.main(["exec", "what is A?", *argv])


# --- --json --------------------------------------------------------------------------------


def test_the_first_event_carries_the_settings(repo, tmp_path, monkeypatch, capsys, fake_model):
    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch)
    _exec(handler, "--json", "--workspace", str(repo))
    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert {key: first[key] for key in ("protocol", "profile", "mode", "effort",
                                        "reasoning_summary", "verbosity")} == {
        "protocol": "openai_compat", "profile": "personal", "mode": "dont_ask",
        "effort": None, "reasoning_summary": None, "verbosity": None}


def test_what_was_chosen_is_what_it_carries(repo, tmp_path, monkeypatch, capsys, fake_model):
    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch,
               'effort = "high"\nverbosity = "low"\nreasoning_summary = "none"\n'
               '[profiles.ci.backend]\neffort = "minimal"\n')
    _exec(handler, "--json", "--mode", "plan", "--profile", "ci", "--workspace", str(repo))
    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert (first["profile"], first["mode"], first["effort"], first["verbosity"],
            first["reasoning_summary"]) == ("ci", "plan", "minimal", "low", "none")


# --- the terminal --------------------------------------------------------------------------


def test_they_are_said_on_stderr_before_anything_else(repo, tmp_path, monkeypatch, capsys,
                                                     fake_model):
    httpd, handler = fake_model
    address = _configure(tmp_path / "home", httpd, monkeypatch)
    _exec(handler, "--workspace", str(repo))
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("session "))
    assert lines[start + 1:start + 5] == [
        f"  workspace {repo.resolve()}",
        "  model     fake (openai_compat, profile personal)",
        "  mode      dont_ask",
        "  effort    the API's default",
    ]
    assert captured.err.index("  effort") < captured.err.index("A is 1")
    assert "workspace" not in captured.out and "openai_compat" not in captured.out
    assert address not in captured.err      # the endpoint's address is not a setting shown


def test_summary_and_verbosity_only_when_set(repo, tmp_path, monkeypatch, capsys, fake_model):
    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch,
               'verbosity = "high"\nreasoning_summary = "none"\n')
    _exec(handler, "--workspace", str(repo))
    err = capsys.readouterr().err
    assert "  summary   none\n" in err and "  verbosity high\n" in err


def test_a_resumed_run_says_them_too(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import tool_turn

    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch)
    _exec(handler, "--json", "--workspace", str(repo))
    session_id = json.loads(capsys.readouterr().out.splitlines()[0])["session_id"]
    handler.turns, handler.index = [tool_turn("finish", {"status": "completed",
                                                         "summary": "A is 1",
                                                         "claims": []}, "f1")], 0
    app.main(["exec", "--resume", session_id, "and B?", "--workspace", str(repo)])
    err = capsys.readouterr().err
    assert f"resumed session {session_id}\n  workspace" in err


# --- the renderer --------------------------------------------------------------------------


def _shown(event) -> str:
    out = io.StringIO()
    Renderer(console=Console(file=out, width=200)).handle(event)
    return out.getvalue()


def test_an_event_that_does_not_say_shows_only_the_session():
    assert run_settings(SessionStarted(session_id="s", workspace="w", model="m")) == []
    assert _shown(SessionStarted(session_id="s", workspace="w", model="m")) == "session s\n"


@pytest.mark.parametrize("effort, shown", [(None, "the API's default"), ("xhigh", "xhigh")])
def test_the_effort_is_always_said(effort, shown):
    rows = dict(run_settings(SessionStarted(session_id="s", workspace="w", model="m",
                                            protocol="responses", profile="p", mode="plan",
                                            effort=effort)))
    assert rows["effort"] == shown and "summary" not in rows and "verbosity" not in rows


def test_a_path_is_shown_as_text_never_as_markup_or_escapes():
    shown = _shown(SessionStarted(session_id="s", workspace="C:\\[bold]x\x1b[31my",
                                  model="m[/dim]", protocol="responses", profile="p",
                                  mode="plan"))
    assert "  workspace C:\\[bold]xy\n" in shown
    assert "  model     m[/dim] (responses, profile p)\n" in shown
    assert "\x1b" not in shown


def test_a_long_path_is_not_cut_in_two():
    """A path wrapped at the console's width cannot be copied out of a log."""
    long = "C:\\" + "\\".join(["a-rather-long-folder-name"] * 6)
    out = io.StringIO()
    Renderer(console=Console(file=out, width=40)).handle(SessionStarted(
        session_id="s", workspace=long, model="m", protocol="responses", profile="p",
        mode="plan"))
    assert f"  workspace {long}\n" in out.getvalue()


def test_the_mode_is_the_runs_not_the_files(repo, tmp_path, monkeypatch, capsys, fake_model):
    """exec runs in dont_ask whatever the files say; saying the file's mode
    would tell a CI log approvals were possible."""
    httpd, handler = fake_model
    _configure(tmp_path / "home", httpd, monkeypatch, '[runtime]\nmode = "accept_edits"\n')
    _exec(handler, "--json", "--workspace", str(repo))
    assert json.loads(capsys.readouterr().out.splitlines()[0])["mode"] == "dont_ask"
