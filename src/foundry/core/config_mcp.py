"""``[mcp_servers]``: each MCP server the configuration names, read and
checked -- a program to start or a URL to reach, with the secrets it is given
and where they come from.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundry.core.errors import ConfigError
from foundry.core.httpc import _is_loopback
from foundry.core.winapi import looks_like_credential


# A header's name: an HTTP token.
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")

# A value Foundry puts in a header of the user's (D-111): printable ASCII,
# which http.client sends as it is.
HEADER_VALUE = re.compile(r"[\x20-\x7e]{1,4096}\Z")


_SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")

_MCP_KEYS = frozenset({"command", "args", "env", "env_vars", "startup_timeout_s",
                       "tool_timeout_s", "read_only", "enabled", "enabled_tools",
                       "disabled_tools", "url", "bearer_token_env_var", "cwd",
                       "startup_timeout_sec", "tool_timeout_sec", "http_headers",
                       "env_http_headers"})

# What only a server Foundry starts can have (D-081), and what only one it
# reaches at an address can (D-111).
_PROGRAM_KEYS = ("command", "args", "env", "env_vars", "cwd")

_URL_KEYS = ("bearer_token_env_var", "http_headers", "env_http_headers")

# The headers the transport sends itself (D-111): one of the user's under
# such a name would speak for Foundry -- another session, another proxy.
_PROTOCOL_HEADERS = frozenset({"accept", "content-type", "content-length", "host",
                               "connection", "transfer-encoding", "te", "upgrade",
                               "mcp-session-id", "mcp-protocol-version", "last-event-id",
                               "proxy-authorization"})

# A server's timeouts, under Foundry's name and codex's (D-110), with how long
# each may be and is unless said.
_MCP_TIMEOUTS = (("startup_timeout_s", "startup_timeout_sec", 300, 20),
                 ("tool_timeout_s", "tool_timeout_sec", 3600, 120))

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
    # As codex's (D-111): headers sent with every request to such a server,
    # with their values; and those whose values are read from a variable,
    # by the variable's name. A credential is only ever the latter.
    http_headers: tuple[tuple[str, str], ...] = ()
    env_http_headers: tuple[tuple[str, str], ...] = ()
    # The folder a server Foundry starts runs in (D-110), as codex's cwd:
    # absolute; the workspace when not said.
    cwd: str = ""

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


def _strings(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{what} must be a list of strings")
    return tuple(value)


def _seconds(value: Any, what: str, ceiling: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= ceiling:
        raise ConfigError(f"{what} must be a number of seconds above 0 and at most {ceiling:g}")
    return float(value)


def _mcp_url(value: Any, where: str, *, token: bool, secrets: bool = False) -> str:
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
    if (token or secrets) and parts.scheme == "http" and not _is_loopback(parts.hostname):
        # Readable by every hop between here and there.
        what = "a bearer token is" if token else "a header read from a variable is"
        raise ConfigError(f"{where}: {what} sent only over https, or to this "
                          f"machine, and {where}.url is http://{parts.hostname}")
    return url


def _secret_header(name: str) -> bool:
    """Whether a header's name says it carries a credential."""
    upper = name.upper()
    return looks_like_credential(name) or "AUTH" in upper or "COOKIE" in upper


