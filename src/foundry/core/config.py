"""Typed configuration with provenance.

Precedence (requirements section 4.3), highest first:

    CLI flag > FOUNDRY_* environment > project-local > profile > user > built-in

Every effective value records which layer it came from, so "why is this model
selected" has an answer that does not involve guessing. Two rules are load-
bearing rather than tidy: a repository's own config may only *tighten* policy,
never add an ALLOW, and connection settings (endpoint, credentials, headers,
proxy) are read from machine-local layers only -- otherwise a cloned repository
could redirect a token to a host of its choosing.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from foundry.core.errors import ConfigError
from foundry.core.policy.engine import Layer, Mode, Rule, Verdict

USER_DIR_ENV = "FOUNDRY_HOME"
ENV_PREFIX = "FOUNDRY_"

# Settings a repository is never allowed to influence.
_CONNECTION_KEYS = frozenset({"base_url", "api_key", "model", "protocol", "headers",
                              "proxy", "ca_bundle", "credential_source"})


def user_dir() -> Path:
    override = os.environ.get(USER_DIR_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "Foundry"


@dataclass(frozen=True, slots=True)
class Provenance:
    value: Any
    layer: str


@dataclass(slots=True)
class BackendConfig:
    name: str = "personal"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5"
    protocol: str = "openai_compat"
    credential_source: str = "api_key"
    headers: dict[str, str] = field(default_factory=dict)
    request_max_retries: int = 4
    stream_idle_timeout_ms: int = 300_000
    stream: bool = True


@dataclass(slots=True)
class Config:
    backend: BackendConfig = field(default_factory=BackendConfig)
    mode: Mode = Mode.DEFAULT
    max_tool_rounds: int = 40
    max_tool_calls: int = 200
    max_output_bytes: int = 32_000
    command_timeout_s: int = 120
    session_retention_days: int = 30
    rules: list[Rule] = field(default_factory=list)
    provenance: dict[str, Provenance] = field(default_factory=dict)

    def record(self, key: str, value: Any, layer: str) -> None:
        self.provenance[key] = Provenance(value, layer)

    def explain(self, key: str) -> str:
        entry = self.provenance.get(key)
        return f"{key} = {entry.value!r} (from {entry.layer})" if entry else f"{key} is unset"


def _parse_rules(raw: Any, layer: Layer, *, allow_permitted: bool) -> list[Rule]:
    if not isinstance(raw, list):
        raise ConfigError("permissions must be a list of tables")
    rules: list[Rule] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError("each permission entry must be a table")
        try:
            verdict = Verdict(str(item.get("decision", "")).lower())
        except ValueError as exc:
            raise ConfigError(
                f"permission {index}: decision must be allow, ask, or deny"
            ) from exc
        if verdict is Verdict.ALLOW and not allow_permitted:
            # The self-privilege-escalation guard: a cloned repository cannot
            # grant itself permissions, only give up ones it does not need.
            raise ConfigError(
                "a repository config may only add 'ask' or 'deny' rules; "
                "'allow' must come from your user configuration"
            )
        rules.append(Rule(
            tool=str(item.get("tool", "*")),
            pattern=str(item.get("pattern", "*")),
            verdict=verdict,
            layer=layer,
            rule_id=f"{layer.value}.{index}",
            reason=str(item.get("reason", "")),
        ))
    return rules


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def _apply(config: Config, data: dict[str, Any], layer: str, *,
           connection_allowed: bool, allow_rules_permitted: bool) -> None:
    backend = data.get("backend", {})
    if backend and not connection_allowed:
        raise ConfigError(
            f"{layer} config may not set connection settings "
            f"({', '.join(sorted(set(backend) & _CONNECTION_KEYS))})"
        )
    for key, value in backend.items():
        if hasattr(config.backend, key):
            setattr(config.backend, key, value)
            config.record(f"backend.{key}", value, layer)

    # A repository may only tighten. Without this, a cloned repo could set
    # mode = "accept_edits" and grant itself step-4 auto-approval for every
    # patch, or raise the budgets that bound a runaway loop -- both strict
    # loosenings dressed as ordinary settings.
    tighten_only = not allow_rules_permitted
    runtime = data.get("runtime", {})

    for key in ("max_tool_rounds", "max_tool_calls", "max_output_bytes",
                "command_timeout_s", "session_retention_days"):
        if key not in runtime:
            continue
        value = runtime[key]
        # Type-check first, unconditionally. Checking inside the tighten test
        # meant a TOML float slipped past it entirely -- 9999 was refused while
        # 9999.0 was accepted -- and a string reached the code that slices tool
        # output, where it failed every later tool call.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"{key} must be an integer, got {type(value).__name__} ({value!r})"
            )
        if value < 1:
            raise ConfigError(f"{key} must be positive, got {value}")
        if tighten_only and value > getattr(config, key):
            raise ConfigError(
                f"{layer} config may only lower {key} (tried {value}, "
                f"current {getattr(config, key)})"
            )
        setattr(config, key, value)
        config.record(key, value, layer)

    if "mode" in runtime:
        try:
            mode = Mode(str(runtime["mode"]))
        except ValueError as exc:
            raise ConfigError(
                f"mode must be one of {', '.join(m.value for m in Mode)}"
            ) from exc
        if tighten_only and mode not in (Mode.PLAN, Mode.DONT_ASK):
            raise ConfigError(
                f"a repository config may only select a more restrictive mode "
                f"(plan or dont_ask), not {mode.value!r}"
            )
        config.mode = mode
        config.record("mode", runtime["mode"], layer)

    if "permissions" in data:
        layer_enum = Layer.PROJECT if layer.startswith("project") else (
            Layer.MANAGED if layer == "managed" else Layer.USER)
        config.rules.extend(_parse_rules(data["permissions"], layer_enum,
                                         allow_permitted=allow_rules_permitted))


def _apply_env(config: Config) -> None:
    mapping = {
        f"{ENV_PREFIX}MODEL": ("backend.model", lambda v: setattr(config.backend, "model", v)),
        f"{ENV_PREFIX}BASE_URL": ("backend.base_url", lambda v: setattr(config.backend, "base_url", v)),
        f"{ENV_PREFIX}MODE": ("mode", lambda v: setattr(config, "mode", Mode(v))),
    }
    for env_name, (key, setter) in mapping.items():
        value = os.environ.get(env_name)
        if value:
            try:
                setter(value)
            except ValueError as exc:
                raise ConfigError(f"{env_name}: {exc}") from exc
            config.record(key, value, "environment")


def load_config(workspace: Path | None = None, *, overrides: dict[str, Any] | None = None,
                home: Path | None = None) -> Config:
    config = Config()
    root = home or user_dir()

    managed = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Foundry" / "policy.toml"
    if managed.is_file():
        _apply(config, _load_toml(managed), "managed",
               connection_allowed=True, allow_rules_permitted=True)

    _apply(config, _load_toml(root / "config.toml"), "user",
           connection_allowed=True, allow_rules_permitted=True)

    if workspace is not None:
        project = workspace / ".foundry" / "config.toml"
        if project.is_file():
            _apply(config, _load_toml(project), "project",
                   connection_allowed=False, allow_rules_permitted=False)

    _apply_env(config)

    for key, value in (overrides or {}).items():
        if hasattr(config, key):
            setattr(config, key, value)
        elif hasattr(config.backend, key):
            setattr(config.backend, key, value)
        config.record(key, value, "cli")

    return config
