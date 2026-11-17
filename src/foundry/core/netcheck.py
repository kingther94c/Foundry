"""The network level of ``foundry checkup`` (D-204).

A session's first request takes several steps -- the proxy it chooses, the
name it looks up, the connection, the proxy's tunnel, TLS -- and when one
fails the session says "connection failed", which names none of them. Each
is tried here on its own, with the same choices Foundry's HTTP client
makes, so that what fails says which step it was and what to do.
"""

from __future__ import annotations

import base64
import os
import socket
import ssl
import time
import urllib.parse
import urllib.request

from foundry.core.checkup import FAIL, OK, SKIP, WARN, Case, Found
from foundry.core.errors import ConfigError
from foundry.core.httpc import _build_ssl_context, _is_loopback, _proxy_for

TIMEOUT_S = 10.0
HEAD_LIMIT = 65536

_NAME_FIELDS = {bytes.fromhex("550403"): "CN", bytes.fromhex("55040a"): "O"}
# OpenSSL's verify codes (x509_vfy.h) that name a cause of their own.
_EXPIRED, _HOSTNAME_MISMATCH = 10, 62


def shown_proxy(proxy: str) -> tuple[str, bool]:
    """A proxy's address without its user name and password, and whether it
    had them."""
    parts = urllib.parse.urlsplit(proxy)
    if parts.username is None and parts.password is None:
        return proxy, False
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query,
                                    parts.fragment)), True


def proxy_source(scheme: str) -> str:
    """Where the proxy for ``scheme`` was found: a variable, or the Windows
    settings that urllib reads when no variable names one."""
    if urllib.request.getproxies_environment().get(scheme):
        for name in (f"{scheme.upper()}_PROXY", f"{scheme}_proxy"):
            if os.environ.get(name):
                return f"the variable {name}"
        return "the environment"
    return "the Windows proxy settings"


def pac_url() -> str | None:
    """The proxy script Windows is set to use, which Foundry's client does
    not read: a corporate machine often finds its proxy only through one."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            value, _ = winreg.QueryValueEx(key, "AutoConfigURL")
    except OSError:
        return None
    return value or None


def _read(data: bytes, at: int) -> tuple[int, bytes, int]:
    """One DER element at ``at``: its tag, its contents, where the next one
    starts."""
    tag, length = data[at], data[at + 1]
    at += 2
    if length & 0x80:
        size = length & 0x7F
        length = int.from_bytes(data[at:at + size], "big")
        at += size
    if at + length > len(data):
        raise ValueError("truncated")
    return tag, data[at:at + length], at + length


def issuer_of(der: bytes) -> str:
    """Who issued a certificate, as DER: its issuer's common name and
    organization, or "" when it cannot be read."""
    try:
        _, certificate, _ = _read(der, 0)
        _, tbs, _ = _read(certificate, 0)
        fields, at = [], 0
        while at < len(tbs):
            tag, value, at = _read(tbs, at)
            fields.append((tag, value))
        if fields and fields[0][0] == 0xA0:     # the version, when there is one
            fields = fields[1:]
        issuer = fields[2][1]                   # after the serial and the algorithm
        found: dict[str, str] = {}
        at = 0
        while at < len(issuer):
            _, names, at = _read(issuer, at)
            inner = 0
            while inner < len(names):
                _, pair, inner = _read(names, inner)
                _, oid, rest = _read(pair, 0)
                _, text, _ = _read(pair, rest)
                if oid in _NAME_FIELDS:
                    found.setdefault(_NAME_FIELDS[oid], text.decode("utf-8", "replace"))
    except (IndexError, ValueError):
        return ""
    return ", ".join(f"{name}={found[name]}" for name in ("CN", "O") if name in found)


def _tunnel(sock: socket.socket, host: str, port: int, proxy: str) -> tuple[int, list[str]]:
    """CONNECT through the proxy: its status, and the schemes it asks for
    with Proxy-Authenticate. The proxy's own user name and password, if its
    address has them, go as Basic, as Foundry's client sends them."""
    parts = urllib.parse.urlsplit(proxy)
    lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
    if parts.username:
        token = base64.b64encode(
            f"{urllib.parse.unquote(parts.username)}:"
            f"{urllib.parse.unquote(parts.password or '')}".encode()).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    head = b""
    while b"\r\n\r\n" not in head and len(head) < HEAD_LIMIT:
        piece = sock.recv(4096)
        if not piece:
            break
        head += piece
    text = head.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    status_line, *header_lines = text.split("\r\n")
    words = status_line.split()
    if len(words) < 2 or not words[0].startswith("HTTP/") or not words[1].isdigit():
        return 0, []
    schemes = [line.split(":", 1)[1].strip().split()[0]
               for line in header_lines
               if line.lower().startswith("proxy-authenticate:") and line.split(":", 1)[1].strip()]
    return int(words[1]), schemes


