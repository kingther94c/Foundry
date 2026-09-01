"""Defects found by pointing Foundry at a real HTTP endpoint for the first time.

A local OpenClaw gateway (Chat Completions on 18789) and a Responses facade
(18790) turned up four things no scripted backend could: the `--json` stream was
lossy, it had no terminal event, its exit code was unexplained, and loopback
traffic would have been routed through a corporate proxy.

The captured wire bytes live in tests/fixtures/live_gateway/ so these stay
runnable on a machine with neither the gateway nor a network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry.cli.app import _event_payload, _plain
from foundry.core.conversation import Usage
from foundry.core.events import (
    MessageDelta,
    TerminalStatus,
    Termination,
    TokenCount,
    ToolBegin,
    ToolEnd,
    TurnStarted,
)

FIXTURES = Path(__file__).parent / "fixtures" / "live_gateway"


# --- the --json stream carries what the events carry ----------------------


def test_token_count_reaches_the_json_stream_with_its_numbers():
    """It serialized through a hand-maintained attribute list that named neither
    `usage` nor `session_total`, so every consumer of `foundry exec --json` got
    `{"kind": "token_count"}` and nothing else -- while the journal beside it
    recorded the real counts."""
    payload = _event_payload(TokenCount(usage=Usage(input_tokens=17095, output_tokens=20),
                                        session_total=Usage(input_tokens=17095, output_tokens=20)))

    assert payload["kind"] == "token_count"
    assert payload["usage"]["input_tokens"] == 17095
    assert payload["usage"]["output_tokens"] == 20
    assert payload["session_total"]["input_tokens"] == 17095


def test_tool_events_carry_the_call_id_that_pairs_them():
    begin = _event_payload(ToolBegin("call_7", "run_command", "pytest -q"))
    end = _event_payload(ToolEnd("call_7", "run_command", True, "exit 0", duration_ms=1200))

    assert begin["call_id"] == end["call_id"] == "call_7"
    assert end["duration_ms"] == 1200


def test_turn_events_carry_their_index():
    assert _event_payload(TurnStarted(turn_index=3))["turn_index"] == 3


def test_every_field_is_json_serializable():
    for event in (TokenCount(Usage(1, 2), Usage(3, 4)),
                  Termination(TerminalStatus.PARTIAL, "why", "summary"),
                  MessageDelta(text="hi"),
                  ToolEnd("c1", "read_file", True, "ok")):
        json.dumps(_event_payload(event))     # must not raise


def test_enums_serialize_as_their_value():
    payload = _event_payload(Termination(TerminalStatus.COMPLETED, "done"))
    assert payload["status"] == "completed"
    assert isinstance(payload["status"], str)


def test_plain_leaves_no_unserializable_object_behind():
    assert _plain(TerminalStatus.FAILED) == "failed"
    assert _plain((1, TerminalStatus.PARTIAL)) == [1, "partial"]
    assert _plain({"u": Usage(1, 2)})["u"]["input_tokens"] == 1
    assert _plain(object()).startswith("<")


# --- loopback is never sent to a proxy ------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:18789/v1/chat/completions",
    "http://localhost:18790/v1/responses",
    "http://[::1]:18789/v1/chat/completions",
    "http://127.5.5.5:9000/x",
])
def test_a_corporate_proxy_does_not_swallow_loopback(url, monkeypatch):
    """urllib's bypass list rarely names 127.0.0.1 -- proxy_bypass returns False
    for it -- so with HTTP_PROXY set, a local gateway was sent to the proxy,
    which cannot route back to the caller's own loopback. It surfaced as a
    connect timeout, which reads like the local server being down."""
    from foundry.core.httpc import HttpClient, _proxy_for

    monkeypatch.setenv("HTTP_PROXY", "http://corp-proxy.example.com:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy.example.com:8080")

    assert _proxy_for(url) is None
    conn, _, _ = HttpClient()._connect(url)
    assert "corp-proxy" not in conn.host


def test_a_real_host_still_goes_through_the_proxy(monkeypatch):
    from foundry.core.httpc import HttpClient, _proxy_for

    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy.example.com:8080")
    url = "https://gateway.corp.example.com/v1/chat/completions"

    assert _proxy_for(url) == "http://corp-proxy.example.com:8080"
    conn, _, _ = HttpClient()._connect(url)
    assert conn.host == "corp-proxy.example.com"


def test_an_https_target_behind_a_proxy_is_a_tls_connection(monkeypatch):
    """The connection class must follow the TARGET's scheme, not the proxy's.
    Choosing it from the proxy meant an ordinary HTTP_PROXY=http://proxy:8080
    built a plain HTTPConnection, whose connect() issues CONNECT and then stops
    -- only HTTPSConnection wraps the socket afterwards -- so Foundry wrote
    `Authorization: Bearer <key>` into the tunnel in the clear."""
    import http.client

    from foundry.core.httpc import HttpClient

    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy.example.com:8080")
    conn, path, extra = HttpClient()._connect("https://api.openai.com/v1/chat/completions")

    assert isinstance(conn, http.client.HTTPSConnection), \
        "a plain HTTPConnection never wraps the tunnel in TLS"
    assert conn.host == "corp-proxy.example.com"
    assert conn._tunnel_host == "api.openai.com"
    assert path == "/v1/chat/completions"


def test_the_credential_enters_the_tunnel_as_tls(monkeypatch):
    """End to end against a real socket: the first byte into the tunnel must be
    0x16 (TLS ClientHello), not 'P' from a plaintext POST."""
    import socket
    import threading

    from foundry.core.errors import FoundryError
    from foundry.core.httpc import HttpClient

    secret = "sk-CANARY-TOKEN-abcdefghijklmnop"
    captured: dict[str, bytes] = {}
    ready: list[int] = []

    def proxy():
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        ready.append(srv.getsockname()[1])
        try:
            sock, _ = srv.accept()
            sock.settimeout(10)
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                request += chunk
            captured["connect"] = request
            sock.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            captured["tunnel"] = sock.recv(4096)
            sock.close()
        except OSError:
            captured.setdefault("tunnel", b"")
        finally:
            srv.close()

    thread = threading.Thread(target=proxy, daemon=True)
    thread.start()
    while not ready:
        pass
    monkeypatch.setenv("HTTPS_PROXY", f"http://user:pw@127.0.0.1:{ready[0]}")

    with pytest.raises(FoundryError):        # the fake proxy is not an origin
        HttpClient(connect_timeout=8, read_timeout=8).post_json(
            "https://api.openai.com/v1/chat/completions",
            {"model": "m", "messages": []},
            {"Authorization": f"Bearer {secret}"})
    thread.join(timeout=10)

    tunnel = captured.get("tunnel", b"")
    assert tunnel, "nothing reached the tunnel"
    assert secret.encode() not in tunnel, "the API key went out in cleartext"
    assert tunnel[:1] == b"\x16", f"expected a TLS ClientHello, got {tunnel[:16]!r}"
    assert b"Proxy-Authorization: Basic" in captured["connect"]


def test_a_tls_fronted_proxy_is_refused_rather_than_mishandled(monkeypatch):
    """stdlib set_tunnel cannot express TLS-to-proxy plus a CONNECT tunnel. Say
    so, instead of silently picking one of the two."""
    from foundry.core.errors import ConfigError
    from foundry.core.httpc import HttpClient

    monkeypatch.setenv("HTTPS_PROXY", "https://corp-proxy.example.com:8443")
    with pytest.raises(ConfigError, match="http://"):
        HttpClient()._connect("https://api.openai.com/v1/chat/completions")


def test_a_proxy_credential_reaches_a_plain_http_request(monkeypatch):
    """For http-through-proxy the credential belongs on the request, not on a
    CONNECT. It was dropped, so an authenticating proxy answered 407 and the run
    failed with no hint why."""
    from foundry.core.httpc import HttpClient

    monkeypatch.setenv("HTTP_PROXY", "http://user:pw@corp-proxy.example.com:8080")
    conn, target, extra = HttpClient()._connect("http://gateway.corp.example.com/v1/x")

    assert conn.host == "corp-proxy.example.com"
    assert target == "http://gateway.corp.example.com/v1/x", "absolute form required"
    assert extra["Proxy-Authorization"].startswith("Basic ")


def test_a_hostname_merely_containing_localhost_is_not_loopback():
    from foundry.core.httpc import _is_loopback

    assert not _is_loopback("localhost.evil.com")
    assert not _is_loopback("notlocalhost")
    assert not _is_loopback("10.0.0.1")
    assert _is_loopback("dev.localhost")


# --- the shapes the live gateway actually sends ---------------------------


def _fixture(name: str) -> bytes:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"capture {name} not present")
    return path.read_bytes()


def test_the_usage_chunk_with_no_choices_is_still_read():
    """The gateway's final chunk carries usage and an EMPTY choices array. An
    adapter that indexes choices[0] before looking for usage reports zero
    tokens for every streamed turn."""
    from foundry.core.backends.base import TurnFinished
    from foundry.core.backends.openai_compat import OpenAICompatBackend

    frames = [json.loads(line[5:].strip())
              for line in _fixture("chat_stream.raw").decode("utf-8").splitlines()
              if line.startswith("data:") and line[5:].strip() != "[DONE]"]
    assert any(f.get("usage") and not f.get("choices") for f in frames), \
        "the fixture no longer exercises the empty-choices usage chunk"

    backend = OpenAICompatBackend(base_url="http://x/v1", model="m")
    backend.client = _StubClient(frames)
    events = list(backend._stream({"model": "m"}))
    finished = [e for e in events if isinstance(e, TurnFinished)][-1]

    assert finished.turn.usage.input_tokens == 16114
    assert finished.turn.usage.output_tokens == 25
    assert finished.turn.text == "OK"


def test_the_responses_stream_the_facade_actually_sends_parses():
    """This adapter had never met a real server. The facade prefixes every frame
    with an `event:` line and still ends with `data: [DONE]`."""
    from foundry.core.backends.base import TurnFinished
    from foundry.core.backends.responses import ResponsesBackend

    raw = _fixture("responses_stream.raw").decode("utf-8")
    assert "event: response.created" in raw, "fixture lost its event: lines"

    frames = [json.loads(line[5:].strip()) for line in raw.splitlines()
              if line.startswith("data:") and line[5:].strip() != "[DONE]"]

    backend = ResponsesBackend(base_url="http://x/v1", model="m")
    backend.client = _StubClient(frames)
    events = list(backend._stream({"model": "m"}))
    finished = [e for e in events if isinstance(e, TurnFinished)][-1]

    assert finished.turn.text == "OK"
    assert finished.turn.usage.input_tokens == 16114


class _StubClient:
    """Replays captured frames through the stream_sse contract."""

    def __init__(self, frames):
        self._frames = frames

    def stream_sse(self, *args, **kwargs):
        yield from self._frames

    def post_json(self, *args, **kwargs):  # pragma: no cover - not used here
        raise AssertionError("the streaming path must not fall back to POST")
