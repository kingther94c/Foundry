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

import dataclasses
import os
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from foundry.core.errors import ConfigError
from foundry.core.httpc import _is_loopback
from foundry.core.winapi import looks_like_credential
from foundry.core.policy.engine import Layer, Mode, Rule, Verdict

USER_DIR_ENV = "FOUNDRY_HOME"
ENV_PREFIX = "FOUNDRY_"
PROFILE_ENV = "FOUNDRY_PROFILE"

# Settings a repository is never allowed to influence.
_CONNECTION_KEYS = frozenset({"base_url", "api_key", "model", "protocol", "headers",
                              "proxy", "ca_bundle", "credential_source", "env_key",
                              "env_http_headers", "query_params"})
# A header's name: an HTTP token.
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")

# Per-protocol defaults for settings no layer set. The base URL is the one that
# matters: an Anthropic key sent to the OpenAI default host is a credential
# handed to the wrong provider.
ANTHROPIC_PROTOCOL = "anthropic_messages"
_PROTOCOL_DEFAULTS: dict[str, dict[str, str]] = {
    ANTHROPIC_PROTOCOL: {"base_url": "https://api.anthropic.com/v1",
                         "model": "claude-opus-5-5"},
}

# How hard the model reasons (D-061), as each protocol spells it:
# output_config.effort for Claude, reasoning.effort and reasoning_effort for
# the OpenAI shapes.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
OPENAI_EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")
ANY_EFFORT = tuple(dict.fromkeys(OPENAI_EFFORT_LEVELS + EFFORT_LEVELS))
FALLBACK_VALUES = ("default", "off")
REASONING_SUMMARIES = ("auto", "concise", "detailed", "none")
MIN_CONTEXT_TOKENS = 1000


def _validate_backend_setting(key: str, value: Any) -> None:
    """Range and type checks for backend settings that have them."""
    if key == "effort":
        if value is not None and value not in ANY_EFFORT:
            raise ConfigError(
                f"backend.effort must be one of {', '.join(ANY_EFFORT)}, got {value!r}"
            )
    elif key == "images":
        if not isinstance(value, bool):
            raise ConfigError(
                f"backend.images must be true or false, got {type(value).__name__} ({value!r})"
            )
    elif key == "reasoning_summary":
        if value is not None and value not in REASONING_SUMMARIES:
            raise ConfigError(
                "backend.reasoning_summary must be one of "
                f"{', '.join(REASONING_SUMMARIES)}, got {value!r}"
            )
    elif key == "env_key":
        if value is not None and (not isinstance(value, str) or not _ENV_NAME.match(value)):
            # Not echoed: a key pasted here by mistake would be printed.
            raise ConfigError("backend.env_key must be the name of an environment variable, "
                              "not the key itself")
    elif key == "env_http_headers":
        if not isinstance(value, dict) or not all(
                isinstance(k, str) and _HEADER_NAME.match(k)
                and isinstance(v, str) and _ENV_NAME.match(v) for k, v in value.items()):
            raise ConfigError("backend.env_http_headers must map header names to the names "
                              "of environment variables, as { \"X-Org\" = \"CORP_ORG_ID\" }")
    elif key == "query_params":
        if not isinstance(value, dict) or not all(
                isinstance(k, str) and k and isinstance(v, str) for k, v in value.items()):
            raise ConfigError("backend.query_params must be a table of strings, as "
                              "{ api-version = \"2025-04-01-preview\" }")
    elif key == "fallbacks":
        if value not in FALLBACK_VALUES:
            raise ConfigError(
                f"backend.fallbacks must be 'default' or 'off', got {value!r}"
            )
    elif key == "max_context_tokens":
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"backend.max_context_tokens must be an integer, got "
                f"{type(value).__name__} ({value!r})"
            )
        if value < MIN_CONTEXT_TOKENS:
            raise ConfigError(
                f"backend.max_context_tokens must be at least {MIN_CONTEXT_TOKENS}, got {value}"
            )


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
    # As codex's model_providers (D-084): the variable this backend's key is
    # read from, and only from (None: the protocol's own, or the store);
    # headers whose values are read from variables; and parameters added to
    # every request's URL.
    env_key: str | None = None
    env_http_headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    stream: bool = True
    # anthropic_messages only: output_config.effort (None leaves it to the API),
    # server-side fallbacks ("off" disables them), and the context window the
    # runtime plans masking against (None keeps the backend's own figure).
    effort: str | None = None
    fallbacks: str = "default"
    max_context_tokens: int | None = None
    # The reasoning summary asked for (D-044). Unset: anthropic_messages asks
    # for its summary, responses asks for nothing (a model that does not reason
    # rejects the field). "none" asks for nothing anywhere.
    reasoning_summary: str | None = None
    # Whether the model takes images (D-045): view_image, -i and /image, and an
    # MCP tool's images. False for a text-only model, which refuses a request
    # that carries one.
    images: bool = True


_SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
_MCP_KEYS = frozenset({"command", "args", "env", "env_vars", "startup_timeout_s",
                       "tool_timeout_s", "read_only", "enabled", "enabled_tools",
                       "disabled_tools", "url", "bearer_token_env_var"})
# What only a server Foundry starts can have (D-081).
_PROGRAM_KEYS = ("command", "args", "env", "env_vars")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """One MCP server the user's own configuration starts (D-043)."""

    name: str
    # The program Foundry starts and speaks to over stdio -- or, with url, none.
    command: str = ""
    args: tuple[str, ...] = ()
    # Plain values for the server's environment. Secrets belong in env_vars:
    # the names of variables passed through from Foundry's own environment.
    env: tuple[tuple[str, str], ...] = ()
    env_vars: tuple[str, ...] = ()
    startup_timeout_s: float = 20.0
    tool_timeout_s: float = 120.0
    # The user vouches that these tools change nothing: they are then offered
    # in plan mode and /review too -- still asking first unless a rule allows.
    read_only: bool = False
    enabled: bool = True
    # Which of the server's tools are offered (D-080), by the server's own
    # names, as codex's enabled_tools and disabled_tools: only these (None:
    # all of them), and never those.
    enabled_tools: tuple[str, ...] | None = None
    disabled_tools: tuple[str, ...] = ()
    # A server reached over streamable HTTP (D-081), as codex's url: its
    # address, and the environment variable holding a bearer token for it.
    # The token itself is never in the config.
    url: str = ""
    bearer_token_env_var: str = ""

    def offers(self, remote: str) -> bool:
        """Whether the user's configuration lets this tool be offered."""
        if self.enabled_tools is not None and remote not in self.enabled_tools:
            return False
        return remote not in self.disabled_tools

    def shown_as(self) -> str:
        """Where the server is, as it may be shown: its program, not its
        arguments; or its address's scheme and host, not its path or query.
        Either of those may carry a secret."""
        if not self.url:
            return Path(self.command).name
        parts = urllib.parse.urlsplit(self.url)
        host = parts.hostname or "?"
        if ":" in host:
            host = f"[{host}]"
        return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


INHERIT = ("core", "all", "none")
_SHELL_KEYS = frozenset({"inherit", "include", "exclude", "include_only", "set"})


@dataclass(frozen=True, slots=True)
class ShellEnvironmentPolicy:
    """Which variables a command gets (D-083), as codex's
    [shell_environment_policy]: run_command's and the user's !COMMAND.

    ``inherit`` is where it starts: ``core``, the variables a build needs
    (the default, D-049's list); ``all``; or ``none``. ``include`` adds
    variables by name or pattern, ``exclude`` takes them out, ``set`` gives
    plain values, and ``include_only``, if given, keeps only what it names.
    A credential-shaped name is never passed by inheritance or a pattern:
    only when ``include`` names it exactly, and its value is then scrubbed
    from whatever the command prints.
    """

    inherit: str = "core"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    include_only: tuple[str, ...] | None = None
    set: tuple[tuple[str, str], ...] = ()


def _names(value: Any, what: str) -> tuple[str, ...]:
    names = _strings(value, what)
    if not all(name.strip() and "=" not in name and "\0" not in name for name in names):
        raise ConfigError(f"{what} must name variables, or patterns of them such as UV_*")
    return names