class _Hop:
    """The first hop's address, and a way to open a fresh connection to the
    endpoint through it: a TLS failure is asked about again, unverified, to
    learn who issued the certificate."""

    def __init__(self, host: str, port: int, proxy: str | None, target: tuple[str, int],
                 timeout: float) -> None:
        self.host, self.port, self.proxy, self.target, self.timeout = (
            host, port, proxy, target, timeout)

    def open(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        if self.proxy:
            try:
                status, _ = _tunnel(sock, *self.target, self.proxy)
            except OSError:
                sock.close()
                raise
            if status != 200:
                sock.close()
                raise OSError(f"the proxy answered {status}")
        return sock


def _tls_case(sock: socket.socket, hop: _Hop, host: str, ca_bundle: str | None) -> Case:
    try:
        context = _build_ssl_context(ca_bundle)
    except ConfigError as exc:
        sock.close()
        return Case("N", "tls", FAIL, str(exc), "TLS3",
                    "point FOUNDRY_CA_BUNDLE at a readable PEM file, or unset it and "
                    "SSL_CERT_FILE to use the Windows certificate store")
    trusted_by = (f"the CA bundle {ca_bundle}" if ca_bundle
                  else "the Windows certificate store")
    try:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            issuer = issuer_of(tls.getpeercert(binary_form=True) or b"")
            version = tls.version() or "TLS"
    except ssl.SSLCertVerificationError as exc:
        issuer = _unverified_issuer(hop, host)
        named = f" (issued by {issuer})" if issuer else ""
        reason = exc.verify_message or str(exc)
        if exc.verify_code == _HOSTNAME_MISMATCH:
            return Case("N", "tls", FAIL, f"the certificate{named} is not for {host}: {reason}",
                        "TLS4", "the address in base_url must be the name the certificate "
                        "names; a proxy that inspects TLS may be answering for another host")
        if exc.verify_code == _EXPIRED:
            return Case("N", "tls", FAIL, f"the certificate{named} has expired: {reason}",
                        "TLS5", "the endpoint's or the inspecting proxy's certificate is out "
                        "of date; only its owner can renew it")
        return Case("N", "tls", FAIL,
                    f"the certificate{named} is not trusted by {trusted_by}: {reason}", "TLS1",
                    "a proxy that inspects TLS signs with the company's own CA. Export that "
                    "CA (and any between) as a Base-64 PEM file and set FOUNDRY_CA_BUNDLE to "
                    "it; or have it installed in the Windows store, which Foundry reads "
                    "with nothing set")
    except ssl.SSLError as exc:
        return Case("N", "tls", FAIL, f"TLS failed: {exc}", "TLS2",
                    "the endpoint or something on the way does not speak the TLS Python "
                    "offers; a proxy that blocks the host may also end it this way")
    except OSError as exc:
        return Case("N", "tls", FAIL, f"the connection ended during TLS: {exc}", "TLS0",
                    "a firewall or proxy that blocks the host often ends the connection "
                    "here; ask whether the endpoint is allowed from this machine")
    by = f", certificate issued by {issuer}" if issuer else ""
    return Case("N", "tls", OK, f"{version} with {host}, trusted by {trusted_by}{by}")


def _unverified_issuer(hop: _Hop, host: str) -> str:
    """Who issued the certificate the endpoint presents, asked again without
    checking it -- only to name the CA to trust; nothing is sent."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with context.wrap_socket(hop.open(), server_hostname=host) as tls:
            return issuer_of(tls.getpeercert(binary_form=True) or b"")
    except (OSError, ssl.SSLError):
        return ""


def network_cases(base_url: str, *, ca_bundle: str | None, timeout: float = TIMEOUT_S,
                  said=lambda case: None) -> tuple[list[Case], bool]:
    """The cases of the network level up to the endpoint's door, each told
    to ``said`` as it is found, and whether the door was reached: what
    comes after asks the endpoint itself."""
    parts = urllib.parse.urlsplit(base_url)
    host, scheme = parts.hostname or "", parts.scheme
    port = parts.port or (443 if scheme == "https" else 80)
    cases = Found(said)

    proxy = _proxy_for(base_url)
    if proxy:
        shown, hidden = shown_proxy(proxy)
        said = (f"{shown} for {host}, from {proxy_source(scheme)}"
                + (" (its user name and password not shown)" if hidden else ""))
        if scheme == "https" and urllib.parse.urlsplit(proxy).scheme == "https":
            cases.append(Case("N", "proxy", FAIL, said + "; it is declared https", "PRXS",
                              "set HTTPS_PROXY to the proxy's http:// address: Foundry "
                              "opens its tunnel in the clear and TLS inside it"))
            return cases, False
        cases.append(Case("N", "proxy", OK, said))
    else:
        script = None if _is_loopback(host) else pac_url()
        if script:
            cases.append(Case("N", "proxy", WARN,
                              f"none for {host}, but Windows uses the proxy script {script}, "
                              "which Foundry does not read", "PAC",
                              "if the connection fails, set HTTPS_PROXY to the proxy the "
                              "script chooses (a browser's proxy settings page shows it)"))
        else:
            cases.append(Case("N", "proxy", OK, f"none for {host}"))

    if proxy:
        proxy_parts = urllib.parse.urlsplit(proxy)
        hop_host, hop_port = proxy_parts.hostname or "", proxy_parts.port or 8080
    else:
        hop_host, hop_port = host, port
    rest = ["tcp"] + (["tunnel"] if proxy and scheme == "https" else []) + (
        ["tls"] if scheme == "https" else [])

    def skipped(after: str) -> list[Case]:
        return [Case("N", name, SKIP, f"not tried: {after}") for name in rest]

    try:
        found = socket.getaddrinfo(hop_host, hop_port, type=socket.SOCK_STREAM)
        address = found[0][4][0]
    except (socket.gaierror, UnicodeError, IndexError) as exc:
        cases.append(Case("N", "dns", FAIL, f"{hop_host} has no address here: {exc}", "DNS0",
                          "the name does not resolve on this network: check base_url's "
                          "host, or whether it is reached only through a proxy"))
        cases.extend(skipped("the name did not resolve"))
        return cases, False
    through = f" (the proxy looks up {host})" if proxy else ""
    cases.append(Case("N", "dns", OK, f"{hop_host} is {address}{through}"))

    rest.remove("tcp")
    began = time.monotonic()
    try:
        sock = socket.create_connection((hop_host, hop_port), timeout=timeout)
    except (socket.timeout, TimeoutError):
        cases.append(Case("N", "tcp", FAIL, f"no answer from {hop_host} port {hop_port} in "
                          f"{timeout:g} s", "TCPT",
                          "a firewall that drops the traffic looks like this: ask whether "
                          "the port is open from this machine, or which proxy to use"))
        cases.extend(skipped("no connection"))
        return cases, False
    except ConnectionRefusedError:
        cases.append(Case("N", "tcp", FAIL, f"{hop_host} refused port {hop_port}", "TCP0",
                          "nothing listens there: check the port in base_url or the proxy's "
                          "address"))
        cases.extend(skipped("no connection"))
        return cases, False
    except OSError as exc:
        cases.append(Case("N", "tcp", FAIL, f"cannot connect to {hop_host} port {hop_port}: "
                          f"{exc}", "TCP1", "check the address, and the network this machine "
                          "is on"))
        cases.extend(skipped("no connection"))
        return cases, False
    cases.append(Case("N", "tcp", OK, f"{hop_host} port {hop_port} in "
                      f"{(time.monotonic() - began) * 1000:.0f} ms"))
    hop = _Hop(hop_host, hop_port, proxy, (host, port), timeout)

    try:
        if proxy and scheme == "https":
            rest.remove("tunnel")
            try:
                status, schemes = _tunnel(sock, host, port, proxy)
            except OSError as exc:
                status, schemes = 0, []
                detail = f": {exc}"
            else:
                detail = ""
            if status != 200:
                cases.append(_tunnel_case(status, schemes, host, port, detail))
                cases.extend(skipped("the proxy opened no tunnel"))
                return cases, False
            cases.append(Case("N", "tunnel", OK, f"the proxy opens a tunnel to {host}:{port}"))
        if scheme == "https":
            rest.remove("tls")
            case = _tls_case(sock, hop, host, ca_bundle)
            sock = None
            cases.append(case)
            return cases, case.status == OK
        return cases, True
    finally:
        if sock is not None:
            sock.close()


def _tunnel_case(status: int, schemes: list[str], host: str, port: int, detail: str) -> Case:
    if status == 407:
        asked = ", ".join(schemes) or "nothing it names"
        if any(scheme.lower() == "basic" for scheme in schemes):
            fix = ("put the proxy's user name and password in HTTPS_PROXY "
                   "(http://user:password@proxy:port); doctor and the checkup never show them")
        else:
            fix = ("Foundry can answer only Basic. Ask for a proxy or a port that takes it, "
                   "or bypass the proxy for this host; a local forwarder that speaks "
                   "NTLM to the proxy is the other way")
        return Case("N", "tunnel", FAIL, f"the proxy wants a sign-in (407; it asks for "
                    f"{asked})", "PX407", fix)
    if status == 0:
        return Case("N", "tunnel", FAIL, f"the proxy gave no HTTP answer to CONNECT{detail}",
                    "PX0", "check that HTTPS_PROXY names an HTTP proxy and its port")
    return Case("N", "tunnel", FAIL, f"the proxy refuses a tunnel to {host}:{port} "
                f"(HTTP {status})", f"PX{status}",
                "the proxy blocks this host or port: ask for it to be allowed")
