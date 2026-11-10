"""/model lists the models the endpoint offers (D-067), as codex's /model
offers a list to choose from.

What carries the feature: the list is asked for with the session's own
endpoint and credential and nothing else; what comes back is shown -- never
sent to the model or journaled -- and an error says what happened without
the server's answer, which could hold anything.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time

import pytest

from foundry.core.backends.anthropic import ANTHROPIC_VERSION, AnthropicBackend
from foundry.core.backends.base import model_ids
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.recording import RecordingBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.errors import AuthError, FatalError, ProtocolError, TransientError
from foundry.core.httpc import HttpClient
from foundry.core.session import SessionStore
from foundry.cli.session_settings import MODELS_SHOWN

from test_resume import _Live, _wire, repo  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _shown

KEY = "sk-test-not-a-real-key-4711"
LISTED = {"object": "list", "data": [{"id": "gpt-5"}, {"id": "claude-opus-5-5"},
                                     {"id": "gpt-5"}, {"id": "fake"}]}


class _Models(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        status, body, delay = self.server.answer
        if delay:
            time.sleep(delay)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def endpoint():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Models)
    httpd.requests = []
    httpd.answer = (200, json.dumps(LISTED).encode(), 0)
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _answer(endpoint, status, body, delay=0.0):
    endpoint.answer = (status, body if isinstance(body, bytes) else json.dumps(body).encode(),
                       delay)


# --- asking the endpoint ----------------------------------------------------------------


@pytest.mark.parametrize("backend_class", [OpenAICompatBackend, ResponsesBackend])
def test_an_openai_endpoint_is_asked_with_its_own_credential(endpoint, backend_class):
    backend = backend_class(base_url=endpoint.url + "/", model="fake", api_key=KEY,
                            extra_headers={"X-Team": "core"})
    assert backend.list_models() == ["claude-opus-5-5", "fake", "gpt-5"]
    [(path, headers)] = endpoint.requests
    assert path == "/v1/models"
    assert headers["authorization"] == f"Bearer {KEY}" and headers["x-team"] == "core"


def test_anthropic_is_asked_its_way(endpoint):
    backend = AnthropicBackend(base_url=endpoint.url, model="fake", api_key=KEY)
    assert backend.list_models() == ["claude-opus-5-5", "fake", "gpt-5"]
    [(path, headers)] = endpoint.requests
    assert path == "/v1/models?limit=1000"
    assert headers["x-api-key"] == KEY and headers["anthropic-version"] == ANTHROPIC_VERSION
    assert "authorization" not in headers and "anthropic-beta" not in headers


@pytest.mark.parametrize("payload, ids", [
    ({"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]}, ["a", "b"]),
    ({"data": [{"id": "a"}, {"name": "x"}, {"id": 7}, {"id": ""}, "c", None]}, ["a"]),
    ({"data": []}, []),
])
def test_what_is_read_from_the_answer(payload, ids):
    assert model_ids(payload) == ids


def test_the_list_is_in_order_whatever_order_it_came_in():
    names = [f"model-{chr(97 + i % 26)}{i}" for i in range(60)]
    assert model_ids({"data": [{"id": n} for n in reversed(names)]}) == sorted(names)


@pytest.mark.parametrize("payload", [{}, {"data": {"id": "a"}}, [{"id": "a"}], None])
def test_an_answer_with_no_list_says_so(payload):
    with pytest.raises(ProtocolError, match="holds no list of models"):
        model_ids(payload)


def test_an_answer_that_is_not_json_says_so(endpoint):
    _answer(endpoint, 200, b"<html>sign in</html>")
    with pytest.raises(ProtocolError, match="not valid JSON"):
        ResponsesBackend(base_url=endpoint.url, model="fake").list_models()


@pytest.mark.parametrize("status, error, said", [
    (404, FatalError, "request rejected (HTTP 404)"),
    (401, AuthError, "authentication rejected (HTTP 401)"),
    (503, TransientError, "server error (HTTP 503)"),
])
def test_a_refusal_says_its_status_not_its_body(endpoint, status, error, said):
    _answer(endpoint, status, {"error": f"bad key {KEY}"})
    with pytest.raises(error) as caught:
        OpenAICompatBackend(base_url=endpoint.url, model="fake", api_key=KEY).list_models()
    assert str(caught.value) == said and KEY not in str(caught.value)
    assert len(endpoint.requests) == 1          # never retried


def test_a_long_answer_is_cut_off(endpoint):
    _answer(endpoint, 200, {"data": [{"id": "x" * 100}] * 50})
    with pytest.raises(ProtocolError, match="larger than 1000 bytes"):
        HttpClient().get_json(endpoint.url + "/models", {}, timeout=5, max_bytes=1000)
    assert HttpClient().get_json(endpoint.url + "/models", {}, timeout=5,
                                 max_bytes=1 << 20).json()["data"][0]["id"] == "x" * 100


def test_a_slow_endpoint_is_given_up_on(endpoint):
    _answer(endpoint, 200, LISTED, delay=3)
    started = time.monotonic()
    with pytest.raises(TransientError, match="timed out"):
        HttpClient().get_json(endpoint.url + "/models", {}, timeout=0.5, max_bytes=1000)
    assert time.monotonic() - started < 2.5


def test_connecting_waits_no_longer_than_the_listing_may(endpoint, monkeypatch):
    seen = []
    real = socket.create_connection

    def spy(address, timeout, *args, **kwargs):
        seen.append(timeout)
        return real(address, timeout, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", spy)
    HttpClient().get_json(endpoint.url + "/models", {}, timeout=4.5, max_bytes=1 << 20)
    assert seen == [4.5]


def test_a_recorded_session_asks_the_backend_it_records(endpoint, tmp_path):
    inner = ResponsesBackend(base_url=endpoint.url, model="fake")
    assert RecordingBackend(inner=inner, fixture_path=tmp_path / "f.json").list_models() == [
        "claude-opus-5-5", "fake", "gpt-5"]
    with pytest.raises(FatalError, match="lists no models"):
        RecordingBackend(inner=_Live([]), fixture_path=tmp_path / "f.json").list_models()


# --- /model -------------------------------------------------------------------------------


class _Listing(_Live):
    def __init__(self, answer):
        super().__init__([])
        self.answer = answer
        self.asked = 0

    def list_models(self):
        self.asked += 1
        if isinstance(self.answer, BaseException):
            raise self.answer
        return list(self.answer)


def _listing(repo, tmp_path, answer):
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = _Listing(answer)
    return wiring


def test_model_shows_the_endpoint_s_models_and_marks_the_session_s(repo, tmp_path):
    wiring = _listing(repo, tmp_path, ["claude-opus-5-5", "fake", "gpt-5"])
    shown = _shown(_repl(wiring, "/model"))
    assert "this session uses [bold]fake[/bold]" in shown
    assert "the endpoint lists 3: claude-opus-5-5, [bold]fake[/bold], gpt-5" in shown
    assert "usage: /model NAME" in shown
    assert wiring.runtime.backend.seen == []                 # nothing sent to the model
    journal = wiring.session.path.read_text(encoding="utf-8")
    assert "claude-opus-5-5" not in journal and "gpt-5" not in journal


def test_it_is_asked_each_time_and_only_when_no_name_is_given(repo, tmp_path):
    wiring = _listing(repo, tmp_path, ["fake", "gpt-5"])
    _repl(wiring, "/model", "/model gpt-5", "/model")
    assert wiring.runtime.backend.asked == 2
    assert wiring.runtime.model == "gpt-5"


def test_a_name_foundry_would_not_take_is_left_out(repo, tmp_path):
    wiring = _listing(repo, tmp_path, ["gpt-5", "[red]boom[/red]", "x\x1b]52;c;aGk=\x07",
                                       "two words", "-flag"])
    shown = _shown(_repl(wiring, "/model"))
    assert "the endpoint lists 1: gpt-5" in shown
    assert "4 with a name that is not a model name left out" in shown
    assert "boom" not in shown and "\x1b" not in shown and "two words" not in shown


def test_a_long_list_is_cut(repo, tmp_path):
    names = [f"m-{i:03}" for i in range(MODELS_SHOWN + 25)]
    shown = _shown(_repl(_listing(repo, tmp_path, names), "/model"))
    assert f"the endpoint lists {MODELS_SHOWN + 25}: m-000, " in shown
    assert f"m-{MODELS_SHOWN - 1:03}, and 25 more" in shown
    assert f"m-{MODELS_SHOWN:03}" not in shown


def test_an_empty_list_says_so(repo, tmp_path):
    assert "the endpoint lists no models" in _shown(_repl(_listing(repo, tmp_path, []),
                                                           "/model"))


def test_a_backend_that_lists_nothing_says_so(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, "/model"))
    assert "this backend lists no models" in shown and "usage: /model NAME" in shown


def test_a_failure_is_said_cleanly_and_the_session_goes_on(repo, tmp_path):
    wiring = _listing(repo, tmp_path, FatalError("request rejected (HTTP 404)\x1b]0;x\x07 [b]"))
    shown = _shown(_repl(wiring, "/model", "/status"))
    assert "the endpoint did not list its models: request rejected (HTTP 404)" in shown
    assert "\x1b" not in shown and r"\[b]" in shown
    assert "session" in shown.split("did not list its models")[1]      # /status ran


def test_ctrl_c_stops_asking_not_the_session(repo, tmp_path):
    wiring = _listing(repo, tmp_path, KeyboardInterrupt())
    shown = _shown(_repl(wiring, "/model", "/model gpt-5"))
    assert "stopped asking the endpoint for its models" in shown
    assert wiring.runtime.model == "gpt-5"


def test_the_key_never_reaches_the_screen(repo, tmp_path, endpoint):
    _answer(endpoint, 401, {"error": {"message": f"invalid key {KEY}"}})
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = ResponsesBackend(base_url=endpoint.url, model="fake", api_key=KEY)
    shown = _shown(_repl(wiring, "/model"))
    assert "the endpoint did not list its models: authentication rejected (HTTP 401)" in shown
    assert KEY not in shown
    assert KEY not in "".join(r.payload.__repr__() for r in
                              SessionStore.read_records(wiring.session.path))
