"""A failure the provider reports inside a successful answer is a failed turn,
not the end of one (D-147).

OpenAI-family providers report a failure part-way through a stream in the
stream itself: the Responses API with an ``error`` event (its code and message
at the top level) or ``response.failed``; OpenRouter with an ``error`` beside a
``finish_reason`` of ``"error"``, and the stream then closes with ``[DONE]``
as usual; vLLM with a chunk that is the error. A failed response answers 200
when it is not streamed, too.

Found by a model played behind a local relay (roadmap, "After parity", P1):
the Chat Completions adapter kept the cut answer -- "Let me loo" -- as the
model's whole one and lost the tool call it was making, and the Responses
adapter ended the run ``failed`` on a passing server error, reporting
``provider reported a failure: {}`` for the API's own error event, whose
message it looked for in the wrong place.

Now each is asked again, as a cut stream is (D-099), unless its code says
waiting cannot help: a used-up quota stops the task as D-085 does, and a
context that is too long or a refused request fail it at once.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.core.backends.base import raise_reported_error
from foundry.core.conversation import Role
from foundry.core.errors import FatalError, QuotaExceeded, StreamDropped
from foundry.core.events import Notice, TerminalStatus
from foundry.core.session import EventType, SessionStore

from test_cli_e2e import repo  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-reported")


def _data(*events, done=False):
    body = b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events)
    return body + (b"data: [DONE]\n\n" if done else b"")


CHAT_FULL = _data({"model": "m", "choices": [{"delta": {"content": "A is 1"}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 3, "completion_tokens": 2}}, done=True)
RESPONSES_FULL = _data({"type": "response.output_text.delta", "delta": "A is 1"},
                       {"type": "response.completed",
                        "response": {"usage": {"input_tokens": 3, "output_tokens": 2}}})
FULL = {"openai_compat": CHAT_FULL, "responses": RESPONSES_FULL}

_BEGUN_CHAT = {"model": "m", "choices": [{"delta": {"content": "Let me loo"}}]}
_BEGUN_RESPONSES = {"type": "response.output_text.delta", "delta": "Let me loo"}


def _chat_error(error, **extra):
    """OpenRouter's shape: the error beside a finish_reason of "error", then [DONE]."""
    return _data(_BEGUN_CHAT, {"model": "m", "error": error, **extra,
                               "choices": [{"index": 0, "delta": {"content": ""},
                                            "finish_reason": "error"}]}, done=True)


def _responses_error(code, message):
    """The Responses API's own error event: code and message at the top level."""
    return _data(_BEGUN_RESPONSES,
                 {"type": "error", "code": code, "message": message, "param": None})


def _responses_failed(code, message):
    return _data(_BEGUN_RESPONSES,
                 {"type": "response.failed", "response": {
                     "status": "failed", "output": [],
                     "error": {"code": code, "message": message}}})


