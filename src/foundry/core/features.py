"""codex's ``[features]`` table, read as Foundry's own settings (D-144).

codex turns parts of itself on and off by name: ``[features] multi_agent =
false``, ``codex features enable NAME``, ``--enable NAME``. A codex user's
file names them, and Foundry reads each name for what it is here. A feature
Foundry has is a switch: its value is the setting's. One Foundry has no
counterpart for is taken as off -- it is off here -- and refused as on, with
what Foundry has instead, as a key no code reads is (D-091): a setting that
quietly does nothing reads as one that works. A name codex does not know is
refused as a typo would be.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from foundry.core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    # What it is here; for one Foundry has no counterpart for, why, and what
    # it has instead.
    said: str
    # The setting it is (a Config attribute), "" for one Foundry has none of.
    setting: str = ""
    # Removed from codex, which accepts and ignores it: so does Foundry.
    ignored: bool = False


_NO_PTY = ("Foundry runs each command to its end, with nothing on its input: it has no "
           "interactive sessions (docs/roadmap.md, Not planned)")
_NO_SEARCH = ("Foundry has no web search of its own; an MCP server that searches can be "
              "added with foundry mcp add")
_NO_SANDBOX = "Foundry has no sandbox (docs/threat-model.md says what that leaves)"
_NO_TIER = "Foundry asks for no service tier: the model and its effort are what you set"

# The features codex documents, by its names.
FEATURES = (
    Feature("multi_agent", "the sub-agents the model may start (runtime.subagents)",
            setting="subagents"),
    Feature("hooks", "the lifecycle hooks your own files name; the machine policy's always "
                     "run", setting="run_hooks"),
    Feature("shell_tool", "the model's run_command; off, each command it asks for is refused",
            setting="shell_tool"),
    Feature("view_image", "the model's view_image tool", setting="view_image"),
    Feature("goals", "/goal, a goal the session works toward", setting="goals"),
    Feature("memories", "notes kept from a workspace's earlier sessions, for the sessions "
                        "after them ([memories]); off by default, as codex's",
            setting="memories"),
    Feature("unified_exec", _NO_PTY),
    Feature("shell_snapshot", "each command starts from the session's own environment "
                              "([shell_environment_policy]), not a snapshot of your shell"),
    Feature("apps", "Foundry has no apps: tools come from MCP servers, added with foundry "
                    "mcp add"),
    Feature("remote_plugin", "Foundry has no plugins: tools come from MCP servers, and skills "
                             "from the skills folder"),
    Feature("fast_mode", _NO_TIER),
    Feature("ultrafast_mode", _NO_TIER),
    Feature("enable_request_compression", "Foundry sends its requests uncompressed"),
    Feature("skill_mcp_dependency_install", "Foundry installs nothing a skill names: add an "
                                            "MCP server with foundry mcp add"),
    Feature("prevent_idle_sleep", "Foundry does not keep the computer awake"),
    Feature("network_proxy", _NO_SANDBOX + ", so no proxy its commands go through"),
    Feature("prefer_mxc", _NO_SANDBOX),
    Feature("web_search", _NO_SEARCH),
    Feature("web_search_cached", _NO_SEARCH),
    Feature("web_search_request", _NO_SEARCH),
    Feature("code_mode", "Foundry has no code mode: the model calls each tool itself"),
    Feature("context_management", "Foundry keeps the context itself: auto_compact and "
                                  "auto_compact_token_limit under [runtime]"),
    Feature("rollout_budget", "Foundry's budgets are runtime.max_tool_rounds and "
                              "max_tool_calls"),
    Feature("apply_patch_freeform", "Foundry's apply_patch takes its patch as a tool "
                                    "call's arguments"),
    Feature("personality", "removed from codex, which accepts and ignores it, as Foundry "
                           "does", ignored=True),
    Feature("undo", "removed from codex, which ignores it, as Foundry does; /undo is "
                    "always there", ignored=True),
)
BY_NAME = {feature.name: feature for feature in FEATURES}
# codex's older names for its features.
ALIASES = {"codex_hooks": "hooks"}

# Every other name codex's [features] takes, as of its 2026 registry: off is
# what each already is here.
CODEX_FEATURES = frozenset("""
analytics_plan_history cli_resume_account_check daemon_auto_start tui_workspace
tui_multi_backend tui_extensions_statusline app_server_websocket_compression transcript_v2
multi_env_copy_file sleep_tool secret_auth_storage unified_exec_tty shell_zsh_fork
unified_exec_zsh_fork login_shell_package_path powershell_shell_version shell_snapshot_v2
deferred_executor cwd_relative_turn_diffs js_repl content_item_kinds
untrusted_as_tool_output executed_tool_call_metadata stable_environment_tools
code_mode_tool_search code_mode_buffered_exec code_mode_host code_mode_host_grpc
code_mode_host_output_truncation code_mode_prewarm code_mode_interrupt
code_mode_tool_description_first instant_interrupt interrupt_on_cancel code_mode_only
code_mode_only_strict_3p_tools js_repl_tools_only terminal_resize_reflow
standalone_web_search hosted_search_policy search_tool codex_git_commit runtime_metrics
critical_path_timing sqlite external_agent_memory_import local_thread_store_compression
local_thread_store_shared_compression background_paginated_rollout_migration chronicle
apply_patch_streaming_events apply_patch_preserve_line_endings exec_permission_approvals
write_stdin_approval request_permissions_tool use_linux_sandbox_bwrap use_legacy_landlock
request_rule experimental_windows_sandbox elevated_windows_sandbox windows_sandbox_service
remote_models api_key_model_discovery cli_daybreak api_key_cyber_access_programs
unbounded_connection_retries credential_masking worktrees respect_system_proxy
system_proxy_fallback multi_agent_v2 model_catalog_in_context multi_agent_v2_dynamic_tools
subagent_thread_environments subagent_default_context_limits defer_mailbox_preemption
wake_on_agent_messages agent_message_board agent_message_board_mcp multi_agent_mode
enable_fanout psp enable_mcp_apps mcp_2026_07_28 mcp_tool_list_changed
mcp_connection_sharing mcp_lazy_stdio_startup apps_early_resource_discovery
codex_apps_mcp_2026_07_28 mcp_oauth_refresh_coordination use_xaa apps_mcp_path_override
tool_search tool_search_always_defer_mcp_tools deferred_tool_world_state
command_sessions_world_state incremental_tools incremental_tools_legacy
suppress_model_change_announcements prediction_preserve_parent_tools
incremental_exec_tools non_prefixed_mcp_tool_names unavailable_dummy_tools tool_suggest
recommended_plugins plugins simplified_selection_hints executor_capability_discovery
executor_capability_discovery_v2 skip_host_skill_discovery plugin_hooks in_app_browser
browser_annotation_api in_app_chat in_app_dictation in_app_voice in_app_local_automation
in_app_updates browser_use browser_use_full_cdp_access browser_use_external computer_use
plugin_discovery_prefetch plugin_sharing external_migration image_generation
image_generation_animation omit_app_server_notification_media image_resize_notice
unified_image_budget resize_all_images item_ids concurrent_reasoning_summaries
output_token_replay omit_skill_descriptions skill_search skill_catalog_deltas
skill_env_var_dependency_prompt mentions_v2 steer default_mode_request_user_input
send_async_message send_message_to_user_async terminal_visualization_instructions
guardian_approval guardian_include_additional_context guardian_recorded_items
guardianv2.thread_context guardian_reuse_parent_compaction guardian_root_handoff_context
guardian_native_multi_agent_history guardian_enhanced_node_repl_transcripts
guardian_node_repl_transcript_images guardian_trust_orchestrator_connectors
guardian_conversation_history_tools guardian_denial_reconsideration guardianv2
guardianv2_decisions_comparison guardianv3 guardian_ext token_budget
reasoning_effort_override current_time_reminder nonfatal_clock_read_errors
collaboration_modes tool_call_mcp_elicitation auth_elicitation bedrock_setup_wizard
gcp_setup_wizard gateway_setup_wizard artifact step_model_switching realtime_conversation
remote_control image_detail_original tui_app_server workspace_owner_usage_nudge
responses_websockets responses_websockets_v2 remote_compaction_v2 async_compaction
async_compaction_root_only compaction_image_budget retain_client_developer_messages
retain_client_tool_outputs use_agent_identity runtime_agent_instances
workspace_dependencies tool_registry
""".split()) - frozenset(BY_NAME)

LIST_HINT = "foundry features list says what each is here"


def refusal(key: str, value: Any) -> str:
    """Why ``features.<key> = value`` is not taken, or "" when it is."""
    name = ALIASES.get(key, key)
    feature = BY_NAME.get(name)
    if feature is None:
        if name in CODEX_FEATURES:
            return ("" if value is False else
                    f"features.{key} cannot be turned on: codex's {key} has no counterpart "
                    "in Foundry, so it is off here")
        close = difflib.get_close_matches(key, [*BY_NAME, *ALIASES, *CODEX_FEATURES], n=1)
        return (f"no feature {key!r}" + (f"; did you mean {close[0]!r}?" if close else "")
                + f" ({LIST_HINT})")
    if not feature.setting and not feature.ignored and value is not False:
        return f"features.{key} cannot be turned on: {feature.said}"
    if not isinstance(value, bool):
        return f"features.{key} must be true or false, got {type(value).__name__} ({value!r})"
    return ""


def apply_features(config: Any, table: Any, layer: str) -> None:
    """One layer's ``[features]``, onto ``config``: each switch's value is its
    setting's, recorded under both names."""
    if not isinstance(table, dict):
        raise ConfigError(f"{layer} config: features must be a table, as [features]")
    named: dict[str, str] = {}
    for key, value in table.items():
        name = ALIASES.get(key, key)
        if name in named:
            raise ConfigError(f"{layer} config: features.{named[name]} and features.{key} "
                              f"are the same feature; set {name} alone")
        named[name] = key
        problem = refusal(key, value)
        if problem:
            raise ConfigError(f"{layer} config: {problem}")
        feature = BY_NAME.get(name)
        if feature is not None and feature.setting:
            setattr(config, feature.setting, value)
            config.record(feature.setting, value, layer)
        config.record(f"features.{name}", value, layer)


def state(config: Any, feature: Feature) -> str:
    """What ``foundry features list`` says of a feature: on, off or ignored."""
    if feature.ignored:
        return "ignored"
    if not feature.setting:
        return "off"
    on = getattr(config, feature.setting)
    if feature.name == "view_image":
        # Offered only to a backend that takes images (D-045).
        on = on and config.backend.images
    return "on" if on else "off"
