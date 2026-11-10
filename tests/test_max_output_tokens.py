"""The most one reply may be, as codex's ``model_max_output_tokens`` (D-096):
``[backend] max_output_tokens = N`` on every protocol -- ``max_tokens`` for
Claude, ``max_output_tokens`` on the Responses API, ``max_completion_tokens``
on Chat Completions, the name OpenAI's reasoning models take.

Claude's requests asked for up to 64,000 tokens with no way to ask for less,
which a gateway serving a model with a smaller limit refuses. What it may not
do: leave less room for the answer than the answer may take (a full context
plus a full answer would overrun the window), take more than half the
window, or be smaller than a tool call.
"""

from __future__ import annotations

import dataclasses
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli.wiring import build
from foundry.core.backends.anthropic import build_body as anthropic_body
from foundry.core.backends.openai_compat import build_body as chat_body
from foundry.core.backends.responses import build_body as responses_body
from foundry.core.config import MIN_OUTPUT_TOKENS, load_config, parse_settings
from foundry.core.errors import ConfigError

from test_anthropic_wiring import _build, _home
from test_backend_responses import request_with_history
from test_cli_e2e import repo  # noqa: F401  (fixture)
from test_compaction import _long_task, _summary_requests

REQUEST = request_with_history()
CAPPED = dataclasses.replace(REQUEST, max_output_tokens=4_096)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-output")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-output")


# --- what each protocol sends ------------------------------------------------------------


def test_chat_completions_sends_the_name_reasoning_models_take():
    body = chat_body(CAPPED, stream=False)
    assert body["max_completion_tokens"] == 4_096 and "max_tokens" not in body


def test_responses_and_claude_send_their_own():
    assert responses_body(CAPPED, stream=True)["max_output_tokens"] == 4_096
    assert anthropic_body(CAPPED, stream=True)["max_tokens"] == 4_096


def test_unset_each_sends_what_it_did():
    chat = chat_body(REQUEST, stream=False)
    assert "max_completion_tokens" not in chat and "max_tokens" not in chat
    assert "max_output_tokens" not in responses_body(REQUEST, stream=True)
    assert anthropic_body(REQUEST, stream=True)["max_tokens"] == 64_000
    assert anthropic_body(REQUEST, stream=False)["max_tokens"] == 16_000


# --- the setting ---------------------------------------------------------------------------


def test_it_loads_from_the_file_and_the_command_line(tmp_path):
    home = _home(tmp_path, "[backend]\nmax_output_tokens = 8192\n")
    assert load_config(home=home).backend.max_output_tokens == 8_192
    assert load_config(home=tmp_path / "nowhere").backend.max_output_tokens is None
    assert load_config(home=home, settings=parse_settings(["max_output_tokens=4096"])
                       ).backend.max_output_tokens == 4_096
    (tmp_path / "floor").mkdir()
    floor = _home(tmp_path / "floor", "[backend]\nmax_output_tokens = 256\n")
    assert load_config(home=floor).backend.max_output_tokens == MIN_OUTPUT_TOKENS == 256


@pytest.mark.parametrize("value", ["255", "0", '"4k"', "true", "4096.0"])
def test_anything_but_a_number_of_tokens_is_refused(tmp_path, value):
    with pytest.raises(ConfigError, match="backend.max_output_tokens must be a whole number "
                                          "of tokens, at least 256"):
        load_config(home=_home(tmp_path, f"[backend]\nmax_output_tokens = {value}\n"))


def test_codex_s_name_is_told_foundry_s(tmp_path):
    with pytest.raises(ConfigError, match="Foundry calls it backend.max_output_tokens"):
        load_config(home=_home(tmp_path, "model_max_output_tokens = 4096\n"))


# --- a session -----------------------------------------------------------------------------


@pytest.mark.parametrize("protocol", ["responses", "openai_compat", "anthropic_messages"])
def test_build_gives_the_runtime_its_cap(tmp_path, protocol):
    runtime = _build(tmp_path, f'[backend]\nprotocol = "{protocol}"\n'
                               "max_output_tokens = 4096\n").runtime
    assert runtime.max_output_tokens == 4_096


@pytest.mark.parametrize("cap, reserve", [(None, 8_000), (4_096, 8_000), (20_000, 20_000)])
def test_the_room_kept_for_the_answer_is_at_least_the_answer(tmp_path, cap, reserve):
    toml = "[backend]\n" + (f"max_output_tokens = {cap}\n" if cap else "")
    assert _build(tmp_path, toml).runtime.context.reserve_output_tokens == reserve


def test_more_than_half_the_window_is_refused(tmp_path):
    with pytest.raises(ConfigError) as caught:
        _build(tmp_path, "[backend]\nmax_context_tokens = 32000\nmax_output_tokens = 16001\n")
    assert "max_output_tokens (16,001) is more than half the model's 32,000-token window" in str(
        caught.value)


def test_half_the_window_is_taken(tmp_path):
    runtime = _build(tmp_path, "[backend]\nmax_context_tokens = 32000\n"
                               "max_output_tokens = 16000\n").runtime
    assert (runtime.max_output_tokens, runtime.context.reserve_output_tokens) == (16_000, 16_000)


def test_every_request_carries_it_a_compactions_too(tmp_path):
    runtime, session, _, model, _ = _long_task(tmp_path)
    runtime.max_output_tokens = 512
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert _summary_requests(model)
    assert {request.max_output_tokens for request in model.seen} == {512}


class _Capture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.bodies.append(json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0)))))
        reply = ({"model": "gpt-5", "output": [{"type": "message", "content": [
                     {"type": "output_text", "text": "ok"}]}]}
                 if self.path.endswith("/responses") else
                 {"model": "gpt-5", "choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant",
                                                             "content": "ok"}}]})
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


@pytest.mark.parametrize("protocol, field", [
    ("responses", "max_output_tokens"), ("openai_compat", "max_completion_tokens"),
])
def test_it_reaches_the_wire(repo, tmp_path, capture, protocol, field):
    host, port = capture.server_address
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[backend]\nprotocol = "{protocol}"\nmax_output_tokens = 2048\nstream = false\n'
        f'base_url = "http://{host}:{port}/v1"\nmodel = "gpt-5"\n', encoding="utf-8")
    wiring = build(repo, home=home, console=Console(file=io.StringIO(), width=100))
    try:
        wiring.runtime.run_turn("say ok")
    finally:
        wiring.session.close()
    [body] = capture.bodies
    assert body[field] == 2_048
