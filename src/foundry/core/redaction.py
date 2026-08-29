"""The single choke point for credential removal.

Three sinks share this one implementation: the session journal (including
artifacts), events leaving the runtime, and anything assembled into model
context. What it guarantees is narrow and therefore verifiable: byte-exact
removal of credentials Foundry itself holds. Pattern scanning is best-effort and
labelled as such -- promising general secret detection would be a lie.

Registered values are matched in UTF-8 *and* UTF-16LE so a redaction pass over
raw command output catches Windows tools that emit wide characters.
"""

from __future__ import annotations

import re
import threading

PLACEHOLDER = "[redacted]"
PLACEHOLDER_BYTES = b"[redacted]"

# Deliberately conservative: only shapes that are unambiguous credentials.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"),  # JWT
)

_MIN_REGISTERED_LENGTH = 8


class Redactor:
    """Holds the values to scrub. One instance per process, shared by all sinks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: list[str] = []

    def register(self, value: str | None) -> None:
        """Add a credential. Short values are ignored: scrubbing a 4-character
        string would shred unrelated output."""
        if not value or len(value) < _MIN_REGISTERED_LENGTH:
            return
        with self._lock:
            if value not in self._values:
                self._values.append(value)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    @property
    def registered_count(self) -> int:
        with self._lock:
            return len(self._values)

    def scrub(self, text: str) -> str:
        """Exact-match removal of registered values, then best-effort patterns."""
        if not text:
            return text
        with self._lock:
            values = tuple(self._values)
        for value in values:
            if value in text:
                text = text.replace(value, PLACEHOLDER)
        for pattern in _PATTERNS:
            text = pattern.sub(PLACEHOLDER, text)
        return text

    def scrub_bytes(self, data: bytes) -> bytes:
        """Runs before base64 encoding, on the raw bytes as captured.

        Command output is journaled as raw bytes so a bad decode can be redone
        later; that means redaction has to happen at the byte level or the
        guarantee is void for exactly the outputs most likely to leak.
        """
        if not data:
            return data
        with self._lock:
            values = tuple(self._values)
        for value in values:
            for encoding in ("utf-8", "utf-16-le"):
                try:
                    needle = value.encode(encoding)
                except UnicodeEncodeError:
                    continue
                if needle and needle in data:
                    data = data.replace(needle, PLACEHOLDER_BYTES)

        # The same best-effort pattern pass `scrub` applies. Without it the
        # journal's command record -- the one sink holding full, untruncated
        # output on disk -- kept credential-shaped strings that the model's own
        # ephemeral context had already had removed.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        for pattern in _PATTERNS:
            text = pattern.sub(PLACEHOLDER, text)
        return text.encode("utf-8")

    def scrub_obj(self, obj):
        """Recursively scrub strings inside a JSON-shaped structure."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {k: self.scrub_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.scrub_obj(v) for v in obj]
        return obj


_default = Redactor()


def default_redactor() -> Redactor:
    return _default
