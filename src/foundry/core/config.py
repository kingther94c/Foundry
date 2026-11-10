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
import difflib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from foundry.core.config_mcp import McpServerConfig, _ENV_NAME, _HEADER_NAME, _parse_mcp_servers
from foundry.core.config_sections import (
    DEFAULT_TERMINAL_TITLE,
    MemorySettings,
    ShellEnvironmentPolicy,
    _parse_doc_names,
    _parse_memories,
    _parse_notify,
    _parse_rules,
    _parse_shell_environment,
    _parse_skills,
    title_items,
)
from foundry.core.errors import ConfigError
from foundry.core.features import apply_features
from foundry.core.hooks import HOOKS_FILE, Hook, parse_hooks, read_hooks_file
from foundry.core.policy.engine import Layer, Mode, Rule, Verdict


USER_DIR_ENV = "FOUNDRY_HOME"

ENV_PREFIX = "FOUNDRY_"

PROFILE_ENV = "FOUNDRY_PROFILE"


# Per-protocol defaults for settings no layer set. The base URL is the one that
# matters: an Anthropic key sent to the OpenAI default host is a credential
# handed to the wrong provider.
ANTHROPIC_PROTOCOL = "anthropic_messages"

CHAT_PROTOCOL = "openai_compat"

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

# How long the answers are (D-090), as codex's model_verbosity: GPT-5's
# text.verbosity on the Responses API, verbosity on Chat Completions. Claude
# has no such setting.
VERBOSITY_LEVELS = ("low", "medium", "high")

MIN_CONTEXT_TOKENS = 1000

# Below this a reply has no room for a tool call (D-096).
MIN_OUTPUT_TOKENS = 256


