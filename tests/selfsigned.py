"""A self-signed certificate for a TLS server on loopback, made at test time
with the standard library alone: no key is kept in the repository, and
nothing need be installed.

    cert_pem, key_pem = certificate(names=["localhost"], ips=["127.0.0.1"])

An RSA key of 2048 bits (what Python's default TLS settings accept), and a
version 3 certificate signed with it, naming itself as issuer, marked as a
CA, with the names given as subject alternative names.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import ipaddress
import math
import random
import ssl

_RANDOM = random.SystemRandom()
_SMALL_PRIMES = [n for n in range(3, 2000, 2) if all(n % d for d in range(3, int(n ** 0.5) + 1, 2))]


def _probably_prime(n: int, rounds: int = 32) -> bool:
    for small in _SMALL_PRIMES:
        if n % small == 0:
            return n == small
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        x = pow(_RANDOM.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int) -> int:
    while True:
        candidate = _RANDOM.getrandbits(bits) | (3 << (bits - 2)) | 1
        if _probably_prime(candidate):
            return candidate


def _rsa(bits: int = 2048, e: int = 65537) -> tuple[int, int, int, int, int]:
    while True:
        p, q = _prime(bits // 2), _prime(bits // 2)
        phi = (p - 1) * (q - 1)
        if p != q and math.gcd(e, phi) == 1 and (p * q).bit_length() == bits:
            return p * q, e, pow(e, -1, phi), p, q


def _length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _length(len(body)) + body


def _int(value: int) -> bytes:
    return _tlv(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def _seq(*parts: bytes) -> bytes:
    return _tlv(0x30, b"".join(parts))


def _oid(dotted: str) -> bytes:
    numbers = [int(part) for part in dotted.split(".")]
    body = bytes([40 * numbers[0] + numbers[1]])
    for number in numbers[2:]:
        chunk = [number & 0x7F]
        number >>= 7
        while number:
            chunk.append(0x80 | (number & 0x7F))
            number >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


def _name(common_name: str, organization: str) -> bytes:
    def part(oid: str, text: str) -> bytes:
        return _tlv(0x31, _seq(_oid(oid), _tlv(0x0C, text.encode("utf-8"))))

    return _seq(part("2.5.4.3", common_name), part("2.5.4.10", organization))


def _time(moment: datetime.datetime) -> bytes:
    return _tlv(0x17, moment.strftime("%y%m%d%H%M%SZ").encode("ascii"))


_NULL = _tlv(0x05, b"")
_SHA256_RSA = _seq(_oid("1.2.840.113549.1.1.11"), _NULL)


def certificate(*, names=("localhost",), ips=("127.0.0.1",),
                common_name: str = "Foundry checkup test CA",
                organization: str = "Foundry tests", days=(-1, 2)) -> tuple[str, str]:
    """A certificate and its key, both as PEM text, valid from and until
    the given numbers of days from now."""
    n, e, d, p, q = _rsa()
    spki = _seq(_seq(_oid("1.2.840.113549.1.1.1"), _NULL),
                _tlv(0x03, b"\x00" + _seq(_int(n), _int(e))))
    alternatives = (b"".join(_tlv(0x82, name.encode("ascii")) for name in names)
                    + b"".join(_tlv(0x87, ipaddress.ip_address(ip).packed) for ip in ips))
    extensions = _tlv(0xA3, _seq(
        _seq(_oid("2.5.29.19"), _tlv(0x01, b"\xff"), _tlv(0x04, _seq(_tlv(0x01, b"\xff")))),
        _seq(_oid("2.5.29.17"), _tlv(0x04, _tlv(0x30, alternatives)))))
    now = datetime.datetime.now(datetime.timezone.utc)
    issuer = _name(common_name, organization)
    tbs = _seq(_tlv(0xA0, _int(2)), _int(_RANDOM.getrandbits(63) + 1), _SHA256_RSA, issuer,
               _seq(_time(now + datetime.timedelta(days=days[0])),
                    _time(now + datetime.timedelta(days=days[1]))),
               issuer, spki, extensions)
    digest = _seq(_seq(_oid("2.16.840.1.101.3.4.2.1"), _NULL),
                  _tlv(0x04, hashlib.sha256(tbs).digest()))
    size = (n.bit_length() + 7) // 8
    padded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    signature = pow(int.from_bytes(padded, "big"), d, n).to_bytes(size, "big")
    der = _seq(tbs, _SHA256_RSA, _tlv(0x03, b"\x00" + signature))
    key = _seq(_int(0), _int(n), _int(e), _int(d), _int(p), _int(q), _int(d % (p - 1)),
               _int(d % (q - 1)), _int(pow(q, -1, p)))
    return ssl.DER_cert_to_PEM_cert(der), _pem("RSA " + "PRIVATE KEY", key)


def _pem(label: str, der: bytes) -> str:
    text = base64.b64encode(der).decode("ascii")
    lines = [text[at:at + 64] for at in range(0, len(text), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"