class _Server(BaseHTTPRequestHandler):
    """Answers each POST with the next scripted reply -- bytes as an event
    stream, a dict as a JSON body -- and then with the full answer."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.bodies.append(json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0)))))
        reply = self.server.script.pop(0) if self.server.script else None
        if reply is None:
            reply = (FULL[self.server.protocol] if self.server.bodies[-1].get("stream")
                     else self.server.single_ok)
        self.send_response(200)
        if isinstance(reply, dict):
            data = json.dumps(reply).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(reply)


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Server)
    httpd.bodies, httpd.script, httpd.protocol, httpd.single_ok = [], [], "openai_compat", {}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _session(repo, tmp_path, server, protocol, *script):
    server.protocol, server.script = protocol, list(script)
    host, port = server.server_address
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(
        f'[backend]\nprotocol = "{protocol}"\nbase_url = "http://{host}:{port}/v1"\n'
        'model = "m"\nrequest_max_retries = 2\n', encoding="utf-8")
    wiring = app.build(repo, home=home, console=Console(file=io.StringIO(), width=300))
    slept, notices = [], []
    wiring.runtime.sleep = slept.append
    wiring.runtime.events.subscribe(
        lambda event: notices.append(event.text) if isinstance(event, Notice) else None)
    return wiring, slept, notices


def _assistant_texts(wiring):
    return [m.blocks[0].text for m in wiring.runtime.context.history
            if m.role is Role.ASSISTANT]


def _retries(wiring):
    return [r.payload for r in SessionStore.read_records(wiring.session.path)
            if r.type == EventType.MODEL_RETRY]


# --- asked again -------------------------------------------------------------------


ASKED_AGAIN = [
    ("openai_compat", _chat_error({"code": "server_error", "message": "upstream fell over"}),
     "the provider reported server_error mid-turn: upstream fell over"),
    # vLLM: the chunk is the error, with the HTTP status as its code.
    ("openai_compat", _data(_BEGUN_CHAT, {"object": "error", "message": "engine died",
                                          "type": "InternalServerError", "code": 500},
                            done=True),
     "the provider reported 500 mid-turn: engine died"),
    ("openai_compat", _data(_BEGUN_CHAT, {"choices": [{"delta": {}, "finish_reason": "error"}]},
                            done=True),
     "the provider ended the turn with an error part-way and said no more"),
    ("responses", _responses_error("server_error", "The server had an error"),
     "the provider reported server_error mid-turn: The server had an error"),
    ("responses", _responses_failed("server_error", "The server had an error"),
     "the provider reported server_error mid-turn: The server had an error"),
    # A code never seen before is asked again, as codex does.
    ("responses", _data(_BEGUN_RESPONSES, {"type": "error", "error": {
        "type": "brand_new_trouble", "message": "nested, as some gateways send it"}}),
     "the provider reported brand_new_trouble mid-turn: nested, as some gateways send it"),
]


@pytest.mark.parametrize("protocol,reply,said", ASKED_AGAIN)
def test_a_failure_reported_part_way_is_asked_again(repo, tmp_path, server, protocol, reply,
                                                    said):
    wiring, slept, notices = _session(repo, tmp_path, server, protocol, reply)
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert outcome.text == "A is 1"
    assert len(server.bodies) == 2 and server.bodies[0] == server.bodies[1]
    # Nothing of the failed answer is kept.
    assert _assistant_texts(wiring) == ["A is 1"]
    [told] = [n for n in notices if "asking again" in n]
    assert told.startswith(said + "; asking again in 1s (try 2 of 6)")
    assert told.endswith("the answer above was cut off and is not kept")
    [retry] = _retries(wiring)
    assert retry["cut"] is True
    assert slept == [1.0]


def test_a_wait_the_provider_names_is_waited(repo, tmp_path, server):
    reply = _responses_failed("rate_limit_exceeded",
                              "Rate limit reached. Please try again in 2.5s. Visit ...")
    wiring, slept, notices = _session(repo, tmp_path, server, "responses", reply)
    assert wiring.runtime.run_turn("what is A?").text == "A is 1"
    wiring.session.close()
    assert slept == [2.5]
    assert any("asking again in 2.5s" in n or "asking again in 3s" in n for n in notices)


# --- not asked again ---------------------------------------------------------------


STOPS = [
    ("openai_compat", _chat_error({"code": "context_length_exceeded",
                                   "message": "too many tokens"}),
     TerminalStatus.FAILED, "the provider reported context_length_exceeded mid-turn: "
                            "too many tokens"),
    ("openai_compat", _data(_BEGUN_CHAT, {"object": "error", "message": "bad schema",
                                          "type": "BadRequestError", "code": 400}, done=True),
     TerminalStatus.FAILED, "the provider reported 400 mid-turn: bad schema"),
    ("responses", _responses_failed("invalid_prompt", "Invalid prompt: refused"),
     TerminalStatus.FAILED, "the provider reported invalid_prompt mid-turn: "
                            "Invalid prompt: refused"),
    ("responses", _responses_error("insufficient_quota", "You exceeded your current quota"),
     TerminalStatus.BLOCKED, "the account's quota is used up"),
]


@pytest.mark.parametrize("protocol,reply,status,said", STOPS)
def test_a_failure_waiting_cannot_fix_stops_at_once(repo, tmp_path, server, protocol, reply,
                                                    status, said):
    wiring, slept, notices = _session(repo, tmp_path, server, protocol, reply)
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert outcome.status is status
    assert said in (outcome.summary or "") + (outcome.text or "") + " ".join(notices)
    assert len(server.bodies) == 1 and slept == [] and _retries(wiring) == []
    assert _assistant_texts(wiring) == []


# --- answers that were not streamed -----------------------------------------------


def test_a_failed_response_that_was_not_streamed_is_asked_again(repo, tmp_path, server):
    """A server that does not stream: the adapter falls back to one JSON answer,
    and a failed one answers 200 too."""
    server.single_ok = {"status": "completed", "model": "m", "output": [
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "A is 1"}]}],
        "usage": {"input_tokens": 3, "output_tokens": 2}}
    failed = {"status": "failed", "output": [],
              "error": {"code": "server_error", "message": "The server had an error"}}
    # The first answer is not a stream, so the adapter asks again without
    # streaming; that answer is the failed response.
    wiring, slept, notices = _session(repo, tmp_path, server, "responses",
                                      server.single_ok | {"note": "not a stream"}, failed)
    outcome = wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert outcome.text == "A is 1"
    assert [b.get("stream") for b in server.bodies] == [True, False, False]
    assert any(n.startswith("the provider reported server_error in its answer: The server "
                            "had an error; asking again") for n in notices)


def test_a_chat_answer_that_is_an_error_is_asked_again(repo, tmp_path, server):
    server.single_ok = {"model": "m", "choices": [{"message": {"content": "A is 1"},
                                                   "finish_reason": "stop"}]}
    error = {"error": {"code": "server_error", "message": "upstream fell over"}}
    wiring, _, notices = _session(repo, tmp_path, server, "openai_compat",
                                  server.single_ok | {"note": "not a stream"}, error)
    assert wiring.runtime.run_turn("what is A?").text == "A is 1"
    wiring.session.close()
    assert any(n.startswith("the provider reported server_error in its answer") for n in notices)


def test_a_chat_answer_that_ended_in_an_error_is_asked_again(repo, tmp_path, server):
    server.single_ok = {"model": "m", "choices": [{"message": {"content": "A is 1"},
                                                   "finish_reason": "stop"}]}
    cut = {"model": "m", "choices": [{"message": {"content": "Let me loo"},
                                      "finish_reason": "error"}]}
    wiring, _, notices = _session(repo, tmp_path, server, "openai_compat",
                                  server.single_ok | {"note": "not a stream"}, cut)
    assert wiring.runtime.run_turn("what is A?").text == "A is 1"
    wiring.session.close()
    assert _assistant_texts(wiring) == ["A is 1"]
    assert any(n.startswith("the provider ended its answer with an error") for n in notices)


# --- the classifier itself --------------------------------------------------------


@pytest.mark.parametrize("error,kind", [
    ({"code": "server_error"}, StreamDropped),
    ({"type": "overloaded"}, StreamDropped),
    ({"code": 503}, StreamDropped),
    ({"code": 429}, StreamDropped),
    ({}, StreamDropped),
    (None, StreamDropped),
    ("a bare string", StreamDropped),
    ({"code": "context_length_exceeded"}, FatalError),
    ({"type": "invalid_request_error", "code": None}, FatalError),
    # OpenAI's request errors carry a code of their own beside the type.
    ({"type": "invalid_request_error", "code": "invalid_value"}, FatalError),
    ({"code": "usage_not_included"}, FatalError),
    ({"code": 404}, FatalError),
    ({"code": "insufficient_quota"}, QuotaExceeded),
    ({"type": "insufficient_quota", "code": "x"}, QuotaExceeded),
])
def test_what_is_asked_again_and_what_is_not(error, kind):
    with pytest.raises(FatalError if kind is not StreamDropped else StreamDropped) as caught:
        raise_reported_error(error, where="mid-turn")
    assert type(caught.value) is kind
    assert caught.value.payload == "" or caught.value.payload.startswith("{")


def test_the_hint_is_read_in_seconds_or_milliseconds():
    for message, wait in (("Please try again in 1.898s.", 1.898),
                          ("Please try again in 250ms.", 0.25),
                          ("busy", None)):
        with pytest.raises(StreamDropped) as caught:
            raise_reported_error({"code": "rate_limit_exceeded", "message": message},
                                 where="mid-turn")
        assert caught.value.retry_after == wait