def _validate_backend_setting(key: str, value: Any) -> None:
    """Range and type checks for backend settings that have them."""
    if key == "effort":
        if value is not None and value not in ANY_EFFORT:
            raise ConfigError(
                f"backend.effort must be one of {', '.join(ANY_EFFORT)}, got {value!r}"
            )
    elif key == "verbosity":
        if value is not None and value not in VERBOSITY_LEVELS:
            raise ConfigError(
                f"backend.verbosity must be one of {', '.join(VERBOSITY_LEVELS)}, got {value!r}"
            )
    elif key == "review_model":
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ConfigError(f"backend.review_model must be a model's name, got {value!r}")
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
    elif key == "max_output_tokens":
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < MIN_OUTPUT_TOKENS:
            raise ConfigError("backend.max_output_tokens must be a whole number of tokens, at "
                              f"least {MIN_OUTPUT_TOKENS}, got {value!r}")
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
    # codex's review_model (D-143): what /review's task and foundry review
    # run on, behind the same endpoint; None for the session's model.
    review_model: str | None = None
    protocol: str = "openai_compat"
    credential_source: str = "api_key"
    headers: dict[str, str] = field(default_factory=dict)
    request_max_retries: int = 4
    # How often a response stream cut part-way is asked again (D-099), as
    # codex's: 0 never.
    stream_max_retries: int = 5
    stream_idle_timeout_ms: int = 300_000
    # As codex's model_providers (D-084): the variable this backend's key is
    # read from, and only from (None: the protocol's own, or the store);
    # headers whose values are read from variables; and parameters added to
    # every request's URL.
    env_key: str | None = None
    env_http_headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    stream: bool = True
    # The reasoning effort (D-061; None leaves it to the API), and, for
    # anthropic_messages only, server-side fallbacks ("off" disables them).
    effort: str | None = None
    fallbacks: str = "default"
    # The model's context window, which masking and compaction are planned
    # against, on every protocol (D-095); None keeps the backend's own figure.
    max_context_tokens: int | None = None
    # The most one reply may be, as codex's model_max_output_tokens (D-096),
    # on every protocol. None leaves it to the backend: 64,000 tokens for
    # Claude (16,000 unstreamed), the API's own for the OpenAI shapes.
    max_output_tokens: int | None = None
    # The reasoning summary asked for (D-044). Unset: anthropic_messages asks
    # for its summary, responses asks for nothing (a model that does not reason
    # rejects the field). "none" asks for nothing anywhere.
    reasoning_summary: str | None = None
    # How long the answers are (D-090): low, medium or high, for the OpenAI
    # shapes only. Unset sends nothing, leaving it to the API.
    verbosity: str | None = None
    # Whether the model takes images (D-045): view_image, -i and /image, and an
    # MCP tool's images. False for a text-only model, which refuses a request
    # that carries one.
    images: bool = True


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
    # How many days a session is kept after it was last written (OQ-19,
    # D-163); 0 keeps every one. The user's to set, never a repository's:
    # deleting a developer's journals on a default they never chose is not a
    # change to make quietly.
    session_retention_days: int = 0
    # Summary compaction before the context fills (D-040); off, a long task
    # ends partial(context window exhausted) as before.
    auto_compact: bool = True
    # Compact past this many tokens when that is sooner than 80% of the
    # window (D-094), as codex's model_auto_compact_token_limit.
    auto_compact_token_limit: int | None = None
    # Whether the model's reasoning is shown (D-044); the journal keeps it.
    show_reasoning: bool = True
    # Whether the model may start sub-agents (D-141), as codex's multi_agent
    # feature, on by default as codex's is.
    subagents: bool = True
    # codex's other features Foundry has (D-144), each on by default as
    # codex's is: the hooks of the user's own files (the machine policy's
    # always run), the model's run_command and view_image, and /goal.
    run_hooks: bool = True
    shell_tool: bool = True
    view_image: bool = True
    goals: bool = True
    # codex's memories (D-145): off by default, as codex's are; turned on with
    # its [features] memories, and its [memories] settings.
    memories: bool = False
    memory_settings: MemorySettings = field(default_factory=lambda: MemorySettings())
    # The editor a file the model cites opens in (D-106), as codex's
    # file_opener: one of FILE_OPENERS, ``none`` for no links.
    file_opener: str = "vscode"
    # What an interactive session's terminal title shows (D-139), as codex's
    # tui.terminal_title: TERMINAL_TITLE_ITEMS, in order; () for no title.
    terminal_title: tuple[str, ...] = DEFAULT_TERMINAL_TITLE
    # codex's developer_instructions (D-142): told to the model after
    # Foundry's own instructions; "" for none.
    developer_instructions: str = ""
    # codex's compact_prompt, and what its experimental_compact_prompt_file
    # holds (D-142): what compaction asks the model for; the text first, then
    # the file's, then Foundry's own.
    compact_prompt: str = ""
    compact_prompt_file: str = ""
    # The repository's instruction files (D-086), as codex's settings: how
    # many characters of them are read, shared from the top down (0 reads
    # none), and the names tried in each directory after AGENTS.override.md,
    # FOUNDRY.md and AGENTS.md.
    project_doc_max_bytes: int = 24_000
    project_doc_fallback_filenames: tuple[str, ...] = ()
    # From the user's own files only: each one is a program Foundry starts.
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    # A program run when a turn ends (D-050), from the user's own files only:
    # the program and its arguments; the turn's JSON is added as the last.
    notify: tuple[str, ...] = ()
    # Which variables commands get (D-083), from the user's own files only.
    shell_environment: ShellEnvironmentPolicy = field(default_factory=ShellEnvironmentPolicy)
    # codex's [skills] (D-114), from the user's own files only: which skills
    # are on or off -- ("path" or "name", what it matches, on) in order, the
    # last that matches deciding, the machine's policy's after the user's --
    # and the tokens the model's list may take.
    skill_switches: tuple[tuple[str, str, bool], ...] = ()
    managed_skill_switches: tuple[tuple[str, str, bool], ...] = ()
    skills_max_context_tokens: int | None = None
    # codex's lifecycle hooks (D-115), from the user's own files and the
    # machine's policy only: each one is a program Foundry starts.
    hooks: tuple[Hook, ...] = ()
    # How many of them, first, are the machine policy's.
    managed_hooks: int = 0

    def active_hooks(self) -> tuple[Hook, ...]:
        """The hooks a session runs: features.hooks = false leaves out those
        of the user's own files, never the machine policy's (D-144)."""
        return self.hooks if self.run_hooks else self.hooks[:self.managed_hooks]

    def all_skill_switches(self) -> tuple[tuple[str, str, bool], ...]:
        """The user's, then the policy's: what the machine's policy says of
        a skill is said last, and so decides."""
        return (*self.skill_switches, *self.managed_skill_switches)
    rules: list[Rule] = field(default_factory=list)
    provenance: dict[str, Provenance] = field(default_factory=dict)

    def record(self, key: str, value: Any, layer: str) -> None:
        self.provenance[key] = Provenance(value, layer)

    def explain(self, key: str) -> str:
        entry = self.provenance.get(key)
        return f"{key} = {entry.value!r} (from {entry.layer})" if entry else f"{key} is unset"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


