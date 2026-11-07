"""codex's review_model (D-143): the model a review runs on -- /review's
task, and foundry review's whole run -- while the session goes on with its
own. -m still picks foundry review's.
"""

from __future__ import annotations

import pytest

from foundry.core.config import load_config
from foundry.core.errors import ConfigError
from foundry.core.session import EventType, SessionStore

from test_resume import _wire, fake_model, repo, turn  # noqa: F401  (fixtures)
from test_review_command import _model, _review
from test_slash_commands import _repl, _shown

REVIEWER = "fake-review"


def _home(tmp_path, backend: str = f'review_model = "{REVIEWER}"\n'):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[backend]\n" + backend, encoding="utf-8")
    return home


def _changes(wiring) -> list:
    return [r.payload for r in SessionStore.read_records(wiring.session.path)
            if r.type == EventType.MODEL_CHANGE]


@pytest.fixture
def changed(repo):
    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")
    return repo


# --- /review ----------------------------------------------------------------------


def test_slash_review_runs_on_it_and_the_session_goes_on_with_its_own(changed, tmp_path):
    wiring = _wire(changed, _home(tmp_path),
                   [turn(text="b.py: B is 5, and nothing tests it."), turn(text="ok")])
    _repl(wiring, "/review", "thanks")
    assert [request.model for request in wiring.runtime.backend.seen] == [REVIEWER, "fake"]
    # The journal says so, so a resume goes on with the session's.
    assert _changes(wiring) == [{"from": "fake", "to": REVIEWER, "by": "review"},
                                {"from": REVIEWER, "to": "fake", "by": "review"}]
    assert f"reviewing on {REVIEWER} (review_model)" in wiring.renderer.console.file.getvalue()
    assert wiring.config.backend.model == "fake"


def test_a_stopped_review_gives_the_session_its_model_back(changed, tmp_path):
    def interrupt():
        raise KeyboardInterrupt

    wiring = _wire(changed, _home(tmp_path), [interrupt])
    _repl(wiring, "/review")
    assert wiring.runtime.model == "fake"
    assert _changes(wiring)[-1] == {"from": REVIEWER, "to": "fake", "by": "review"}


def test_after_a_model_switch_the_session_goes_back_to_the_one_it_chose(changed, tmp_path):
    wiring = _wire(changed, _home(tmp_path), [turn(text="reviewed"), turn(text="ok")])
    _repl(wiring, "/model other", "/review", "thanks")
    assert [request.model for request in wiring.runtime.backend.seen] == [REVIEWER, "other"]


@pytest.mark.parametrize("backend", ["", 'review_model = "fake"\n'])
def test_without_one_of_its_own_a_review_runs_on_the_session_s(changed, tmp_path, backend):
    wiring = _wire(changed, _home(tmp_path, backend), [turn(text="reviewed"), turn(text="ok")])
    _repl(wiring, "/review", "thanks")
    assert [request.model for request in wiring.runtime.backend.seen] == ["fake", "fake"]
    assert _changes(wiring) == []


def test_other_tasks_run_on_the_session_s(changed, tmp_path):
    wiring = _wire(changed, _home(tmp_path), [turn(text="hi")])
    _repl(wiring, "hello")
    assert wiring.runtime.backend.seen[0].model == "fake"


def test_status_says_it(changed, tmp_path):
    wiring = _wire(changed, _home(tmp_path), [])
    assert f"[dim]reviews  [/dim] on {REVIEWER} (review_model)" in _shown(
        _repl(wiring, "/status"))


# --- foundry review ---------------------------------------------------------------


def _reviewer_home(tmp_path):
    config = tmp_path / "home" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + f'review_model = "{REVIEWER}"\n',
                      encoding="utf-8")


def test_foundry_review_runs_on_it(changed, tmp_path, monkeypatch, capsys, fake_model):
    httpd = _model(tmp_path, monkeypatch, fake_model)
    _reviewer_home(tmp_path)
    assert _review(changed, "--uncommitted") == 0
    assert {request["model"] for request in httpd.requests} == {REVIEWER}


def test_its_model_flag_comes_first(changed, tmp_path, monkeypatch, capsys, fake_model):
    httpd = _model(tmp_path, monkeypatch, fake_model)
    _reviewer_home(tmp_path)
    assert _review(changed, "--uncommitted", "-m", "picked") == 0
    assert {request["model"] for request in httpd.requests} == {"picked"}


def test_exec_runs_on_the_session_s(changed, tmp_path, monkeypatch, capsys, fake_model):
    from foundry.cli import app

    httpd = _model(tmp_path, monkeypatch, fake_model)
    _reviewer_home(tmp_path)
    assert app.main(["exec", "say hi", "--workspace", str(changed)]) == 0
    assert {request["model"] for request in httpd.requests} == {"fake"}


# --- the setting --------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", '""', '"  "', "true"])
def test_only_a_model_s_name(tmp_path, value):
    home = _home(tmp_path, f"review_model = {value}\n")
    with pytest.raises(ConfigError, match="review_model must be a model's name"):
        load_config(home=home)


def test_where_codex_keeps_it_is_told_where_foundry_does(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(f'review_model = "{REVIEWER}"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"put it under \[backend\]"):
        load_config(home=home)
