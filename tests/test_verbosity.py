"""How long the answers are, as codex's ``model_verbosity`` (D-090):
``[backend] verbosity = "low" | "medium" | "high"``, sent as ``text.verbosity``
on the Responses API and ``verbosity`` on Chat Completions.

What it may not do: send anything when unset (a server that does not know the
field may refuse it), take a level GPT-5 does not, or be quietly dropped for
Claude, which has no such setting -- a setting that does nothing reads as one
that works, so it is refused there by name.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli.wiring import build
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.anthropic import build_body as anthropic_body
from foundry.core.backends.base import collect_turn
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.openai_compat import build_body as chat_body
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.backends.responses import build_body as responses_body
from foundry.core.config import VERBOSITY_LEVELS, load_config, parse_settings
from foundry.core.errors import ConfigError

from test_anthropic_wiring import _build, _home
from test_backend_responses import _Handler, base_url, request_with_history, server  # noqa: F401
from test_cli_e2e import repo  # noqa: F401  (fixture)
from test_mode_model import _session
from test_resume import _wire, fake_model  # noqa: F401  (fixtures)

REQUEST = request_with_history()


# --- what each protocol sends ------------------------------------------------------------


@pytest.mark.parametrize("level", VERBOSITY_LEVELS)
def test_responses_sends_it_as_text_verbosity(level):
    assert responses_body(REQUEST, stream=True, verbosity=level)["text"] == {"verbosity": level}


@pytest.mark.parametrize("level", VERBOSITY_LEVELS)
def test_chat_completions_sends_it_as_verbosity(level):
    assert chat_body(REQUEST, stream=False, verbosity=level)["verbosity"] == level


def test_unset_sends_what_it_did():
    assert responses_body(REQUEST, stream=True, verbosity=None) == responses_body(
        REQUEST, stream=True)
    assert "text" not in responses_body(REQUEST, stream=True)
    assert chat_body(REQUEST, stream=True, verbosity=None) == chat_body(REQUEST, stream=True)
    assert "verbosity" not in chat_body(REQUEST, stream=True)


def test_it_goes_beside_the_reasoning_settings():
    body = responses_body(REQUEST, stream=False, verbosity="low", effort="high",
                          reasoning_summary="auto")
    assert body["text"] == {"verbosity": "low"}
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}
    chat = chat_body(REQUEST, stream=False, verbosity="high", effort="minimal")
    assert (chat["verbosity"], chat["reasoning_effort"]) == ("high", "minimal")


def test_claude_has_no_such_field():
    assert "verbosity" not in AnthropicBackend.__dataclass_fields__
    assert "text" not in anthropic_body(REQUEST, stream=True)


def test_the_responses_backend_sends_its_verbosity(server):
    _Handler.script = {"json": {"model": "gpt-5", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}}
    backend = ResponsesBackend(base_url=base_url(server), model="gpt-5", stream=False,
                               verbosity="low")
    collect_turn(iter(backend.stream_turn(REQUEST)))
    assert server.last_body["text"] == {"verbosity": "low"}


# --- the configuration -------------------------------------------------------------------


@pytest.mark.parametrize("protocol", ["responses", "openai_compat"])
@pytest.mark.parametrize("level", VERBOSITY_LEVELS)
def test_a_level_loads_for_the_openai_shapes(tmp_path, protocol, level):
    home = _home(tmp_path, f'[backend]\nprotocol = "{protocol}"\nverbosity = "{level}"\n')
    assert load_config(home=home).backend.verbosity == level


def test_unset_is_none(tmp_path):
    assert load_config(home=_home(tmp_path, '[backend]\nmodel = "gpt-5"\n')).backend.verbosity \
        is None


@pytest.mark.parametrize("value", ["terse", "LOW", "", 1, True])
def test_a_level_gpt5_does_not_take_is_refused(tmp_path, value):
    toml = f"[backend]\nverbosity = {json.dumps(value)}\n"
    with pytest.raises(ConfigError, match="backend.verbosity must be one of low, medium, high"):
        load_config(home=_home(tmp_path, toml))


def test_claude_refuses_it_by_name(tmp_path):
    home = _home(tmp_path, '[backend]\nprotocol = "anthropic_messages"\nverbosity = "low"\n')
    with pytest.raises(ConfigError) as caught:
        load_config(home=home)
    message = str(caught.value)
    assert "backend.verbosity is not a setting the anthropic_messages protocol takes" in message
    assert "profile" in message


def test_a_profile_of_an_openai_endpoint_can_carry_it(tmp_path):
    home = _home(tmp_path, '[backend]\nprotocol = "anthropic_messages"\n\n'
                           '[profiles.gpt.backend]\nprotocol = "responses"\n'
                           'verbosity = "high"\n')
    assert load_config(home=home).backend.verbosity is None
    config = load_config(home=home, profile="gpt")
    assert (config.backend.protocol, config.backend.verbosity) == ("responses", "high")


def test_the_command_line_sets_it_and_is_checked_too(tmp_path):
    home = _home(tmp_path, '[backend]\nprotocol = "responses"\n')
    assert parse_settings(["verbosity=low"]) == {"backend": {"verbosity": "low"}}
    assert load_config(home=home, settings=parse_settings(["verbosity=low"])
                       ).backend.verbosity == "low"
    with pytest.raises(ConfigError, match="backend.verbosity must be one of"):
        load_config(home=home, settings=parse_settings(["backend.verbosity=short"]))
    with pytest.raises(ConfigError, match="not a setting the anthropic_messages protocol"):
        load_config(home=home, settings=parse_settings(
            ["protocol=anthropic_messages", "verbosity=low"]))
    with pytest.raises(ConfigError, match="backend.verbosity must be one of"):
        load_config(home=home, overrides={"verbosity": "short"})


def test_a_repository_cannot_set_it(tmp_path):
    home = _home(tmp_path, '[backend]\nprotocol = "responses"\n')
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text('[backend]\nverbosity = "low"\n',
                                                         encoding="utf-8")
    with pytest.raises(ConfigError, match="may not set"):
        load_config(workspace, home=home)


# --- wiring ------------------------------------------------------------------------------


@pytest.mark.parametrize("protocol, backend_class", [
    ("responses", ResponsesBackend), ("openai_compat", OpenAICompatBackend),
])
def test_build_gives_the_openai_backends_their_verbosity(tmp_path, monkeypatch, protocol,
                                                         backend_class):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-verbosity")
    backend = _build(tmp_path, f'[backend]\nprotocol = "{protocol}"\nverbosity = "medium"\n'
                     ).runtime.backend
    assert isinstance(backend, backend_class) and backend.verbosity == "medium"


def test_build_without_it_gives_none(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-verbosity")
    assert _build(tmp_path, '[backend]\nprotocol = "openai_compat"\n').runtime.backend \
        .verbosity is None


def test_status_shows_it_only_when_set(repo, tmp_path):
    rows = lambda wiring: [line for line in _session(wiring, "/status").printed  # noqa: E731
                           if line.startswith("[dim]verbosity")]
    assert rows(_wire(repo, tmp_path / "a", [])) == []
    assert rows(_wire(repo, tmp_path / "b", [], overrides={"verbosity": "low"})) == [
        "[dim]verbosity[/dim] low"]


# --- a session ---------------------------------------------------------------------------


class _Capture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.bodies.append(json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0)))))
        reply = ({"model": "gpt-5", "output": [{"type": "message", "content": [
                     {"type": "output_text", "text": "short"}]}]}
                 if self.path.endswith("/responses") else
                 {"model": "gpt-5", "choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant",
                                                             "content": "short"}}]})
        payload = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def capture():
    httpd = HTTPServer(("127.0.0.1", 0), _Capture)
    httpd.bodies = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.parametrize("protocol, field, sent", [
    ("responses", "text", {"verbosity": "low"}),
    ("openai_compat", "verbosity", "low"),
])
def test_every_request_of_a_session_carries_it(repo, tmp_path, capture, protocol, field, sent):
    host, port = capture.server_address
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nprotocol = "{protocol}"\nverbosity = "low"\nstream = false\n'
        f'base_url = "http://{host}:{port}/v1"\nmodel = "gpt-5"\n', encoding="utf-8")
    wiring = build(repo, home=home, console=Console(file=io.StringIO(), width=100))
    try:
        wiring.runtime.run_turn("summarise the repo")
        wiring.runtime.run_turn("and again")
    finally:
        wiring.session.close()
    assert len(capture.bodies) == 2
    assert [body[field] for body in capture.bodies] == [sent, sent]