# The editors a cited file may open in (D-106), as codex's file_opener.
FILE_OPENERS = ("vscode", "vscode-insiders", "windsurf", "cursor", "none")

# What a configuration file holds (D-091). Profiles are the user's file's own.
_SECTIONS = frozenset({"backend", "runtime", "permissions", "mcp_servers", "notify",
                       "shell_environment_policy", "skills", "hooks", "features",
                       "memories"})

_PROFILE_KEYS = frozenset({"profile", "profiles"})


# Another tool's names for settings Foundry has under its own, so a file
# carried over from codex is told where each one lives here.
_CALLED_HERE = {
    "model_reasoning_effort": "backend.effort",
    "model_reasoning_summary": "backend.reasoning_summary",
    "model_verbosity": "backend.verbosity",
    "model_context_window": "backend.max_context_tokens",
    "model_max_output_tokens": "backend.max_output_tokens",
    "model_auto_compact_token_limit": "runtime.auto_compact_token_limit",
    "approval_policy": "runtime.mode",
    "hide_agent_reasoning": "runtime.show_reasoning (true there is false here)",
}

_NOT_A_SETTING = {
    "api_key": "secrets come only from the environment or the credential store "
               "(foundry login)",
    "proxy": "the proxy comes from HTTPS_PROXY",
    "ca_bundle": "the CA bundle comes from FOUNDRY_CA_BUNDLE or SSL_CERT_FILE",
    "model_provider": "Foundry has no model providers: a provider's base_url, protocol and "
                      "env_key go under [backend], or under [profiles.NAME.backend]",
    "sandbox_mode": "Foundry has no sandbox; runtime.mode decides what runs without asking",
}

_NOT_A_SETTING["model_providers"] = _NOT_A_SETTING["model_provider"]

_NOT_A_SETTING["sandbox_workspace_write"] = _NOT_A_SETTING["sandbox_mode"]

# codex's replacements for its built-in instructions (D-142). Foundry's carry
# the rules its runtime checks a finish against.
_NOT_A_SETTING["model_instructions_file"] = (
    "Foundry's own instructions are not replaced: they carry the finish and evidence rules "
    "the runtime checks; add to them with runtime.developer_instructions")

_NOT_A_SETTING["instructions"] = _NOT_A_SETTING["model_instructions_file"]

_NOT_A_SETTING["experimental_instructions_file"] = _NOT_A_SETTING["model_instructions_file"]

_NOT_A_SETTING["tui"] = ("Foundry has no [tui] table: codex's tui.terminal_title is "
                         "runtime.terminal_title here, and its other tui settings have no "
                         "counterpart")


