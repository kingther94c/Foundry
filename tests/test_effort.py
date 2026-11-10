"""How hard the model reasons, for every protocol, chosen again mid-session
with /effort (D-061).

backend.effort used to reach Claude alone. The OpenAI shapes take one too --
reasoning.effort on the Responses API, reasoning_effort on Chat Completions --
each with its own levels, which load_config checks. Unset sends nothing, so a
setup that never named an effort sends the same body it always did.
"""

from __future__ import annotations

import builtins

import pytest

from foundry.cli import app
from foundry.cli.session_settings import API_DEFAULT_EFFORT
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.anthropic import build_body as anthropic_body
from foundry.core.backends.base import collect_turn
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.openai_compat import build_body as chat_body
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.backends.responses import build_body as responses_body
from foundry.core.config import EFFORT_LEVELS, OPENAI_EFFORT_LEVELS, effort_levels, load_config
from foundry.core.errors import ConfigError
from foundry.core.events import Notice
from foundry.core.session import _FSYNC_TYPES, EventType, SessionStore

from test_anthropic_wiring import _build, _home
from test_backend_responses import _Handler, base_url, request_with_history, server  # noqa: F401
from test_mode_model import _records, _session
from test_resume import _wire, fake_model, repo, turn  # noqa: F401  (fixtures)

REQUEST = request_with_history()


# --- what each protocol sends ------------------------------------------------------------


def test_responses_sends_the_effort_in_reasoning():
    assert responses_body(REQUEST, stream=True, effort="high")["reasoning"] == {
        "effort": "high"}


def test_responses_sends_the_effort_beside_the_summary():
    body = responses_body(REQUEST, stream=False, effort="low", reasoning_summary="auto")
    assert body["reasoning"] == {"effort": "low", "summary": "auto"}


def test_responses_without_an_effort_sends_what_it_did():
    assert "reasoning" not in responses_body(REQUEST, stream=True)
    assert responses_body(REQUEST, stream=True, reasoning_summary="auto")["reasoning"] == {
        "summary": "auto"}


def test_chat_completions_sends_reasoning_effort():
    assert chat_body(REQUEST, stream=False, effort="minimal")["reasoning_effort"] == "minimal"
    assert chat_body(REQUEST, stream=True, effort="none")["reasoning_effort"] == "none"


def test_chat_completions_without_an_effort_sends_what_it_did():
    assert chat_body(REQUEST, stream=True) == chat_body(REQUEST, stream=True, effort=None)
    assert "reasoning_effort" not in chat_body(REQUEST, stream=True)


def test_claude_still_takes_it_as_output_config():
    assert anthropic_body(REQUEST, stream=True, effort="max")["output_config"] == {
        "effort": "max"}


def test_the_responses_backend_sends_its_effort(server):
    _Handler.script = {"json": {
        "model": "gpt-5", "usage": {"input_tokens": 1, "output_tokens": 1},
        "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok"}]}]}}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5", stream=False,
                               effort="xhigh")
    collect_turn(iter(backend.stream_turn(REQUEST)))
    assert server.last_body["reasoning"] == {"effort": "xhigh"}
    backend.effort = None
    collect_turn(iter(backend.stream_turn(REQUEST)))
    assert "reasoning" not in server.last_body


# --- which levels each protocol takes ----------------------------------------------------


def test_each_protocol_has_its_own_levels():
    assert effort_levels("anthropic_messages") == EFFORT_LEVELS
    assert effort_levels("responses") == OPENAI_EFFORT_LEVELS
    assert effort_levels("openai_compat") == OPENAI_EFFORT_LEVELS
    assert "max" in EFFORT_LEVELS and "max" not in OPENAI_EFFORT_LEVELS
    assert "minimal" in OPENAI_EFFORT_LEVELS and "minimal" not in EFFORT_LEVELS


@pytest.mark.parametrize("protocol, effort", [
    ("responses", "none"), ("responses", "minimal"), ("responses", "xhigh"),
    ("openai_compat", "low"), ("openai_compat", "none"),
    ("anthropic_messages", "max"), ("anthropic_messages", "xhigh"),
])
def test_a_level_the_protocol_takes_loads(tmp_path, protocol, effort):
    home = _home(tmp_path, f'[backend]\nprotocol = "{protocol}"\neffort = "{effort}"\n')
    assert load_config(home=home).backend.effort == effort


@pytest.mark.parametrize("protocol, effort", [
    ("responses", "max"), ("openai_compat", "max"),
    ("anthropic_messages", "minimal"), ("anthropic_messages", "none"),
])
def test_a_level_of_another_protocol_is_refused_by_name(tmp_path, protocol, effort):
    home = _home(tmp_path, f'[backend]\nprotocol = "{protocol}"\neffort = "{effort}"\n')
    with pytest.raises(ConfigError) as caught:
        load_config(home=home)
    message = str(caught.value)
    assert f"backend.effort {effort!r} is not one the {protocol} protocol takes" in message
    assert ", ".join(effort_levels(protocol)) in message


def test_a_level_no_protocol_knows_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="backend.effort must be one of"):
        load_config(home=_home(tmp_path, '[backend]\neffort = "extreme"\n'))


def test_a_command_line_effort_is_checked_against_the_protocol_too(tmp_path):
    home = _home(tmp_path, '[backend]\nprotocol = "responses"\n')
    with pytest.raises(ConfigError, match="not one the responses protocol takes"):
        load_config(home=home, overrides={"effort": "max"})
    assert load_config(home=home, overrides={"effort": "high"}).backend.effort == "high"


