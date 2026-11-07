"""`foundry policy check COMMAND` (D-135), as `codex execpolicy check`: what
the policy would decide for a command, here, with this configuration -- at
a terminal and unattended -- without running it or starting a session.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app

from test_resume import repo  # noqa: F401 (fixture)

RULES = (
    '[[permissions]]\ntool = "run_command"\npattern = "python -m pytest*"\n'
    'decision = "allow"\n'
    '[[permissions]]\ntool = "run_command"\npattern = "npm publish*"\n'
    'decision = "ask"\nreason = "publishing is the release manager\'s"\n'
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(RULES, encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    return home


def _check(capsys, repo, *argv):
    code = app.main(["policy", "check", "-C", str(repo), *argv])
    return code, capsys.readouterr()


def _json(capsys, repo, *argv) -> dict:
    code, out = _check(capsys, repo, "--json", *argv)
    assert code == 0
    return json.loads(out.out)


def test_an_allow_rule(repo, home, capsys):
    said = _json(capsys, repo, "--", "python", "-m", "pytest", "-q")
    assert said["command"] == "python -m pytest -q" and said["mode"] == "default"
    for side in ("at_a_terminal", "unattended"):
        assert said[side]["verdict"] == "allow" and said[side]["step"] == 5
        assert said[side]["rule"] == "user.0"


def test_an_ask_rule_is_refused_unattended(repo, home, capsys):
    said = _json(capsys, repo, "npm publish --tag next")
    assert said["at_a_terminal"] == {"verdict": "ask",
                                     "reason": "publishing is the release manager's",
                                     "rule": "user.1", "step": 3}
    assert said["unattended"]["verdict"] == "deny"
    assert said["unattended"]["rule"] == "user.1"


def test_no_rule_asks_at_a_terminal_and_is_refused_unattended(repo, home, capsys):
    said = _json(capsys, repo, "make", "deploy")
    assert said["at_a_terminal"]["verdict"] == "ask"
    assert said["at_a_terminal"]["rule"] == "interactive"
    assert said["unattended"] == {"verdict": "deny",
                                  "reason": "no matching allow rule and approval is "
                                            "unavailable",
                                  "rule": "fail_closed", "step": 6}


def test_the_breaker_is_said_too(repo, home, capsys):
    said = _json(capsys, repo, "git push origin main")
    assert said["at_a_terminal"]["verdict"] == "deny"
    assert said["at_a_terminal"]["rule"] == "breaker" and said["at_a_terminal"]["step"] == 0


def test_a_chain_needs_every_part_allowed(repo, home, capsys):
    said = _json(capsys, repo, "python -m pytest; make deploy")
    assert said["at_a_terminal"]["verdict"] == "ask"


def test_the_mode_counts(repo, home, capsys):
    said = _json(capsys, repo, "--mode", "dont_ask", "make", "deploy")
    assert said["mode"] == "dont_ask" and said["at_a_terminal"]["verdict"] == "deny"
    said = _json(capsys, repo, "--mode", "plan", "make", "deploy")
    assert said["mode"] == "plan" and said["at_a_terminal"]["verdict"] == "deny"


def test_shown_for_a_person(repo, home, capsys):
    code, out = _check(capsys, repo, "npm publish")
    assert code == 0
    lines = out.out.splitlines()
    assert lines[0] == "npm publish"
    assert lines[1].split() == ["at", "a", "terminal", "(default)", "ask", "--",
                                "publishing", "is", "the", "release", "manager's",
                                "(user.1,", "step", "3)"]
    assert lines[2].split()[:3] == ["unattended", "deny", "--"]


def test_the_workspace_s_rules_once_it_is_trusted(repo, home, capsys):
    (repo / ".foundry").mkdir()
    (repo / ".foundry" / "config.toml").write_text(
        '[[permissions]]\ntool = "run_command"\npattern = "make *"\ndecision = "deny"\n',
        encoding="utf-8")
    code, out = _check(capsys, repo, "--json", "make deploy")
    assert json.loads(out.out)["at_a_terminal"]["verdict"] == "ask"
    assert "not trusted yet: its rules are not applied" in out.err
    app._remember_trust(home, repo.resolve())
    said = _json(capsys, repo, "make deploy")
    assert said["at_a_terminal"]["verdict"] == "deny"
    assert said["at_a_terminal"]["rule"] == "project.0"


def test_nothing_runs_and_no_session_starts(repo, home, capsys, monkeypatch):
    import subprocess

    def refused(*args, **kwargs):
        raise AssertionError("nothing is run")

    monkeypatch.setattr(subprocess, "Popen", refused)
    assert _json(capsys, repo, "python -m pytest")["unattended"]["verdict"] == "allow"
    assert not (home / "sessions").exists()


def test_an_empty_command_is_refused(repo, home, capsys):
    code, out = _check(capsys, repo, "  ")
    assert code == 2 and "policy check needs the command to check" in out.err


def test_a_command_is_needed(repo, home):
    with pytest.raises(SystemExit):
        app.main(["policy", "check"])
    with pytest.raises(SystemExit):
        app.main(["policy"])