def _mcp_headers(entry: dict, where: str, *, token: bool):
    """A url server's ``http_headers`` and ``env_http_headers`` (D-111), as
    sorted pairs. No value is echoed: one may be a secret pasted by mistake."""
    plain = entry.get("http_headers", {})
    if not isinstance(plain, dict) or not all(
            isinstance(k, str) and _HEADER_NAME.match(k) and isinstance(v, str)
            and HEADER_VALUE.match(v) for k, v in plain.items()):
        raise ConfigError(f"{where}.http_headers must map header names to values of "
                          'printable characters, as { "X-Region" = "eu" }')
    named = entry.get("env_http_headers", {})
    if not isinstance(named, dict) or not all(
            isinstance(k, str) and _HEADER_NAME.match(k) and isinstance(v, str)
            and _ENV_NAME.match(v) for k, v in named.items()):
        raise ConfigError(f"{where}.env_http_headers must map header names to the names of "
                          'environment variables, as { "X-Api-Key" = "DOCS_KEY" }')
    seen = {"authorization"} if token else set()
    for table, headers in (("http_headers", plain), ("env_http_headers", named)):
        for header in headers:
            lower = header.lower()
            if lower in _PROTOCOL_HEADERS:
                raise ConfigError(f"{where}.{table}: Foundry sends {header} itself")
            if lower in seen:
                raise ConfigError(f"{where}.{table}: {header} is given twice; give it once")
            seen.add(lower)
            if table == "http_headers" and _secret_header(header):
                raise ConfigError(f"{where}.http_headers: {header} carries a credential, "
                                  "which never goes in the file; name the variable that "
                                  f"holds it in {where}.env_http_headers")
    return tuple(sorted(plain.items())), tuple(sorted(named.items()))


def _parse_mcp_servers(raw: Any) -> dict[str, McpServerConfig]:
    if not isinstance(raw, dict):
        raise ConfigError("mcp_servers must be tables, as [mcp_servers.<name>]")
    servers: dict[str, McpServerConfig] = {}
    for name, entry in raw.items():
        where = f"mcp_servers.{name}"
        if not _SERVER_NAME.match(name):
            raise ConfigError(f"{where}: a server name is letters, digits, _ and -, "
                              "at most 32 of them")
        if "__" in name or name.endswith("_"):
            # mcp__<server>__<tool> must say where the tool's name begins: a
            # server a__b and a server a with a tool b__c were one name, and a
            # rule for mcp__a__* matched both (D-155).
            raise ConfigError(f"{where}: a server name may not hold __ or end in _, "
                              "which would run into its tools' names")
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
        http_headers, env_http_headers = _mcp_headers(entry, where, token=bool(token))
        url = ""
        command = entry.get("command")
        if "url" in entry:
            both = [key for key in _PROGRAM_KEYS if key in entry]
            if both:
                raise ConfigError(f"{where}: url and {both[0]} do not go together -- a "
                                  "server is a program Foundry starts, or an address it "
                                  "reaches")
            url = _mcp_url(entry["url"], where, token=bool(token),
                           secrets=bool(env_http_headers))
            command = ""
        elif any(entry.get(key) for key in _URL_KEYS):
            given = next(key for key in _URL_KEYS if entry.get(key))
            raise ConfigError(f"{where}.{given} is for a server reached by url")
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
        timeouts = {}
        for ours, theirs, ceiling, default in _MCP_TIMEOUTS:
            if ours in entry and theirs in entry:
                raise ConfigError(f"{where}: {ours} and {theirs} are one setting, Foundry's "
                                  "name and codex's; give one")
            key = theirs if theirs in entry else ours
            timeouts[ours] = _seconds(entry.get(key, default), f"{where}.{key}", ceiling)
        cwd = entry.get("cwd", "")
        if not isinstance(cwd, str) or ("cwd" in entry and not Path(cwd).is_absolute()):
            raise ConfigError(f"{where}.cwd must be the absolute path of the folder the "
                              "server runs in")
        servers[name] = McpServerConfig(
            name=name, command=command.strip(), url=url, bearer_token_env_var=token,
            http_headers=http_headers, env_http_headers=env_http_headers,
            args=_strings(entry.get("args", []), f"{where}.args"),
            env=tuple(sorted(env.items())),
            env_vars=_strings(entry.get("env_vars", []), f"{where}.env_vars"),
            cwd=cwd,
            **timeouts,
            enabled_tools=(_strings(entry["enabled_tools"], f"{where}.enabled_tools")
                           if "enabled_tools" in entry else None),
            disabled_tools=_strings(entry.get("disabled_tools", []),
                                    f"{where}.disabled_tools"),
            **flags,
        )
    return servers