def _prompt_file(value: str) -> str:
    """experimental_compact_prompt_file's text (D-142): read when the
    configuration is, as codex reads it, so a file that cannot be used is
    said before a session starts rather than when it compacts."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError("experimental_compact_prompt_file must be an absolute path, got "
                          f"{value!r}")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"could not read experimental_compact_prompt_file {path}: "
                          f"{getattr(exc, 'strerror', None) or exc}") from exc
    if not text:
        raise ConfigError(f"experimental_compact_prompt_file is empty: {path}")
    return text


def _no_setting(layer: str, name: str, known: Any, tables: str) -> ConfigError:
    """A key no code reads (D-091), refused: skipped, a typo or another tool's
    spelling looked like a setting that works. Said with where it belongs."""
    key = name.rpartition(".")[2]
    if key in _NOT_A_SETTING:
        hint = _NOT_A_SETTING[key]
    elif key in _CALLED_HERE:
        hint = f"Foundry calls it {_CALLED_HERE[key]}"
    elif key in _PROFILE_KEYS:
        hint = "profiles are tables of your own config.toml, and do not nest"
    # Valid in its own table, so it reached here from another.
    elif key in BackendConfig.__dataclass_fields__:
        hint = f"it is a backend setting: put it under [{tables}backend]"
    elif key in _RUNTIME_KEYS:
        hint = f"it is a runtime setting: put it under [{tables}runtime]"
    else:
        close = difflib.get_close_matches(key, sorted(known), n=1)
        hint = f"did you mean {close[0]!r}?" if close else ""
    return ConfigError(f"{layer} config: no setting {name!r}" + (f"; {hint}" if hint else ""))


def _check_names(data: dict[str, Any], layer: str, *, profiles_allowed: bool,
                 tables: str) -> None:
    sections = _SECTIONS | _PROFILE_KEYS if profiles_allowed else _SECTIONS
    for key in data:
        if key not in sections:
            raise _no_setting(layer, key, sections, tables)
    for section, known in (("backend", BackendConfig.__dataclass_fields__),
                           ("runtime", _RUNTIME_KEYS)):
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise ConfigError(f"{layer} config: {section} must be a table, as "
                              f"[{tables}{section}]")
        for key in table:
            if key not in known:
                raise _no_setting(layer, f"{section}.{key}", known, tables)


def _apply(config: Config, data: dict[str, Any], layer: str, *,
           connection_allowed: bool, allow_rules_permitted: bool,
           profiles_allowed: bool = False, tables: str = "") -> None:
    _check_names(data, layer, profiles_allowed=profiles_allowed, tables=tables)
    backend = data.get("backend", {})
    if backend and not connection_allowed:
        # Every backend setting, not only the endpoint's: the whole table
        # comes from machine-local layers.
        raise ConfigError(
            f"{layer} config may not set connection settings ({', '.join(sorted(backend))})"
        )
    # runtime.* was type-checked while backend.* was applied blind, so
    # `request_max_retries = 0` -- the natural way to ask for no retries --
    # reached range(0), left the retry loop with nothing to re-raise, and died
    # at `raise None`: a TypeError outside the taxonomy, so the CLI printed a
    # traceback rather than making the request once.
    _BACKEND_INT_FLOORS = {"request_max_retries": 1, "stream_max_retries": 0,
                           "stream_idle_timeout_ms": 1000}
    for key, value in backend.items():
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
                "command_timeout_s"):
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

    if "session_retention_days" in runtime:
        value = runtime["session_retention_days"]
        # Lowering it deletes more: what a repository or a client lowers is
        # the user's journals, so only the user's own layers set it (D-163).
        if tighten_only:
            raise ConfigError(f"{layer} config may not set session_retention_days: how long "
                              "your sessions are kept is yours to say")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError("session_retention_days must be a whole number of days, 0 to "
                              f"keep every session, got {value!r}")
        config.session_retention_days = value
        config.record("session_retention_days", value, layer)

    if "project_doc_max_bytes" in runtime:
        value = runtime["project_doc_max_bytes"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError("project_doc_max_bytes must be a whole number of characters, "
                              f"0 for none, got {value!r}")
        # A repository may read less of its own notes, not fill the context
        # with more of them.
        if tighten_only and value > config.project_doc_max_bytes:
            raise ConfigError(
                f"{layer} config may only lower project_doc_max_bytes (tried {value}, "
                f"current {config.project_doc_max_bytes})")
        config.project_doc_max_bytes = value
        config.record("project_doc_max_bytes", value, layer)

    if "project_doc_fallback_filenames" in runtime:
        # Which of its files become instructions is the user's call: a
        # repository naming its README would steer the agent with a file
        # nobody wrote for it.
        if tighten_only:
            raise ConfigError(f"{layer} config may not set project_doc_fallback_filenames; "
                              "set it in your own config.toml")
        config.project_doc_fallback_filenames = _parse_doc_names(
            runtime["project_doc_fallback_filenames"])
        config.record("project_doc_fallback_filenames",
                      list(config.project_doc_fallback_filenames), layer)

    if "auto_compact_token_limit" in runtime:
        # The user's trade of detail for room: a repository compacting their
        # session early would summarise away what they were reading.
        if tighten_only:
            raise ConfigError(f"{layer} config may not set auto_compact_token_limit; set it "
                              "in your own config.toml")
        value = runtime["auto_compact_token_limit"]
        if isinstance(value, bool) or not isinstance(value, int) or value < MIN_CONTEXT_TOKENS:
            raise ConfigError("auto_compact_token_limit must be a whole number of tokens, at "
                              f"least {MIN_CONTEXT_TOKENS}, got {value!r}")
        config.auto_compact_token_limit = value
        config.record("auto_compact_token_limit", value, layer)

    if "file_opener" in runtime:
        value = runtime["file_opener"]
        if value not in FILE_OPENERS:
            raise ConfigError(f"file_opener must be one of {', '.join(FILE_OPENERS)}, got "
                              f"{value!r}")
        config.file_opener = value
        config.record("file_opener", value, layer)

    if "terminal_title" in runtime:
        config.terminal_title = title_items(runtime["terminal_title"])
        config.record("terminal_title", list(config.terminal_title), layer)

    for key in ("developer_instructions", "compact_prompt", "experimental_compact_prompt_file"):
        if key not in runtime:
            continue
        # What the model is told is the user's to say (D-142): a repository's
        # instructions are its AGENTS.md, labelled as the project's.
        if tighten_only:
            where = ("a repository's instructions go in its AGENTS.md" if layer == "project"
                     else "a client's instructions go in its prompt")
            raise ConfigError(f"{layer} config may not set {key}; set it in your own "
                              f"config.toml ({where})")
        value = runtime[key]
        if not isinstance(value, str):
            raise ConfigError(f"{key} must be text, got {type(value).__name__} ({value!r})")
        if key == "experimental_compact_prompt_file":
            config.compact_prompt_file = _prompt_file(value)
        else:
            setattr(config, key, value.strip())
        config.record(key, value.strip(), layer)

    for key in ("auto_compact", "show_reasoning", "subagents"):
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
                f"{'a repository' if layer == 'project' else layer} config may only "
                f"select a more restrictive mode "
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

    if "skills" in data:
        # Which of the user's skills are on, and the list's room: the user's
        # own choice, as the skills folder is theirs.
        if not connection_allowed:
            raise ConfigError(f"{layer} config may not set skills; set them in your own "
                              "config.toml")
        _parse_skills(data["skills"], config, managed=layer == "managed")
        config.record("skills", len(config.all_skill_switches()), layer)

    if "hooks" in data:
        # Each hook is a program to run, at every tool call: a repository
        # naming one would run code of its choosing, as with notify.
        if not connection_allowed:
            raise ConfigError(f"{layer} config may not set hooks: each is a program Foundry "
                              "runs; set them in your own config.toml or the hooks.json "
                              "beside it")
        config.hooks = (*config.hooks, *parse_hooks(data["hooks"], f"{layer} config"))
        config.record("hooks", len(config.hooks), layer)

    if "features" in data:
        # codex's switches (D-144): the user's own choice of what the session
        # has, as hooks are; a repository turning off the user's hooks would
        # be a loosening dressed as a switch.
        if not connection_allowed:
            raise ConfigError(f"{layer} config may not set features; set them in your own "
                              "config.toml")
        apply_features(config, data["features"], layer)

    if "memories" in data:
        # What is kept of the user's sessions, and what a session is given of
        # it (D-145): the user's own choice, as the feature is.
        if not connection_allowed:
            raise ConfigError(f"{layer} config may not set memories; set them in your own "
                              "config.toml")
        config.memory_settings = _parse_memories(data["memories"], config.memory_settings)
        config.record("memory_settings", dataclasses.asdict(config.memory_settings), layer)

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
                 "session_retention_days", "auto_compact", "show_reasoning", "mode",
                 "project_doc_max_bytes", "project_doc_fallback_filenames",
                 "auto_compact_token_limit", "file_opener", "terminal_title", "subagents",
                 "developer_instructions", "compact_prompt", "experimental_compact_prompt_file")

# Secrets may come only from the environment or the credential store, never
# from a command line other processes can read (requirements section 4.3).
_SECRET_KEYS = frozenset({"api_key", "headers"})


# What -c can set: a session's backend and runtime settings. A backend's name
# is the profile's, which --profile chooses.
_FLAG_KEYS = {"backend": frozenset(BackendConfig.__dataclass_fields__) - {"name"},
              "runtime": frozenset(_RUNTIME_KEYS)}


def _no_flag_setting(name: str, section: str, key: str) -> ConfigError:
    """``-c NAME=...`` that no code reads, said with where it belongs, as a
    file's is (D-098): codex's ``-c model_reasoning_effort=high`` is told
    Foundry's name. Never the value, which may be a secret pasted by mistake."""
    top = section.partition(".")[0]
    if key in _NOT_A_SETTING or top in _NOT_A_SETTING:
        hint = _NOT_A_SETTING.get(key) or _NOT_A_SETTING[top]
    elif key in _CALLED_HERE:
        hint = f"Foundry calls it {_CALLED_HERE[key]}"
    elif key in _PROFILE_KEYS:
        hint = "choose one with -p NAME"
    elif top in _SECTIONS - set(_FLAG_KEYS) - {"features"}:
        hint = (f"-c sets backend, runtime and features settings; [{top}] goes in your "
                "config.toml")
    elif other := next((table for table, keys in _FLAG_KEYS.items()
                        if key in keys and table != section), None):
        hint = f"it is {other}.{key}"
    else:
        # In the table it was given under, when it was given one.
        dotted = "." in name
        pool = _FLAG_KEYS.get(section) if dotted else None
        close = difflib.get_close_matches(key, sorted(pool or set().union(*_FLAG_KEYS.values())),
                                          n=1)
        hint = (f"did you mean {(f'{section}.' if pool else '') + close[0]!r}?" if close
                else "")
    return ConfigError(f"-c: no setting {name!r}" + (f"; {hint}" if hint else ""))


