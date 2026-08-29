"""Credential acquisition and storage.

A credential travels as a :class:`SecretHandle`, not a string. Only the HTTP
layer resolves it, so no amount of refactoring in the context assembler or a
tool can put a token into a prompt by accident. Whatever is resolved is also
registered with the redactor, which is what makes the canary leak suite
meaningful rather than aspirational.

Storage is a DPAPI-encrypted file under the user profile. On failure to decrypt
-- a forced local password reset invalidates the master key -- the user is
logged out and asked to sign in again; it is never reported as corruption.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from foundry.core.errors import AuthError
from foundry.core.redaction import Redactor, default_redactor
from foundry.core.winapi import (
    CredentialProtectionUnavailable,
    dpapi_protect,
    dpapi_unprotect,
)


@dataclass(frozen=True, slots=True)
class SecretHandle:
    """An opaque reference. ``__str__`` and ``__repr__`` never show the value,
    so a stray log line or exception cannot leak it."""

    _value: str
    label: str = "credential"

    def reveal(self) -> str:
        """Called only by the HTTP header builder."""
        return self._value

    def __str__(self) -> str:
        return f"<{self.label} redacted>"

    __repr__ = __str__


class CredentialSource(Protocol):
    def acquire(self) -> SecretHandle: ...
    def invalidate(self) -> None: ...
    def logout(self) -> None: ...


@dataclass(slots=True)
class CredentialVault:
    """DPAPI-encrypted credential file, written atomically."""

    path: Path
    redactor: Redactor = field(default_factory=default_redactor)

    def save(self, data: dict[str, str]) -> None:
        payload = json.dumps({**data, "saved_at": datetime.now(timezone.utc).isoformat()})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            blob = dpapi_protect(payload.encode("utf-8"))
        except CredentialProtectionUnavailable:
            # Non-Windows development: store plainly but say so, rather than
            # implying protection that is not there.
            blob = b"PLAIN:" + payload.encode("utf-8")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self.path)

    def load(self) -> dict[str, str] | None:
        if not self.path.exists():
            return None
        blob = self.path.read_bytes()
        try:
            if blob.startswith(b"PLAIN:"):
                payload = blob[6:]
            else:
                payload = dpapi_unprotect(blob)
        except CredentialProtectionUnavailable:
            return None  # treated as logged out, never as corruption
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        for key in ("api_key", "token"):
            if data.get(key):
                self.redactor.register(data[key])
        return data

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


@dataclass(slots=True)
class ApiKeySource:
    """Personal path: an OpenAI platform key from the environment or the vault.

    Foundry never types a key on the user's behalf and never prompts the model
    for one; it is supplied by the user through the environment or ``login``.
    """

    vault: CredentialVault
    env_var: str = "OPENAI_API_KEY"
    redactor: Redactor = field(default_factory=default_redactor)

    def acquire(self) -> SecretHandle:
        from_env = os.environ.get(self.env_var)
        if from_env:
            self.redactor.register(from_env)
            return SecretHandle(from_env, label="api key")

        stored = self.vault.load()
        if stored and stored.get("api_key"):
            return SecretHandle(stored["api_key"], label="api key")

        raise AuthError(
            f"no credentials. Set {self.env_var} or run 'foundry login'."
        )

    def store(self, api_key: str) -> None:
        self.redactor.register(api_key)
        self.vault.save({"api_key": api_key})

    def invalidate(self) -> None:
        pass  # a static key has nothing to refresh

    def logout(self) -> None:
        self.vault.clear()


@dataclass(slots=True)
class StaticTokenSource:
    """Corporate path placeholder: a token obtained elsewhere, stored as-is.

    The real acquisition mechanism (an HTTP exchange, an internal executable, or
    a browser SSO flow) is an open discovery item; it plugs in here without the
    runtime noticing, which is the point of the interface.
    """

    vault: CredentialVault
    env_var: str = "FOUNDRY_GATEWAY_TOKEN"
    redactor: Redactor = field(default_factory=default_redactor)

    def acquire(self) -> SecretHandle:
        from_env = os.environ.get(self.env_var)
        if from_env:
            self.redactor.register(from_env)
            return SecretHandle(from_env, label="gateway token")
        stored = self.vault.load()
        if stored and stored.get("token"):
            return SecretHandle(stored["token"], label="gateway token")
        raise AuthError(f"no gateway token. Set {self.env_var} or run 'foundry login'.")

    def store(self, token: str) -> None:
        self.redactor.register(token)
        self.vault.save({"token": token})

    def invalidate(self) -> None:
        self.vault.clear()

    def logout(self) -> None:
        self.vault.clear()
