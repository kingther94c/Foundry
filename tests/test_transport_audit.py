"""Transport defects found by auditing the code against real captured traffic.

Having a live endpoint made the provider layer worth reading closely. These are
the confirmed findings from that pass, each reproduced before it was fixed.
"""

from __future__ import annotations

import datetime
import email.utils
import json

import pytest

from foundry.core.errors import ConfigError, FatalError, TransientError
from foundry.core.httpc import Response, raise_for_status, retry_with_backoff


def _response(status: int, headers: dict[str, str] | None = None, body: bytes = b"") -> Response:
    return Response(status=status, headers=headers or {}, body=body)


# --- Retry-After is read wherever a server sends it ------------------------


def test_retry_after_is_honoured_on_503_not_only_429():
    """A gateway shedding load answers 503 with Retry-After too. Reading it only
    on 429 meant Foundry re-sent at 1s, 3s, 7s -- inside the window the server
    had just asked it to wait out -- then gave up at 7s on a server that said 30."""
    with pytest.raises(TransientError) as caught:
        raise_for_status(_response(503, {"retry-after": "30"}))
    assert caught.value.retry_after == 30.0


def test_retry_after_accepts_the_http_date_form():
    """RFC 9110 permits delta-seconds or an HTTP-date; only the first parsed, so
    the date form was silently discarded."""
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=45)
    with pytest.raises(TransientError) as caught:
        raise_for_status(_response(429, {"retry-after": email.utils.format_datetime(when)}))
    assert caught.value.retry_after is not None
    assert 30 <= caught.value.retry_after <= 60


def test_a_retry_after_in_the_past_is_not_negative():
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=90)
    with pytest.raises(TransientError) as caught:
        raise_for_status(_response(429, {"retry-after": email.utils.format_datetime(when)}))
    assert caught.value.retry_after == 0.0


def test_an_unparseable_retry_after_falls_back_to_backoff():
    with pytest.raises(TransientError) as caught:
        raise_for_status(_response(429, {"retry-after": "soon-ish"}))
    assert caught.value.retry_after is None


# --- no retry count crashes outside the taxonomy --------------------------


@pytest.mark.parametrize("attempts", [0, -3, None, "4"])
def test_a_bad_retry_count_still_makes_one_request(attempts):
    """`raise last` after a zero-trip loop is `raise None` -- a TypeError, not a
    FoundryError, so the CLI died with a traceback instead of running once."""
    calls = []

    def operation():
        calls.append(1)
        return "ok"

    assert retry_with_backoff(operation, attempts=attempts) == "ok"
    assert len(calls) == 1


def test_a_transient_failure_with_one_attempt_raises_the_real_error():
    def operation():
        raise TransientError("nope")

    with pytest.raises(TransientError, match="nope"):
        retry_with_backoff(operation, attempts=0)


