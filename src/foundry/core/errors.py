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


class AuthError(FoundryError):
    """Credentials missing, expired, or rejected. Refresh once, then surface."""

    category = "auth"


class FatalError(FoundryError):
    """Not retryable: context exceeded, content refusal, malformed protocol."""

    category = "fatal"


class ProtocolError(FatalError):
    category = "provider_protocol"


class PolicyDenied(FoundryError):
    """Denied before execution. Fed back to the model as a tool result so the
    loop survives and the model can choose a different approach."""

    category = "policy_denied"


class ApprovalDeclined(PolicyDenied):
    category = "approval_declined"


class ToolError(FoundryError):
    """The tool ran (or refused to run) and failed in a way the model should
    see and can act on."""

    category = "tool_failed"


class InvalidToolCall(ToolError):
    category = "invalid_tool_call"


class StaleFileError(ToolError):
    """The file changed since the model last read it; never overwrite blind."""

    category = "stale_file"


class BudgetExceeded(FoundryError):
    category = "budget_exceeded"


class Cancelled(FoundryError):
    category = "cancelled"