def parse_settings(items: list[str]) -> dict[str, dict[str, Any]]:
    """``-c key=value`` settings as a config layer: ``backend.model=x``,
    ``runtime.max_tool_rounds=60``, or a bare key from either table. The value
    is read as TOML -- ``60``, ``true``, ``"a b"`` -- and as a plain string when
    it is not, so ``-c model=gpt-5`` needs no quotes (D-042)."""
    layer: dict[str, dict[str, Any]] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        if not sep or not name.strip():
            raise ConfigError(f"-c expects key=value, got {item!r}")
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw.strip()
        _set_flag(layer, name.strip(), value)
    return layer


def settings_layer(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The settings -c takes, given as values already typed: the ``config``
    argument of ``foundry mcp-server``'s tools (D-103)."""
    layer: dict[str, dict[str, Any]] = {}
    for name, value in values.items():
        _set_flag(layer, name.strip(), value)
    return layer


def _set_flag(layer: dict[str, dict[str, Any]], name: str, value: Any) -> None:
    if name.startswith("features."):
        # codex's switches (D-144), checked as a file's are when applied:
        # -c features.NAME=true is codex's --enable NAME.
        layer.setdefault("features", {})[name.removeprefix("features.")] = value
        return
    section, dot, key = name.rpartition(".")
    if not dot:
        section = "backend" if key in BackendConfig.__dataclass_fields__ else "runtime"
    if key in _SECRET_KEYS:
        raise ConfigError(f"-c may not set {name}: secrets come only from the environment "
                          "or the credential store (foundry login)")
    if key not in _FLAG_KEYS.get(section, ()):
        raise _no_flag_setting(name, section, key)
    layer.setdefault(section, {})[key] = value


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
           connection_allowed=True, allow_rules_permitted=True, tables=f"profiles.{name}.")
    config.backend.name = name
    config.record("profile", name, chosen_by)


