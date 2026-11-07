"""`exec --trust` (D-137), as codex exec's: the repository trusted by name on
the command line, in this run and later ones, as answering yes at the trust
prompt trusts it -- the one way an unattended run reads a repository's
instructions and settings.
"""

from __future__ import annotations

import json
import os

from foundry.cli import app

from test_cli_e2e import tool_turn
from test_cli_flags import _exec_home
from test_resume import fake_model, repo  # noqa: F401 (fixtures)

MARK = "Always run the frobnicator before the tests."
DONE = tool_turn("finish", {"status": "completed", "summary": "done", "claims": []}, "f1")
NOT_LOADED = "provides Foundry instructions; not loading them"


def _setup(tmp_path, monkeypatch, fake_model, repo):
    httpd, _ = fake_model
    home = _exec_home(tmp_path, httpd, monkeypatch)
    (repo / "AGENTS.md").write_text(f"# Notes\n\n{MARK}\n", encoding="utf-8")
    return home


def _run(capsys, fake_model, where, *argv, command=("exec", "fix it"), code=0):
    httpd, handler = fake_model
    handler.turns, handler.index = [DONE], 0
    httpd.requests.clear()
    assert app.main([*command, *argv, "-C", str(where)]) == code
    said = capsys.readouterr()
    return said, MARK in json.dumps(httpd.requests)


def _trusted(home):
    path = home / "trusted.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["trusted"]


def test_without_it_an_unattended_run_reads_none(repo, tmp_path, monkeypatch, capsys,
                                                 fake_model):
    home = _setup(tmp_path, monkeypatch, fake_model, repo)
    said, read = _run(capsys, fake_model, repo)
    assert not read and NOT_LOADED in said.err
    assert "--trust" in said.err
    assert _trusted(home) == []


def test_trust_reads_them_and_is_kept(repo, tmp_path, monkeypatch, capsys, fake_model):
    home = _setup(tmp_path, monkeypatch, fake_model, repo)
    said, read = _run(capsys, fake_model, repo, "--trust")
    assert read and NOT_LOADED not in said.err
    assert "trusting" in said.err and "from now on" in said.err
    assert _trusted(home) == [os.path.realpath(repo)]
    # A later run without it reads them too, as after a yes at the prompt.
    said, read = _run(capsys, fake_model, repo)
    assert read and "trusting" not in said.err


def test_said_only_the_first_time(repo, tmp_path, monkeypatch, capsys, fake_model):
    home = _setup(tmp_path, monkeypatch, fake_model, repo)
    _run(capsys, fake_model, repo, "--trust")
    said, read = _run(capsys, fake_model, repo, "--trust")
    assert read and "trusting" not in said.err
    assert _trusted(home) == [os.path.realpath(repo)]


def test_the_others_trusted_are_kept(repo, tmp_path, monkeypatch, capsys, fake_model):
    home = _setup(tmp_path, monkeypatch, fake_model, repo)
    other = str(tmp_path / "other")
    (home / "trusted.json").write_text(json.dumps({"trusted": [other]}), encoding="utf-8")
    _run(capsys, fake_model, repo, "--trust")
    assert sorted(_trusted(home)) == sorted([other, os.path.realpath(repo)])


def test_kept_even_when_the_run_is_not(repo, tmp_path, monkeypatch, capsys, fake_model):
    home = _setup(tmp_path, monkeypatch, fake_model, repo)
    _, read = _run(capsys, fake_model, repo, "--trust", "--ephemeral")
    assert read and _trusted(home) == [os.path.realpath(repo)]


def test_a_review_takes_it_too(repo, tmp_path, monkeypatch, capsys, fake_model):
    home = _setup(tmp_path, monkeypatch, fake_model, repo)
    _, read = _run(capsys, fake_model, repo, "--trust", command=("review", "--uncommitted"))
    assert read and _trusted(home) == [os.path.realpath(repo)]


def test_a_folder_outside_git_is_not_trusted(tmp_path, monkeypatch, capsys, fake_model):
    httpd, _ = fake_model
    home = _exec_home(tmp_path, httpd, monkeypatch)
    loose = tmp_path / "loose"
    loose.mkdir()
    said, _ = _run(capsys, fake_model, loose, "--trust", code=12)
    assert "not inside a Git repository" in said.err
    assert _trusted(home) == []
