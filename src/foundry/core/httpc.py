"""A small HTTP/SSE client on the standard library.

Chosen over httpx (7 wheels) and requests (5) for one decisive reason beyond
size: ``ssl.create_default_context()`` loads the Windows CA and ROOT stores, so a
corporate TLS-inspecting proxy whose CA is installed machine-wide works with no
configuration. certifi-backed clients trust only the Mozilla bundle and fail with
CERTIFICATE_VERIFY_FAILED until someone discovers SSL_CERT_FILE -- the single
most common enterprise support ticket for Python HTTP clients.

Scope is deliberately narrow: POST JSON, stream SSE, honour proxy settings, and
raise the error taxonomy. No redirects, no connection pooling.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import itertools
import json
import os
import socket
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator

from foundry.core.errors import (
    AuthError,
    ConfigError,
    FatalError,
    ProtocolError,
    TransientError,
)

DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 300.0


class NotStreaming(Exception):
    """The server answered a stream request with a single JSON response.

    Not an error in the taxonomy sense: it is a capability difference, and the
    backend degrades to non-streaming rather than failing the turn.
    """

    def __init__(self, message: str, *, body: bytes) -> None:
        super().__init__(message)
        self.body = body


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProtocolError(f"response was not valid JSON: {exc}") from exc


def _build_ssl_context(ca_bundle: str | None = None) -> ssl.SSLContext:
    if ca_bundle:
        context = ssl.create_default_context(cafile=ca_bundle)
    else:
        # Loads the Windows CA/ROOT stores: corporate MITM CAs work unconfigured.
        context = ssl.create_default_context()
    return context


def _is_loopback(host: str) -> bool:
    """Loopback is never reachable through a proxy, whatever the config says."""
    if not host:
        return False
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _proxy_for(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    # A corporate machine has HTTP_PROXY set and its bypass list rarely names
    # 127.0.0.1 -- urllib.proxy_bypass returns False for it -- so a local
    # gateway or facade was sent to the proxy, which cannot route back to the
    # caller's own loopback. It failed as a connect timeout, which reads like
    # the local server being down.
    if _is_loopback(host):
        return None
    proxies = urllib.request.getproxies()  # env vars and the Windows registry
    if urllib.request.proxy_bypass(host):
        return None
    return proxies.get(parsed.scheme)


@dataclass(slots=True)
class HttpClient:
    ca_bundle: str | None = field(default_factory=lambda: os.environ.get("FOUNDRY_CA_BUNDLE")
                                  or os.environ.get("SSL_CERT_FILE"))
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT

    def _connect(self, url: str) -> tuple[http.client.HTTPConnection, str, dict[str, str]]:
        """Returns the connection, the request target, and any headers the proxy
        itself needs on the request (as opposed to on the CONNECT)."""
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise FatalError(f"unsupported URL scheme: {parsed.scheme!r}")
        host, port = parsed.hostname, parsed.port
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        proxy = _proxy_for(url)
        if not proxy:
            if parsed.scheme == "https":
                return http.client.HTTPSConnection(
                    host, port or 443, timeout=self.connect_timeout,
                    context=_build_ssl_context(self.ca_bundle),
                ), path, {}
            return (http.client.HTTPConnection(host, port or 80, timeout=self.connect_timeout),
                    path, {})

        proxy_parts = urllib.parse.urlsplit(proxy)
        proxy_host = proxy_parts.hostname
        proxy_port = proxy_parts.port or 8080
        proxy_headers: dict[str, str] = {}
        if proxy_parts.username:
            token = base64.b64encode(
                f"{proxy_parts.username}:{proxy_parts.password or ''}".encode()
            ).decode()
            proxy_headers["Proxy-Authorization"] = f"Basic {token}"

        if parsed.scheme == "https":
            # The connection class must follow the TARGET's scheme, not the
            # proxy's. Choosing it from the proxy meant an ordinary
            # HTTP_PROXY=http://proxy:8080 built a plain HTTPConnection, whose
            # connect() issues CONNECT and then stops -- only HTTPSConnection
            # wraps the socket afterwards. Foundry then wrote plaintext into the
            # tunnel: `POST /v1/chat/completions` with `Authorization: Bearer
            # <key>` in the clear, readable by the proxy and every hop past it.
            # Since base_url defaults to https://api.openai.com/v1, that was the
            # default path on any machine with a proxy configured.
            if proxy_parts.scheme == "https":
                raise ConfigError(
                    f"proxy {proxy_host} is declared https, and a TLS connection to the "
                    "proxy itself cannot be combined with a CONNECT tunnel here. Point "
                    "HTTPS_PROXY at the proxy's http:// endpoint."
                )
            conn = http.client.HTTPSConnection(
                proxy_host, proxy_port, timeout=self.connect_timeout,
                context=_build_ssl_context(self.ca_bundle))
            # Cleartext CONNECT to the proxy, then wrap_socket against the
            # origin -- the credential is in the request, which is inside TLS.
            conn.set_tunnel(host, port or 443, headers=proxy_headers)
            return conn, path, {}

        # Plain http through a proxy uses absolute-form and carries the proxy's
        # own credential on the request. It used to be dropped here, so an
        # authenticating proxy answered 407 and the run failed with no hint why.
        conn = http.client.HTTPConnection(proxy_host, proxy_port,
                                          timeout=self.connect_timeout)
        return conn, url, proxy_headers

    def post_json(self, url: str, payload: dict, headers: dict[str, str]) -> Response:
        conn, path, proxy_headers = self._connect(url)
        body = json.dumps(payload).encode("utf-8")
        send_headers = {"Content-Type": "application/json", "Accept": "application/json",
                        **proxy_headers, **headers}
        try:
            conn.request("POST", path, body=body, headers=send_headers)
            conn.sock.settimeout(self.read_timeout)
            raw = conn.getresponse()
            data = raw.read()
            response = Response(status=raw.status,
                                headers={k.lower(): v for k, v in raw.getheaders()},
                                body=data)
        except (socket.timeout, TimeoutError) as exc:
            raise TransientError(f"request timed out: {exc}") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise TransientError(f"connection failed: {exc}") from exc
        finally:
            conn.close()

        raise_for_status(response)
        return response

    def stream_sse(self, url: str, payload: dict, headers: dict[str, str],
                   *, expect_done_sentinel: bool = True) -> Iterator[dict]:
        """Yield parsed SSE ``data:`` payloads until the stream ends.

        If the server answers with JSON instead of an event stream -- which a
        gateway that does not implement streaming will do -- this raises
        :class:`NotStreaming` carrying the body, so the caller can parse it as a
        single response rather than silently producing an empty turn.

        Staleness is enforced by the socket timeout, since http.client has no
        overall read deadline.
        """
        conn, path, proxy_headers = self._connect(url)
        body = json.dumps(payload).encode("utf-8")
        send_headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                        **proxy_headers, **headers}
        try:
            conn.request("POST", path, body=body, headers=send_headers)
            conn.sock.settimeout(self.read_timeout)
            raw = conn.getresponse()

            if raw.status >= 400:
                raise_for_status(Response(status=raw.status,
                                          headers={k.lower(): v for k, v in raw.getheaders()},
                                          body=raw.read()))

            content_type = raw.getheader("Content-Type", "")
            if "event-stream" not in content_type.lower():
                raise NotStreaming(
                    f"server replied with {content_type or 'no content type'} "
                    "instead of an event stream",
                    body=raw.read(),
                )

            terminated = False
            for line in raw:
                text = line.decode("utf-8", errors="replace").strip()
                if not text or text.startswith(":"):
                    continue
                if not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    terminated = True
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProtocolError(f"malformed SSE payload: {exc}") from exc

            # A chunked body that ends without its terminating chunk reads as a
            # clean EOF, so a connection dropped mid-turn was indistinguishable
            # from a model that finished speaking: a truncated half-sentence was
            # presented as the complete answer. Transient, so it is retried.
            #
            # Only Chat Completions sends [DONE]; the Responses protocol signals
            # completion with its own event, so that adapter checks for itself.
            if expect_done_sentinel and not terminated:
                raise TransientError(
                    "the response stream ended without its terminator; "
                    "the connection was cut mid-turn"
                )
        except (socket.timeout, TimeoutError) as exc:
            raise TransientError(f"stream stalled: {exc}") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise TransientError(f"stream failed: {exc}") from exc
        finally:
            conn.close()


def raise_for_status(response: Response) -> None:
    """Map HTTP status onto the error taxonomy the runtime acts on."""
    if response.status < 400:
        return

    detail = response.body.decode("utf-8", errors="replace")[:500]

    if response.status in (401, 403):
        raise AuthError(f"authentication rejected (HTTP {response.status})", payload=detail)
    if response.status == 429:
        retry_after = response.headers.get("retry-after")
        seconds = None
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = None
        raise TransientError("rate limited (HTTP 429)", payload=detail, retry_after=seconds)
    if response.status == 407:
        raise AuthError(
            "the proxy requires authentication. If it uses NTLM or Kerberos "
            "(Proxy-Authenticate: Negotiate), Foundry cannot authenticate to it; "
            "set HTTPS_PROXY to a proxy that accepts Basic auth or bypass it.",
            payload=detail,
        )
    if response.status >= 500:
        raise TransientError(f"server error (HTTP {response.status})", payload=detail)
    raise FatalError(f"request rejected (HTTP {response.status})", payload=detail)


def open_retrying_stream(make_stream, *, attempts: int = 4, sleep=time.sleep):
    """Retry the connection, then hand back a lazy iterator over the rest.

    Wrapping the whole stream in ``list()`` made it retryable but stopped it
    being a stream: nothing reached the renderer until the model had finished
    generating, and the full response sat in memory as parsed dicts. Since
    ``stream_sse`` is a generator, everything worth retrying -- the connect, the
    status line, a 429 with its Retry-After, a gateway answering JSON instead of
    an event stream -- surfaces on the first ``next()``, so pulling exactly one
    event under retry keeps that recovery and gives up only the rarer mid-stream
    drop, which cannot be retried anyway once deltas have been shown.
    """
    def connect():
        iterator = iter(make_stream())
        try:
            return iterator, next(iterator), True
        except StopIteration:
            return iterator, None, False

    iterator, first, has_first = retry_with_backoff(
        connect, attempts=attempts, sleep=sleep)
    if not has_first:
        return iter(())
    return itertools.chain((first,), iterator)


def retry_with_backoff(operation, *, attempts: int = 4, base_delay: float = 1.0,
                       sleep=time.sleep):
    """Retry only what the taxonomy marks transient, honouring Retry-After."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except TransientError as exc:
            last = exc
            if attempt == attempts - 1:
                break
            delay = exc.retry_after if exc.retry_after is not None else base_delay * (2 ** attempt)
            sleep(min(delay, 60.0))
    raise last  # type: ignore[misc]