def _add_hooks_file(config: Config, path: Path, layer: str) -> None:
    hooks = read_hooks_file(path)
    if hooks:
        config.hooks = (*config.hooks, *hooks)
        config.record("hooks", len(config.hooks), layer)


def load_config(workspace: Path | None = None, *, overrides: dict[str, Any] | None = None,
                home: Path | None = None, profile: str | None = None,
                settings: dict[str, dict[str, Any]] | None = None,
                client_settings: dict[str, dict[str, Any]] | None = None) -> Config:
    """``profile`` and ``settings`` (from ``-c``) are command-line layers
    (D-042); ``client_settings`` is a ``foundry mcp-server`` client's
    ``config``, which may only tighten (D-150); ``overrides`` are flat keys,
    applied last."""
    config = Config()
    root = home or user_dir()

    managed = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Foundry" / "policy.toml"
    if managed.is_file():
        _apply(config, _load_toml(managed), "managed",
               connection_allowed=True, allow_rules_permitted=True)
    # codex reads a hooks.json beside each config layer's file (D-115).
    _add_hooks_file(config, managed.parent / HOOKS_FILE, "managed")
    config.managed_hooks = len(config.hooks)

    user = _load_toml(root / "config.toml")
    _apply(config, user, "user", connection_allowed=True, allow_rules_permitted=True,
           profiles_allowed=True)
    _add_hooks_file(config, root / HOOKS_FILE, "user")

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

    if client_settings:
        # A foundry mcp-server client's config (D-150), after the server's
        # own -c, with a repository's limits. Applied as -c, it could point
        # the backend at a host of its choosing with the user's credential,
        # read any file as a compact prompt, or turn off the user's hooks:
        # the client gives tasks, and -c's settings are the user's.
        _apply(config, client_settings, "the client's", connection_allowed=False,
               allow_rules_permitted=False)

    for key, value in (overrides or {}).items():
        if hasattr(config, key):
            setattr(config, key, value)
        elif hasattr(config.backend, key):
            _validate_backend_setting(key, value)
            setattr(config.backend, key, value)
        config.record(key, value, "cli")

    _apply_protocol_defaults(config)
    if not config.shell_tool:
        # codex's shell_tool off (D-144): every command the model asks for is
        # refused, ahead of any rule allowing it, and the model is told so with
        # the rules -- as codex's model has no shell. The user's ! still runs.
        config.rules.append(Rule(tool="run_command", pattern="*", verdict=Verdict.DENY,
                                 layer=Layer.USER, rule_id="features.shell_tool",
                                 reason="run_command is off: features.shell_tool = false"))
    effort = config.backend.effort
    if effort is not None and effort not in effort_levels(config.backend.protocol):
        raise ConfigError(
            f"backend.effort {effort!r} is not one the {config.backend.protocol} protocol "
            f"takes: {', '.join(effort_levels(config.backend.protocol))}")
    if config.backend.verbosity is not None and config.backend.protocol == ANTHROPIC_PROTOCOL:
        # Refused, not dropped: a setting that silently does nothing reads as
        # one that works.
        raise ConfigError(
            f"backend.verbosity is not a setting the {ANTHROPIC_PROTOCOL} protocol takes: "
            "it is GPT-5's (text.verbosity on the Responses API, verbosity on Chat "
            "Completions); set it in the profile of an OpenAI endpoint instead")
    # The same for the two that were still dropped without a word (D-100).
    if config.backend.fallbacks != "default" and config.backend.protocol != ANTHROPIC_PROTOCOL:
        raise ConfigError(
            f"backend.fallbacks is not a setting the {config.backend.protocol} protocol takes: "
            f"it is Claude's server-side fallback model ({ANTHROPIC_PROTOCOL}); set it in the "
            "profile of a Claude endpoint instead")
    summary = config.backend.reasoning_summary
    if summary not in (None, "none") and config.backend.protocol == CHAT_PROTOCOL:
        raise ConfigError(
            f"backend.reasoning_summary {summary!r} is not one the {CHAT_PROTOCOL} protocol "
            "takes: Chat Completions has no reasoning summary to ask for (the Responses API "
            "and Claude do); what a server streams of its reasoning is shown unless "
            "runtime.show_reasoning = false")
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