@pytest.mark.parametrize("protocol, backend_class", [
    ("responses", ResponsesBackend), ("openai_compat", OpenAICompatBackend),
    ("anthropic_messages", AnthropicBackend),
])
def test_build_gives_every_backend_its_effort(tmp_path, monkeypatch, protocol, backend_class):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-effort")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-effort")
    backend = _build(tmp_path, f'[backend]\nprotocol = "{protocol}"\neffort = "low"\n'
                     ).runtime.backend
    assert isinstance(backend, backend_class) and backend.effort == "low"


def test_build_without_an_effort_gives_none(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-effort")
    assert _build(tmp_path, '[backend]\nprotocol = "responses"\n').runtime.backend.effort is None


# --- /effort -----------------------------------------------------------------------------


def test_effort_alone_shows_it_and_the_levels(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = "\n".join(_session(wiring, "/effort").printed)
    assert f"reasoning effort: [bold]{API_DEFAULT_EFFORT}[/bold]" in shown
    assert ("usage: /effort LEVEL -- none, minimal, low, medium, high, xhigh, or default"
            in shown)
    assert not _records(wiring, EventType.EFFORT_CHANGE)


def test_effort_changes_what_the_next_request_sends(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="one")])
    events: list = []
    wiring.runtime.events.subscribe(events.append)
    _session(wiring, "/effort HIGH", "hello")
    assert wiring.runtime.backend.effort == "high"
    assert wiring.config.backend.effort == "high"
    assert wiring.chosen == {"effort": "high"}
    assert _records(wiring, EventType.EFFORT_CHANGE) == [
        {"from": None, "to": "high", "by": "repl"}]
    assert any(isinstance(e, Notice) and e.text == (
        f"reasoning effort: high now (it was {API_DEFAULT_EFFORT})") for e in events)
    assert wiring.note == ""                       # nothing for the model to be told


def test_effort_default_sends_none_again(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [], overrides={"effort": "low"})
    events: list = []
    wiring.runtime.events.subscribe(events.append)
    _session(wiring, "/effort default")
    assert wiring.runtime.backend.effort is None and wiring.config.backend.effort is None
    assert wiring.chosen == {"effort": None}
    assert _records(wiring, EventType.EFFORT_CHANGE) == [
        {"from": "low", "to": None, "by": "repl"}]
    assert any(isinstance(e, Notice) and e.text == (
        f"reasoning effort: {API_DEFAULT_EFFORT} now (it was low)") for e in events)


@pytest.mark.parametrize("level", ["max", "extreme", "high please", "-1"])
def test_a_level_the_protocol_does_not_take_changes_nothing(repo, tmp_path, level):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = "\n".join(_session(wiring, f"/effort {level}").printed)
    assert ("openai_compat takes none, minimal, low, medium, high, xhigh, or default"
            in shown)
    assert getattr(wiring.runtime.backend, "effort", None) is None
    assert wiring.config.backend.effort is None
    assert wiring.chosen == {} and not _records(wiring, EventType.EFFORT_CHANGE)


@pytest.mark.parametrize("start, typed, said", [
    (None, "default", f"already {API_DEFAULT_EFFORT}"),
    ("medium", "medium", "already medium"),
])
def test_the_same_level_changes_nothing(repo, tmp_path, start, typed, said):
    wiring = _wire(repo, tmp_path / "home", [], overrides={"effort": start})
    shown = "\n".join(_session(wiring, f"/effort {typed}").printed)
    assert said in shown
    assert wiring.chosen == {} and not _records(wiring, EventType.EFFORT_CHANGE)


def test_claude_takes_its_own_levels(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-effort")
    wiring = _wire(repo, tmp_path / "home", [], overrides={"protocol": "anthropic_messages"})
    shown = "\n".join(_session(wiring, "/effort minimal", "/effort max").printed)
    assert "anthropic_messages takes low, medium, high, xhigh, max, or default" in shown
    assert wiring.runtime.backend.effort == "max"
    assert [r["to"] for r in _records(wiring, EventType.EFFORT_CHANGE)] == ["max"]


def test_status_shows_the_effort(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _session(wiring, "/status", "/effort xhigh", "/status").printed
    rows = [line for line in shown if line.startswith("[dim]effort")]
    assert rows == [f"[dim]effort   [/dim] {API_DEFAULT_EFFORT}", "[dim]effort   [/dim] xhigh"]


def test_help_lists_it(repo, tmp_path):
    shown = "\n".join(_session(_wire(repo, tmp_path / "home", []), "/help").printed)
    assert "/effort [LEVEL]" in shown


def test_the_effort_change_record_is_synced():
    assert EventType.EFFORT_CHANGE in _FSYNC_TYPES


# --- on the wire, and across /new --------------------------------------------------------


def test_effort_reaches_the_wire_and_carries_into_new(repo, tmp_path, monkeypatch, fake_model):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    host, port = httpd.server_address
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    handler.turns, handler.index = [text_turn("ok")], 0
    lines = iter(["hello", "/effort high", "again", "/new", "third", "/effort default",
                  "fourth", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(lines))
    assert app.main(["run", "--workspace", str(repo)]) == 13
    assert [body.get("reasoning_effort") for body in httpd.requests] == [
        None, "high", "high", None]
    journals = sorted((home / "sessions").glob("*/events.jsonl"),
                      key=lambda p: p.stat().st_mtime)
    changes = [[r.payload for r in SessionStore.read_records(j)
                if r.type == EventType.EFFORT_CHANGE] for j in journals]
    assert changes == [[{"from": None, "to": "high", "by": "repl"}],
                       [{"from": "high", "to": None, "by": "repl"}]]
