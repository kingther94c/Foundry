"""What a child is given (D-153).

P2's review of child processes found:

* A relative PATH entry (`.`, `bin`, `C:tools`) is searched by cmd.exe and
  PowerShell from the current directory -- the workspace -- so a repository
  that ships `bin\\git.cmd` supplied the program a hook or a command ran.
* The variables the configuration reads a credential from -- the backend's
  `env_key` and `env_http_headers`, an MCP server's `bearer_token_env_var` and
  `env_http_headers` -- passed to every command and hook with
  `inherit = "all"` when their names did not look like a credential's.
* A credential passed by name was registered with the redactor only when the
  first command ran, an MCP server's `env_vars` never, and a fine-grained
  GitHub token's shape was not one the redactor knew.
"""

from __future__ import annotations

import os
import sys

import pytest

from foundry.cli.wiring import _guard_credentials, build
from foundry.core import winapi
from foundry.core.config import load_config
from foundry.core.config_mcp import McpServerConfig
from foundry.core.config_sections import ShellEnvironmentPolicy
from foundry.core.hooks import Hook, HookSet
from foundry.core.mcp import StdioClient
from foundry.core.redaction import Redactor
from foundry.core.winapi import IS_WINDOWS, absolute_path_entries, child_environment

from test_hooks import OVERRIDES, home, machine, repo  # noqa: F401  (fixtures)
from test_mcp import _config


@pytest.fixture()
def redactor(monkeypatch):
    fresh = Redactor()
    monkeypatch.setattr("foundry.core.redaction.default_redactor", lambda: fresh)
    monkeypatch.setattr("foundry.core.mcp.default_redactor", lambda: fresh)
    return fresh


@pytest.fixture()
def withheld(monkeypatch):
    names: set[str] = set()
    monkeypatch.setattr(winapi, "_WITHHELD", names)
    return names


# --- PATH ----------------------------------------------------------------------------------


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows paths")
def test_a_relative_path_entry_is_dropped():
    path = ";".join(["C:\\bin", ".", "bin", "C:tools", "\\rooted", '"C:\\Program Files\\x"',
                     "\\\\server\\share\\tools", "", ".\\scripts", "..\\up"])
    assert absolute_path_entries(path) == ";".join(
        ["C:\\bin", '"C:\\Program Files\\x"', "\\\\server\\share\\tools"])


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows paths")
@pytest.mark.parametrize("policy", [None, ShellEnvironmentPolicy(inherit="all"),
                                    ShellEnvironmentPolicy(set=(("PATH", "bin;C:\\only"),))])
def test_every_child_s_path_is_absolute_whatever_the_policy(policy):
    env = child_environment({"Path": "bin;C:\\bin;.", "SYSTEMROOT": "C:\\Windows"},
                            policy=policy)
    (name,) = [name for name in env if name.upper() == "PATH"]
    assert env[name] == ("C:\\only" if policy and policy.set else "C:\\bin")


@pytest.mark.skipif(not IS_WINDOWS, reason="hooks run through cmd.exe here")
def test_a_hook_does_not_run_the_repository_s_program_through_path(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "hostname.bat").write_text("@echo planted\r\n", encoding="ascii")
    environ = {**os.environ, "PATH": "bin;" + os.environ["PATH"]}
    (run,) = HookSet(cwd=tmp_path, environ=environ).run(
        [Hook("PreToolUse", "", "hostname")], {})
    assert run.exit_code == 0 and run.stdout.strip()
    assert "planted" not in run.stdout


# --- the configuration's credentials -----------------------------------------------------

SOURCE = {"PATH": "C:\\bin", "GATEWAY_AUTH": "gw-0123456789abcdef", "CORP_ID": "corp-id-42-xyz",
          "DOCS_BEARER": "docs-bearer-0123456", "DOCS_TENANT": "tenant-0123456789",
          "MY_FLAG": "1"}


def test_a_withheld_variable_is_kept_as_a_credential_is(withheld, redactor):
    winapi.withhold_variables(["gateway_auth", "", None])
    assert withheld == {"GATEWAY_AUTH"}
    everything = child_environment(SOURCE, policy=ShellEnvironmentPolicy(inherit="all"))
    assert "GATEWAY_AUTH" not in everything and everything["MY_FLAG"] == "1"
    by_pattern = child_environment(SOURCE, policy=ShellEnvironmentPolicy(include=("GATEWAY_*",)))
    assert "GATEWAY_AUTH" not in by_pattern
    assert redactor.registered_values() == ()
    named = child_environment(SOURCE, policy=ShellEnvironmentPolicy(include=("Gateway_Auth",)))
    assert named["GATEWAY_AUTH"] == "gw-0123456789abcdef"
    assert redactor.scrub("key gw-0123456789abcdef") == "key [redacted]"


def test_the_core_allowlist_withholds_one_too(withheld):
    winapi.withhold_variables(["PIP_INDEX_URL"])
    source = {"PATH": "C:\\bin", "PIP_INDEX_URL": "https://user:pw@pypi.corp/simple"}
    assert "PIP_INDEX_URL" not in child_environment(source)


def test_a_session_withholds_what_its_configuration_reads_a_credential_from(
        tmp_path, withheld, redactor, monkeypatch):
    for name, value in SOURCE.items():
        if name != "PATH":
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("NPM_TOKEN", "npm-secret-0123456789")
    config = load_config(home=_config(tmp_path / "home", (
        '[backend]\nenv_key = "GATEWAY_AUTH"\nenv_http_headers = { "X-Corp" = "CORP_ID" }\n'
        '[mcp_servers.docs]\nurl = "https://docs.corp/mcp"\n'
        'bearer_token_env_var = "DOCS_BEARER"\n'
        'env_http_headers = { "X-Tenant" = "DOCS_TENANT" }\n'
        '[shell_environment_policy]\ninherit = "all"\ninclude = ["NPM_TOKEN"]\n')))
    assert redactor.registered_values() == ()
    _guard_credentials(config)
    assert withheld == {"GATEWAY_AUTH", "CORP_ID", "DOCS_BEARER", "DOCS_TENANT"}
    # Registered now, before any command builds an environment.
    assert redactor.registered_values() == ("npm-secret-0123456789",)
    env = child_environment(policy=config.shell_environment)
    assert not {"GATEWAY_AUTH", "CORP_ID", "DOCS_BEARER", "DOCS_TENANT"} & set(env)
    assert env["NPM_TOKEN"] == "npm-secret-0123456789" and env["MY_FLAG"] == "1"


def test_build_guards_before_anything_starts(repo, home, monkeypatch):
    guarded = []
    monkeypatch.setattr("foundry.cli.wiring._guard_credentials", guarded.append)
    wiring = build(repo, home=home, overrides=OVERRIDES)
    try:
        assert guarded == [wiring.config]
    finally:
        wiring.session.close()


# --- what the redactor knows ---------------------------------------------------------------


def test_a_fine_grained_github_token_is_scrubbed():
    token = "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOP"
    assert Redactor().scrub(f"push with {token} now") == "push with [redacted] now"


def test_an_mcp_server_s_env_vars_are_scrubbed(tmp_path, redactor):
    server = McpServerConfig(name="docs", command=sys.executable,
                             env_vars=("DOCS_SECRET", "UNSET_ONE"), env=(("PLAIN", "plain-1234"),))
    env = StdioClient(server, tmp_path, environ={"PATH": "C:\\bin",
                                                 "DOCS_SECRET": "docs-secret-0123"}).environment()
    assert env["DOCS_SECRET"] == "docs-secret-0123" and "UNSET_ONE" not in env
    assert redactor.registered_values() == ("docs-secret-0123",)
