"""An HttpClient that keeps what each request received (D-204).

The checkup asks the endpoint through an adapter, as a session does, and the
adapter turns what went wrong into an error that keeps little of the answer:
an HTML sign-in page becomes "not valid JSON". This client is the adapter's
own, with each response's status, headers and bytes kept as they arrived,
and the time each piece came. The request's headers, where a credential
goes, are never kept.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from foundry.core.httpc import HttpClient


@dataclass(slots=True)
class Exchange:
    """One request and what came back for it."""

    method: str = ""
    target: str = ""
    sent: bytes = b""
    started: float = field(default_factory=time.monotonic)
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Each piece of the body as it was read, with time.monotonic() then.
    pieces: list[tuple[float, bytes]] = field(default_factory=list)

    @property
    def body(self) -> bytes:
        return b"".join(piece for _, piece in self.pieces)

    def took(self, data: bytes) -> None:
        if data:
            self.pieces.append((time.monotonic(), bytes(data)))


class _TapResponse:
    """The parts of http.client.HTTPResponse that HttpClient uses."""

    def __init__(self, raw, exchange: Exchange) -> None:
        self._raw = raw
        self._exchange = exchange

    @property
    def status(self) -> int:
        return self._raw.status

    def getheaders(self):
        return self._raw.getheaders()

    def getheader(self, name: str, default=None):
        return self._raw.getheader(name, default)

    def read(self, amount: int | None = None) -> bytes:
        data = self._raw.read() if amount is None else self._raw.read(amount)
        self._exchange.took(data)
        return data

    def __iter__(self):
        for line in self._raw:
            self._exchange.took(line)
            yield line


class _TapConnection:
    """A connection whose response is kept as it is read."""

    def __init__(self, conn, exchange: Exchange) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_exchange", exchange)

    def request(self, method, target, body=None, headers=None):
        self._exchange.method = method
        self._exchange.target = target
        self._exchange.sent = body or b""
        return self._conn.request(method, target, body=body, headers=headers or {})

    def getresponse(self):
        raw = self._conn.getresponse()
        self._exchange.status = raw.status
        self._exchange.headers = {name.lower(): value for name, value in raw.getheaders()}
        return _TapResponse(raw, self._exchange)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


@dataclass(slots=True)
class TapClient(HttpClient):
    exchanges: list[Exchange] = field(default_factory=list)

    def _connect(self, url):
        # Named, not super(): a slotted dataclass is a new class, and the
        # zero-argument form looks for the old one.
        conn, target, proxy_headers = HttpClient._connect(self, url)
        exchange = Exchange()
        self.exchanges.append(exchange)
        return _TapConnection(conn, exchange), target, proxy_headers

    @property
    def last(self) -> Exchange | None:
        return self.exchanges[-1] if self.exchanges else None
