"""The only module that talks to the Claude API.

Every agent role is an object with
``json(system, prompt, schema, *, prefix=None) -> dict``. ``ClaudeJSON``
implements it on top of an injected Anthropic client.
"""

from __future__ import annotations

import json

DEFAULT_MODEL = "claude-opus-5-5"
BETAS = ["server-side-fallback-2026-07-01"]
USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class LLMError(RuntimeError):
    """The model refused, ran out of tokens, failed, or produced unusable output."""


def anthropic_client():
    """Return a real ``anthropic.Anthropic()`` client (reads ANTHROPIC_API_KEY)."""
    import anthropic

    return anthropic.Anthropic()


def _is_api_error(exc: BaseException) -> bool:
    try:
        import anthropic
    except ImportError:  # pragma: no cover - anthropic is a dependency
        return False
    return isinstance(exc, anthropic.APIError)


def _get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class ClaudeJSON:
    """Structured-output JSON calls to Claude through an injected client."""

    def __init__(self, client, model: str = DEFAULT_MODEL, effort: str = "high",
                 max_tokens: int = 16000):
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.usage = {key: 0 for key in USAGE_KEYS}

    def json(self, system: str, prompt: str, schema: dict, *, prefix: str | None = None) -> dict:
        if prefix:
            blocks = [
                {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": prompt},
            ]
        else:
            blocks = [{"type": "text", "text": prompt}]
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                betas=list(BETAS),
                fallbacks="default",
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                system=system,
                messages=[{"role": "user", "content": blocks}],
            )
        except Exception as exc:
            if _is_api_error(exc):
                raise LLMError(f"Claude API request failed: {exc}") from exc
            raise
        self._add_usage(_get(response, "usage"))

        stop_reason = _get(response, "stop_reason")
        if stop_reason == "refusal":
            category = _get(_get(response, "stop_details"), "category")
            if category:
                raise LLMError(f"model refused the request (category: {category})")
            raise LLMError("model refused the request")
        if stop_reason == "max_tokens":
            raise LLMError(f"model hit max_tokens ({self.max_tokens}) before finishing")

        text = None
        for block in _get(response, "content") or []:
            if _get(block, "type") == "text":
                text = _get(block, "text")
                break
        if text is None:
            raise LLMError("response has no text block")
        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise LLMError(f"response text is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("response JSON is not an object")
        return data

    def _add_usage(self, usage) -> None:
        if usage is None:
            return
        for key in USAGE_KEYS:
            value = _get(usage, key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.usage[key] += value