def _parse_shell_environment(raw: Any, current: ShellEnvironmentPolicy,
                             where: str = "shell_environment_policy") -> ShellEnvironmentPolicy:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")
    if "ignore_default_excludes" in raw:
        # codex's way to let a secret through every command; here a secret is
        # passed only by its own name.
        raise ConfigError(f"{where}.ignore_default_excludes is not offered: a variable "
                          "whose name looks like a credential is passed only when include "
                          "names it exactly")
    unknown = sorted(set(raw) - _SHELL_KEYS)
    if unknown:
        raise ConfigError(f"{where}: no setting {unknown[0]!r} "
                          f"(known: {', '.join(sorted(_SHELL_KEYS))})")
    changes: dict[str, Any] = {}
    if "inherit" in raw:
        if raw["inherit"] not in INHERIT:
            raise ConfigError(f"{where}.inherit must be one of {', '.join(INHERIT)}")
        changes["inherit"] = raw["inherit"]
    for key in ("include", "exclude", "include_only"):
        if key in raw:
            changes[key] = _names(raw[key], f"{where}.{key}")
    if "set" in raw:
        values = raw["set"]
        if not isinstance(values, dict) or not all(
                isinstance(k, str) and k.strip() and "=" not in k and isinstance(v, str)
                for k, v in values.items()):
            raise ConfigError(f"{where}.set must be a table of strings")
        for name in values:
            if looks_like_credential(name):
                raise ConfigError(f"{name} looks like a secret, and {where}.set writes its "
                                  "value into config.toml; keep it in your environment and "
                                  f"name it in {where}.include")
        changes["set"] = tuple(sorted(values.items()))
    return dataclasses.replace(current, **changes)


