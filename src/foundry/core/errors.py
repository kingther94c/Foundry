"""Error taxonomy.

Backends map their provider's failures onto these classes; the runtime decides
retry-or-stop from the class alone, so no ``isinstance`` chains against provider
exceptions leak into the loop.
"""

from __future__ import annotations


class FoundryError(Exception):
    """Base class. ``payload`` keeps the provider's original detail for the
    journal without it ever reaching a prompt."""

    category = "internal"

    def __init__(self, message: str, *, payload: str = "") -> None:
        super().__init__(message)
        self.payload = payload


class ConfigError(FoundryError):
    category = "configuration"


class TransientError(FoundryError):
    """Retryable: 429, 5xx, connection resets, idle-timeout mid-stream."""

    category = "transient"

    def __init__(self, message: str, *, payload: str = "", retry_after: float | None = None) -> None:
        super().__init__(message, payload=payload)
        self.retry_after = retry_after


class StreamDropped(TransientError):
    """The response stream broke after it had begun: cut, stalled, or closed
    before the turn was finished (D-099). Nothing of a turn is acted on before
    it finishes, so the runtime sends the request again whole -- codex's
    stream_max_retries -- where a failure before the stream began has already
    had the request's own retries."""


class AuthError(FoundryError):
    """Credentials missing, expired, or rejected. Refresh once, then surface."""

    category = "auth"


class FatalError(FoundryError):
    """Not retryable: context exceeded, content refusal, malformed protocol."""

    category = "fatal"


class ProtocolError(FatalError):
    category = "provider_protocol"


class QuotaExceeded(FatalError):
    """The account's quota is used up (D-085). It answers 429 like a rate
    limit, but no wait clears it: a person has to."""

    category = "quota"


class ToolError(FoundryError):
    """The tool ran (or refused to run) and failed in a way the model should
    see and can act on."""

    category = "tool_failed"


class InvalidToolCall(ToolError):
    category = "invalid_tool_call"


class StaleFileError(ToolError):
    """The file changed since the model last read it; never overwrite blind."""

    category = "stale_file"


class Cancelled(FoundryError):
    category = "cancelled"


class ImageRejected(FoundryError):
    """An image that is not sent: its type, size or pixels (D-045)."""

    category = "image"


class ResumeError(FoundryError):
    """A session that cannot be continued safely; nothing was written to it."""

    category = "resume"


class SessionLocked(ResumeError):
    """Another Foundry process is writing this session's journal."""

    category = "session_locked"


class PromptError(FoundryError):
    """A custom prompt that cannot be sent as typed; nothing was sent (D-071)."""

    category = "prompt"


class UndoError(FoundryError):
    """A session's changes cannot be undone from here; nothing was changed."""

    category = "undo"


def explain(exc: Exception) -> str:
    """The error, plus the provider's own words about it.

    raise_for_status carefully keeps 500 bytes of the response body on
    ``payload`` and nothing ever read it, so every HTTP failure reached the user
    as a bare status line -- "request rejected (HTTP 400)" -- while the body
    sitting unused said exactly which field was wrong.
    """
    detail = (getattr(exc, "payload", "") or "").strip()
    if not detail:
        return str(exc)
    return f"{exc}: {detail[:300]}"