def test_config_refuses_a_retry_count_below_one(tmp_path):
    from foundry.core.config import load_config

    (tmp_path / "config.toml").write_text(
        "[backend]\nrequest_max_retries = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="at least 1"):
        load_config(home=tmp_path)


def test_config_refuses_a_non_integer_retry_count(tmp_path):
    from foundry.core.config import load_config

    (tmp_path / "config.toml").write_text(
        '[backend]\nrequest_max_retries = "four"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config(home=tmp_path)


# --- the provider's own words reach the user ------------------------------


def test_the_error_body_is_not_thrown_away():
    """raise_for_status keeps 500 bytes of the body on `payload` and nothing
    read it, so a 400 surfaced as "request rejected (HTTP 400)" while the body
    said exactly which field was wrong."""
    from foundry.core.runtime import _explain

    body = json.dumps({"error": {"message": "Missing user message in `messages`.",
                                 "type": "invalid_request_error"}}).encode()
    with pytest.raises(FatalError) as caught:
        raise_for_status(_response(400, body=body))

    explained = _explain(caught.value)
    assert "HTTP 400" in explained
    assert "Missing user message" in explained


def test_an_error_without_a_body_reads_cleanly():
    from foundry.core.runtime import _explain

    with pytest.raises(FatalError) as caught:
        raise_for_status(_response(418))
    assert _explain(caught.value) == str(caught.value)


# --- a bad CA bundle is a config error, not a traceback -------------------


def test_an_unreadable_ca_bundle_names_the_file_and_the_variable(tmp_path):
    """ca_bundle silently adopts SSL_CERT_FILE -- set for other tooling, often
    stale after a rotation -- and a missing path escaped as a bare
    FileNotFoundError with no filename, outside the taxonomy."""
    from foundry.core.httpc import HttpClient

    missing = tmp_path / "no-such-bundle.pem"
    client = HttpClient(ca_bundle=str(missing))

    with pytest.raises(ConfigError) as caught:
        client._connect("https://api.openai.com/v1/chat/completions")
    message = str(caught.value)
    assert "no-such-bundle.pem" in message
    assert "SSL_CERT_FILE" in message


# --- adapter shapes a real gateway can produce ----------------------------


def test_a_tool_call_with_null_arguments_does_not_crash_the_run():
    """`.get("arguments", "{}")` only defaults when the key is ABSENT. A
    present-but-null value -- what an unset proto3 field serializes to -- put
    None into the block, and json.loads(None) raises TypeError, which
    parse_arguments does not catch."""
    from foundry.core.backends.openai_compat import OpenAICompatBackend

    payload = {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": None}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    backend = OpenAICompatBackend(base_url="http://x/v1", model="m")
    backend.client = _PostOnly(payload)

    events = backend._single({"model": "m"})
    call = [e for e in events if hasattr(e, "turn")][-1].turn.tool_calls[0]

    assert call.arguments == "{}"
    assert call.parse_arguments() == {}


def test_the_non_streaming_degrade_drops_stream_options():
    """The spread overwrote `stream` but left `stream_options`, which OpenAI and
    strict gateways reject with 400 when stream is false -- so the degrade path
    built to rescue the turn failed it instead."""
    from foundry.core.backends.openai_compat import OpenAICompatBackend
    from foundry.core.httpc import NotStreaming

    seen: dict = {}

    class _Client:
        def stream_sse(self, *args, **kwargs):
            raise NotStreaming("json, not a stream", body=b"{}")
            yield  # pragma: no cover

        def post_json(self, url, payload, headers):
            seen.update(payload)
            return _Json({"choices": [{"message": {"content": "hi"},
                                       "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                    "total_tokens": 2}})

    backend = OpenAICompatBackend(base_url="http://x/v1", model="m")
    backend.client = _Client()
    list(backend._stream({"model": "m", "stream": True,
                          "stream_options": {"include_usage": True}}))

    assert seen["stream"] is False
    assert "stream_options" not in seen, "a non-streaming body carried stream_options"


def test_a_stream_that_only_says_done_is_reported_as_a_failure():
    """The truncation guard catches "ended with no terminator" but not
    "terminator arrived with no content". A gateway that opens the stream,
    faults, and closes with just [DONE] produced a successful empty turn."""
    from foundry.core.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(base_url="http://x/v1", model="m")
    backend.client = _StreamOnly([])

    with pytest.raises(TransientError, match="no content"):
        list(backend._stream({"model": "m"}))


def test_a_stream_with_content_is_still_fine():
    from foundry.core.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(base_url="http://x/v1", model="m")
    backend.client = _StreamOnly([
        {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])
    finished = [e for e in backend._stream({"model": "m"}) if hasattr(e, "turn")][-1]
    assert finished.turn.text == "hi"


# --- a correct patch must actually take effect ----------------------------


def test_a_same_second_rewrite_is_not_masked_by_a_stale_pyc(tmp_path):
    """Python validates a cached .pyc against the source's size and its mtime
    truncated to SECONDS. An agent's edit-then-test cycle is sub-second and a
    one-character fix keeps the size identical, so the interpreter re-ran the
    OLD code -- and the model, seeing its correct patch still fail, patches
    again. Found while writing demo/mini_foundry.py, which hit it for real."""
    import os
    import subprocess
    import sys

    from foundry.core.winapi import child_environment

    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "calc.py"
    source.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "check.py").write_text(
        "from calc import add\nimport sys\nsys.exit(0 if add(2, 3) == 5 else 1)\n",
        encoding="utf-8")

    env = child_environment()
    assert env.get("PYTHONDONTWRITEBYTECODE") == "1"

    def check() -> int:
        return subprocess.run([sys.executable, "check.py"], cwd=repo,
                              capture_output=True, env=env).returncode

    assert check() == 1, "the bug should be present at the start"

    stamp = source.stat()
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    # Same size already; put the mtime back into the same second, which is what
    # a fast edit-then-test cycle produces on its own.
    os.utime(source, (stamp.st_atime, stamp.st_mtime))

    assert check() == 0, "a correct patch did not take effect"


class _Json:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _PostOnly:
    def __init__(self, payload):
        self._payload = payload

    def post_json(self, *args, **kwargs):
        return _Json(self._payload)


class _StreamOnly:
    def __init__(self, frames):
        self._frames = frames

    def stream_sse(self, *args, **kwargs):
        yield from self._frames