def _strings(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{what} must be a list of strings")
    return tuple(value)


def _seconds(value: Any, what: str, ceiling: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= ceiling:
        raise ConfigError(f"{what} must be a number of seconds above 0 and at most {ceiling:g}")
    return float(value)


def _mcp_url(value: Any, where: str, *, token: bool) -> str:
    """The address of a server reached over HTTP (D-081)."""
    url = value.strip() if isinstance(value, str) else ""
    parts = urllib.parse.urlsplit(url)
    try:
        parts.port
    except ValueError:
        url = ""
    if parts.scheme not in ("http", "https") or not parts.hostname or not url:
        raise ConfigError(f"{where}.url must be an http:// or https:// address")
    if parts.username or parts.password:
        raise ConfigError(f"{where}.url may not carry a user name or password; put a token "
                          f"in an environment variable and name it in "
                          f"{where}.bearer_token_env_var")
    if token and parts.scheme == "http" and not _is_loopback(parts.hostname):
        # Readable by every hop between here and there.
        raise ConfigError(f"{where}: a bearer token is sent only over https, or to this "
                          f"machine, and {where}.url is http://{parts.hostname}")
    return url


def _parse_mcp_servers(raw: Any) -> dict[str, McpServerConfig]:
    if not isinstance(raw, dict):
        raise ConfigError("mcp_servers must be tables, as [mcp_servers.<name>]")
    servers: dict[str, McpServerConfig] = {}
    for name, entry in raw.items():
        where = f"mcp_servers.{name}"
        if not _SERVER_NAME.match(name):
            raise ConfigError(f"{where}: a server name is letters, digits, _ and -, "
                              "at most 32 of them")
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        unknown = sorted(set(entry) - _MCP_KEYS)
        if unknown:
            raise ConfigError(f"{where}: no setting {unknown[0]!r} "
                              f"(known: {', '.join(sorted(_MCP_KEYS))})")
        token = entry.get("bearer_token_env_var", "")
        if not isinstance(token, str) or (token and not _ENV_NAME.match(token)):
            # Not echoed: a token pasted here by mistake would be printed.
            raise ConfigError(f"{where}.bearer_token_env_var must be the name of an "
                              "environment variable, not the token itself")
        url = ""
        command = entry.get("command")
        if "url" in entry:
            both = [key for key in _PROGRAM_KEYS if key in entry]
            if both:
                raise ConfigError(f"{where}: url and {both[0]} do not go together -- a "
                                  "server is a program Foundry starts, or an address it "
                                  "reaches")
            url = _mcp_url(entry["url"], where, token=bool(token))
            command = ""
        elif token:
            raise ConfigError(f"{where}.bearer_token_env_var is for a server reached by url")
        elif not isinstance(command, str) or not command.strip():
            raise ConfigError(f"{where}.command must name the program to run, or "
                              f"{where}.url the address of a server")
        env = entry.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                for k, v in env.items()):
            raise ConfigError(f"{where}.env must be a table of strings")
        flags = {}
        for flag in ("read_only", "enabled"):
            if flag in entry:
                if not isinstance(entry[flag], bool):
                    raise ConfigError(f"{where}.{flag} must be true or false")
                flags[flag] = entry[flag]
        servers[name] = McpServerConfig(
            name=name, command=command.strip(), url=url, bearer_token_env_var=token,
            args=_strings(entry.get("args", []), f"{where}.args"),
            env=tuple(sorted(env.items())),
            env_vars=_strings(entry.get("env_vars", []), f"{where}.env_vars"),
            startup_timeout_s=_seconds(entry.get("startup_timeout_s", 20),
                                       f"{where}.startup_timeout_s", 300),
            tool_timeout_s=_seconds(entry.get("tool_timeout_s", 120),
                                    f"{where}.tool_timeout_s", 3600),
            enabled_tools=(_strings(entry["enabled_tools"], f"{where}.enabled_tools")
                           if "enabled_tools" in entry else None),
            disabled_tools=_strings(entry.get("disabled_tools", []),
                                    f"{where}.disabled_tools"),
            **flags,
        )
    return servers


@dataclass(slots=True)
class Config:
    backend: BackendConfig = field(default_factory=BackendConfig)
    mode: Mode = Mode.DEFAULT
    max_tool_rounds: int = 40
    max_tool_calls: int = 200
    max_output_bytes: int = 32_000
    # A ceiling, not a default: run_command keeps its own lower default and
    # clamps every request to this. Shipping it at the tool's own maximum leaves
    # stock behaviour unchanged while giving an operator a knob that bites.
    command_timeout_s: int = 600
    # Reserved (OQ-19): nothing prunes sessions yet. Deleting a developer's
    # journals on a default they never chose is not a change to make quietly.
    session_retention_days: int = 30
    # Summary compaction before the context fills (D-040); off, a long task
    # ends partial(context window exhausted) as before.
    auto_compact: bool = True
    # Whether the model's reasoning is shown (D-044); the journal keeps it.
    show_reasoning: bool = True
    # From the user's own files only: each one is a program Foundry starts.
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    # A program run when a turn ends (D-050), from the user's own files only:
    # the program and its arguments; the turn's JSON is added as the last.
    notify: tuple[str, ...] = ()
    # Which variables commands get (D-083), from the user's own files only.
    shell_environment: ShellEnvironmentPolicy = field(default_factory=ShellEnvironmentPolicy)
    rules: list[Rule] = field(default_factory=list)
    provenance: dict[str, Provenance] = field(default_factory=dict)

    def record(self, key: str, value: Any, layer: str) -> None:
        self.provenance[key] = Provenance(value, layer)

    def explain(self, key: str) -> str:
        entry = self.provenance.get(key)
        return f"{key} = {entry.value!r} (from {entry.layer})" if entry else f"{key} is unset"


def _parse_notify(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(a, str) and a.strip() and "\0" not in a
                                            for a in raw):
        raise ConfigError('notify must be a list of strings, the program and its arguments, '
                          'as notify = ["notify-send", "Foundry"] ([] for none)')
    return tuple(raw)


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
    # runtime.* was type-checked while backend.* was applied blind, so
    # `request_max_retries = 0` -- the natural way to ask for no retries --
    # reached range(0), left the retry loop with nothing to re-raise, and died
    # at `raise None`: a TypeError outside the taxonomy, so the CLI printed a
    # traceback rather than making the request once.
    _BACKEND_INT_FLOORS = {"request_max_retries": 1, "stream_idle_timeout_ms": 1000}
    for key, value in backend.items():
        if not hasattr(config.backend, key):
            continue
        floor = _BACKEND_INT_FLOORS.get(key)
        if floor is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(
                    f"backend.{key} must be an integer, got {type(value).__name__} ({value!r})"
                )
            if value < floor:
                raise ConfigError(
                    f"backend.{key} must be at least {floor}, got {value}"
                    + (" (one attempt is the minimum; there is no way to make zero requests)"
                       if key == "request_max_retries" else "")
                )
        _validate_backend_setting(key, value)
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
        floor = 1024 if key == "max_output_bytes" else 1
        if value < floor:
            raise ConfigError(
                f"{key} must be at least {floor}, got {value}"
                + (" (a smaller cap leaves no room for a useful tool result)"
                   if key == "max_output_bytes" else "")
            )
        if tighten_only and value > getattr(config, key):
            raise ConfigError(
                f"{layer} config may only lower {key} (tried {value}, "
                f"current {getattr(config, key)})"
            )
        setattr(config, key, value)
        config.record(key, value, layer)

    for key in ("auto_compact", "show_reasoning"):
        if key not in runtime:
            continue
        value = runtime[key]
        if not isinstance(value, bool):
            raise ConfigError(
                f"{key} must be true or false, got {type(value).__name__} ({value!r})"
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

    if "mcp_servers" in data:
        # Each entry is a program to run. A repository naming one would be a
        # clone running code of its choosing the moment a session starts.
        if not connection_allowed:
            raise ConfigError(f"{layer} config may not start MCP servers; configure them "
                              "in your own config.toml")
        config.mcp_servers.update(_parse_mcp_servers(data["mcp_servers"]))
        config.record("mcp_servers", sorted(config.mcp_servers), layer)

    if "notify" in data:
        # A program to run, as an MCP server is, and told what the session
        # did: a repository naming one would run code of its choosing.
        if not connection_allowed:
            raise ConfigError(f"{layer} config may not set notify; set it in your own "
                              "config.toml")
        config.notify = _parse_notify(data["notify"])
        config.record("notify", list(config.notify), layer)

    if "shell_environment_policy" in data:
        # What reaches every command the model runs: a repository widening it
        # would hand its own test code the user's environment.
        if not connection_allowed:
            raise ConfigError(f"{layer} config may not set shell_environment_policy; set it "
                              "in your own config.toml")
        config.shell_environment = _parse_shell_environment(
            data["shell_environment_policy"], config.shell_environment)
        config.record("shell_environment_policy", config.shell_environment.inherit, layer)

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


# The runtime table's settings, for ``-c`` to check a name against: an
# unknown one is a typo, not a setting nobody reads.
_RUNTIME_KEYS = ("max_tool_rounds", "max_tool_calls", "max_output_bytes", "command_timeout_s",
                 "session_retention_days", "auto_compact", "show_reasoning", "mode")
# Secrets may come only from the environment or the credential store, never
# from a command line other processes can read (requirements section 4.3).
_SECRET_KEYS = frozenset({"api_key", "headers"})


def parse_settings(items: list[str]) -> dict[str, dict[str, Any]]:
    """``-c key=value`` settings as a config layer: ``backend.model=x``,
    ``runtime.max_tool_rounds=60``, or a bare key from either table. The value
    is read as TOML -- ``60``, ``true``, ``"a b"`` -- and as a plain string when
    it is not, so ``-c model=gpt-5`` needs no quotes (D-042)."""
    layer: dict[str, dict[str, Any]] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        name = name.strip()
        if not sep or not name:
            raise ConfigError(f"-c expects key=value, got {item!r}")
        section, dot, key = name.rpartition(".")
        if not dot:
            section = "backend" if key in BackendConfig.__dataclass_fields__ else "runtime"
        if key in _SECRET_KEYS:
            raise ConfigError(f"-c may not set {name}: secrets come only from the environment "
                              "or the credential store (foundry login)")
        known = (section == "backend" and key in BackendConfig.__dataclass_fields__
                 and key != "name") or (section == "runtime" and key in _RUNTIME_KEYS)
        if not known:
            raise ConfigError(f"-c: no setting {name!r}")
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw.strip()
        layer.setdefault(section, {})[key] = value
    return layer


def _apply_profile(config: Config, data: dict[str, Any], name: str, path: Path,
                   chosen_by: str) -> None:
    """A named set of settings from the user's own file, between it and the
    repository's (requirements section 4.3)."""
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict) or not all(isinstance(p, dict) for p in profiles.values()):
        raise ConfigError(f"profiles in {path} must be tables, as [profiles.<name>]")
    if name not in profiles:
        known = ", ".join(sorted(profiles)) or "none defined"
        raise ConfigError(f"no profile {name!r} in {path} (profiles: {known})")
    _apply(config, profiles[name], f"profile {name}",
           connection_allowed=True, allow_rules_permitted=True)
    config.backend.name = name
    config.record("profile", name, chosen_by)


def load_config(workspace: Path | None = None, *, overrides: dict[str, Any] | None = None,
                home: Path | None = None, profile: str | None = None,
                settings: dict[str, dict[str, Any]] | None = None) -> Config:
    """``profile`` and ``settings`` (from ``-c``) are command-line layers
    (D-042); ``overrides`` are flat keys, applied last."""
    config = Config()
    root = home or user_dir()

    managed = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Foundry" / "policy.toml"
    if managed.is_file():
        _apply(config, _load_toml(managed), "managed",
               connection_allowed=True, allow_rules_permitted=True)

    user = _load_toml(root / "config.toml")
    _apply(config, user, "user", connection_allowed=True, allow_rules_permitted=True)

    chosen = profile or os.environ.get(PROFILE_ENV) or user.get("profile")
    if chosen:
        if not isinstance(chosen, str):
            raise ConfigError("profile must be the name of a [profiles.<name>] table")
        chosen_by = ("cli" if profile else "environment" if os.environ.get(PROFILE_ENV)
                     else "user")
        _apply_profile(config, user, chosen, root / "config.toml", chosen_by)

    if workspace is not None:
        project = workspace / ".foundry" / "config.toml"
        if project.is_file():
            _apply(config, _load_toml(project), "project",
                   connection_allowed=False, allow_rules_permitted=False)

    _apply_env(config)

    if settings:
        _apply(config, settings, "cli (-c)", connection_allowed=True, allow_rules_permitted=True)

    for key, value in (overrides or {}).items():
        if hasattr(config, key):
            setattr(config, key, value)
        elif hasattr(config.backend, key):
            _validate_backend_setting(key, value)
            setattr(config.backend, key, value)
        config.record(key, value, "cli")

    _apply_protocol_defaults(config)
    effort = config.backend.effort
    if effort is not None and effort not in effort_levels(config.backend.protocol):
        raise ConfigError(
            f"backend.effort {effort!r} is not one the {config.backend.protocol} protocol "
            f"takes: {', '.join(effort_levels(config.backend.protocol))}")
    return config


def effort_levels(protocol: str) -> tuple[str, ...]:
    """The reasoning efforts a protocol takes (D-061)."""
    return EFFORT_LEVELS if protocol == ANTHROPIC_PROTOCOL else OPENAI_EFFORT_LEVELS


def _was_set(config: Config, key: str) -> bool:
    """Whether any layer set backend.<key>. CLI overrides record the bare key."""
    return f"backend.{key}" in config.provenance or key in config.provenance


def _apply_protocol_defaults(config: Config) -> None:
    """Swap the built-in default for the protocol's own where nothing set it.

    Decided from provenance, not by comparing against the OpenAI default: a
    user who explicitly points anthropic_messages at a proxy keeps that URL,
    and one who left it alone never sends an Anthropic key to api.openai.com.
    """
    defaults = _PROTOCOL_DEFAULTS.get(config.backend.protocol, {})
    for key, value in defaults.items():
        if not _was_set(config, key):
            setattr(config.backend, key, value)
            config.record(f"backend.{key}", value, f"default for {config.backend.protocol}")
