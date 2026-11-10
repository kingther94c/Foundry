"""Retries you can see, and none that cannot help (D-085).

A 429, a 5xx or a dropped connection was retried in silence: up to four tries
with waits of up to a minute each, which looked exactly like a hang. Each
retry is now said before its wait -- what failed, how long, which try -- in
the terminal and in ``exec --json``, journaled, and counted by ``foundry
report``. A 429 that says the account's quota is spent is not retried at all:
no wait clears it, so the task ends blocked at once.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.report import render
from foundry.cli.wiring import build
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.conversation import Message, Role, TurnRequest
from foundry.core.errors import FatalError, QuotaExceeded, TransientError
from foundry.core.events import Notice, TerminalStatus
from foundry.core.httpc import (Response, open_retrying_stream, raise_for_status,
                                retry_with_backoff)
from foundry.core.runtime import _seconds
from foundry.core.session import EventType, SessionStore

from test_cli_e2e import repo, text_turn  # noqa: F401  (fixture)
from test_mcp import _config


# --- the helpers -------------------------------------------------------------------------


def _flaky(failures):
    """An operation that raises each of ``failures`` in turn, then answers."""
    pending = list(failures)

    def operation():
        if pending:
            raise pending.pop(0)
        return "answer"
    return operation


def test_each_retry_is_told_before_its_wait():
    seen = []
    result = retry_with_backoff(
        _flaky([TransientError("server error (HTTP 503)")] * 3), attempts=4,
        sleep=lambda delay: seen.append(("slept", delay)),
        on_retry=lambda exc, attempt, attempts, delay: seen.append(
            ("told", str(exc), attempt, attempts, delay)))
    assert result == "answer"
    assert seen == [
        ("told", "server error (HTTP 503)", 2, 4, 1.0), ("slept", 1.0),
        ("told", "server error (HTTP 503)", 3, 4, 2.0), ("slept", 2.0),
        ("told", "server error (HTTP 503)", 4, 4, 4.0), ("slept", 4.0)]


def test_the_wait_told_is_the_one_taken():
    told, slept = [], []
    retry_with_backoff(
        _flaky([TransientError("rate limited (HTTP 429)", retry_after=30.0),
                TransientError("rate limited (HTTP 429)", retry_after=600.0)]),
        attempts=4, sleep=slept.append,
        on_retry=lambda exc, attempt, attempts, delay: told.append(delay))
    assert told == slept == [30.0, 60.0]


def test_the_last_failure_is_not_announced_as_a_retry():
    told = []
    with pytest.raises(TransientError):
        retry_with_backoff(_flaky([TransientError("connection failed")] * 2), attempts=2,
                           sleep=lambda delay: None,
                           on_retry=lambda *args: told.append(args[1]))
    assert told == [2]


def test_what_is_not_retried_is_not_announced():
    told = []
    with pytest.raises(FatalError):
        retry_with_backoff(_flaky([FatalError("request rejected (HTTP 400)")]),
                           sleep=lambda delay: None, on_retry=lambda *args: told.append(args))
    assert told == []


def test_a_stream_is_retried_aloud_too():
    told = []
    stream = open_retrying_stream(
        lambda: iter(_events()), sleep=lambda delay: None,
        on_retry=lambda exc, attempt, attempts, delay: told.append((str(exc), attempt)))
    assert list(stream) == ["first", "second"]
    assert told == [("stream failed", 2)]


@pytest.mark.parametrize("delay, said", [
    (0.0, "0s"), (0.25, "0.2s"), (1.0, "1s"), (1.5, "1.5s"), (10.0, "10s"), (60.0, "60s")])
def test_a_wait_reads_as_seconds(delay, said):
    assert _seconds(delay) == said


_calls = {"n": 0}


def _events():
    _calls["n"] += 1
    if _calls["n"] == 1:
        raise TransientError("stream failed")
    yield "first"
    yield "second"


# --- every backend passes it on ----------------------------------------------------------


class _FailingClient:
    """Fails once in a way worth retrying, then in a way that is not."""

    def __init__(self):
        self.calls = 0

    def post_json(self, url, body, headers):
        return self._fail()

    def stream_sse(self, url, body, headers, **kwargs):
        return self._fail()

    def _fail(self):
        self.calls += 1
        if self.calls == 1:
            raise TransientError("server error (HTTP 503)", retry_after=0.0)
        raise FatalError("stop here")


@pytest.mark.parametrize("backend", [OpenAICompatBackend, ResponsesBackend, AnthropicBackend])
@pytest.mark.parametrize("stream", [True, False])
def test_every_backend_says_when_it_retries(backend, stream):
    told = []
    client = _FailingClient()
    made = backend(base_url="https://h/v1", model="m", client=client, stream=stream,
                   max_retries=3,
                   on_retry=lambda exc, attempt, attempts, delay: told.append(
                       (str(exc), attempt, attempts, delay)))
    request = TurnRequest(messages=(Message.text(Role.USER, "go"),), tools=(), model="m")
    with pytest.raises(FatalError, match="stop here"):
        list(made.stream_turn(request))
    assert client.calls == 2
    assert told == [("server error (HTTP 503)", 2, 3, 0.0)]


# --- a spent quota -----------------------------------------------------------------------

QUOTA = json.dumps({"error": {"message": "You exceeded your current quota.",
                              "type": "insufficient_quota", "code": "insufficient_quota"}})


def test_a_spent_quota_is_not_a_rate_limit():
    with pytest.raises(QuotaExceeded, match="quota is used up .*waiting will not help"):
        raise_for_status(Response(status=429, headers={}, body=QUOTA.encode()))
    with pytest.raises(TransientError, match="rate limited"):
        raise_for_status(Response(status=429, headers={}, body=b'{"error": {"type": '
                                                               b'"rate_limit_exceeded"}}'))
    assert not issubclass(QuotaExceeded, TransientError)


def test_a_spent_quota_is_tried_once():
    tries = []

    def operation():
        tries.append(1)
        raise_for_status(Response(status=429, headers={}, body=QUOTA.encode()))
    with pytest.raises(QuotaExceeded):
        retry_with_backoff(operation, sleep=lambda delay: None)
    assert len(tries) == 1


# --- a session ---------------------------------------------------------------------------


class _Scripted(BaseHTTPRequestHandler):
    """Answers each POST with the next scripted (status, headers, body)."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        status, headers, body = self.server.script.pop(0) if self.server.script else (
            200, {}, _ok("done"))
        self.server.posts += 1
        data = body.encode()
        self.send_response(status)
        for name, value in {"Content-Type": "application/json", **headers}.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _ok(text):
    return json.dumps({"model": "fake", "choices": [text_turn(text)],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


@pytest.fixture()
def scripted():
    httpd = HTTPServer(("127.0.0.1", 0), _Scripted)
    httpd.script, httpd.posts = [], 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _home(tmp_path, httpd):
    host, port = httpd.server_address
    return _config(tmp_path / "home", (f'[backend]\nbase_url = "http://{host}:{port}/v1"\n'
                                       'model = "fake"\nstream = false\n'))


BUSY = (503, {"Retry-After": "0"}, '{"error": {"message": "the gateway is busy"}}')


def test_a_session_says_it_is_retrying_and_journals_it(repo, tmp_path, scripted):
    scripted.script = [BUSY, BUSY, (200, {}, _ok("A is 1"))]
    out = io.StringIO()
    wiring = build(repo, home=_home(tmp_path, scripted),
                       console=Console(file=out, width=200))
    notices = []
    wiring.runtime.events.subscribe(
        lambda event: notices.append(event) if isinstance(event, Notice) else None)
    wiring.runtime.run_turn("what is A?")
    wiring.session.close()
    assert scripted.posts == 3
    said = [n.text for n in notices if "retrying" in n.text]
    assert said == ["server error (HTTP 503); retrying in 0s (try 2 of 4)",
                    "server error (HTTP 503); retrying in 0s (try 3 of 4)"]
    assert all(n.level == "warning" for n in notices if "retrying" in n.text)
    assert "server error (HTTP 503); retrying in 0s (try 2 of 4)" in out.getvalue()
    retries = [r.payload for r in SessionStore.read_records(wiring.session.path)
               if r.type == EventType.MODEL_RETRY]
    assert retries == [
        {"attempt": 2, "attempts": 4, "delay": 0.0,
         "reason": 'server error (HTTP 503): {"error": {"message": "the gateway is busy"}}'},
        {"attempt": 3, "attempts": 4, "delay": 0.0,
         "reason": 'server error (HTTP 503): {"error": {"message": "the gateway is busy"}}'}]
    assert "model retries         2" in render(tmp_path / "home" / "sessions")
    assert json.loads(render(tmp_path / "home" / "sessions", as_json=True))[
        "model_retries"] == 2


def test_a_retried_session_resumes_as_if_it_had_not_been(repo, tmp_path, scripted):
    """The record sits between a request and its response; a resume skips it."""
    scripted.script = [BUSY, (200, {}, _ok("A is 1"))]
    home = _home(tmp_path, scripted)
    first = build(repo, home=home, console=Console(file=io.StringIO(), width=100))
    first.runtime.run_turn("what is A?")
    session_id = first.session.path.parent.name
    history = first.runtime.context.history
    first.session.close()
    again = build(repo, home=home, console=Console(file=io.StringIO(), width=100),
                      resume=session_id)
    assert again.runtime.context.history == history
    again.session.close()


def test_exec_json_carries_the_retry_and_a_spent_quota_blocks_at_once(
        repo, tmp_path, scripted, monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_HOME", str(_home(tmp_path, scripted)))
    workspace = ["--workspace", str(repo)]

    scripted.script = [BUSY, (200, {}, _ok("A is 1"))]
    app.main(["exec", "what is A?", "--json", *workspace])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert {"kind": "notice", "level": "warning",
            "text": "server error (HTTP 503); retrying in 0s (try 2 of 4)"} in events

    scripted.script, scripted.posts = [(429, {"Retry-After": "0"}, QUOTA)], 0
    code = app.main(["exec", "what is A?", "--json", *workspace])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert scripted.posts == 1
    assert code == 11
    assert not any("retrying" in e.get("text", "") for e in events)
    errors = [e for e in events if e["kind"] == "error"]
    assert errors and errors[0]["category"] == "quota"
    end = [e for e in events if e["kind"] == "termination"][-1]
    assert end["status"] == TerminalStatus.BLOCKED.value
    assert "quota is used up" in end["reason"]
