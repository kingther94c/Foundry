"""Which variables a command gets (D-083): ``[shell_environment_policy]`` in
the user's own config.toml, as codex's -- ``inherit``, ``include``,
``exclude``, ``set`` and ``include_only``.

What carries it: without it nothing changes (the build allowlist, D-049);
with it the user can hand commands what their builds need -- an index URL,
JVM options, a registry token -- but a credential-shaped variable is never
passed by inheritance or a pattern, only by its own exact name, and then its
value is scrubbed from what the command prints. A repository cannot set it.
"""

from __future__ import annotations

import pytest

from foundry.cli.wiring import _close
from foundry.core.config import load_config
from foundry.core.config_sections import ShellEnvironmentPolicy
from foundry.core.errors import ConfigError
from foundry.core.redaction import Redactor
from foundry.core.winapi import child_environment

from foundry.core.events import TerminalStatus

from test_mcp import _config
from test_resume import _Live, _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

SOURCE = {
    "PATH": "C:\\bin", "SYSTEMROOT": "C:\\Windows", "HTTP_PROXY": "http://proxy:8080",
    "UV_INDEX_URL": "https://pypi.corp/simple", "uv_cache_dir": "D:\\uv",
    "JAVA_TOOL_OPTIONS": "-Xmx2g", "DATABASE_URL": "postgres://localhost/test",
    "NPM_TOKEN": "npm-secret-0123456789", "NPM_CONFIG_REGISTRY": "https://npm.corp",
    "GITHUB_TOKEN": "ghp-secret", "MY_FLAG": "1",
}


def _env(**policy):
    env = child_environment(SOURCE, policy=ShellEnvironmentPolicy(**policy))
    return {k: v for k, v in env.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING",
                                                       "PYTHONDONTWRITEBYTECODE")}


CORE = {"PATH", "SYSTEMROOT", "HTTP_PROXY", "NPM_CONFIG_REGISTRY"}


# --- the environment ---------------------------------------------------------------------


def test_without_settings_it_is_the_build_allowlist():
    assert set(_env()) == CORE
    assert child_environment(SOURCE) == child_environment(SOURCE,
                                                          policy=ShellEnvironmentPolicy())


def test_include_adds_by_name_or_pattern_whatever_the_case():
    env = _env(include=("uv_*", "Java_Tool_Options"))
    assert set(env) == CORE | {"UV_INDEX_URL", "uv_cache_dir", "JAVA_TOOL_OPTIONS"}


def test_a_secret_passes_only_by_its_own_name_and_is_then_scrubbed(monkeypatch):
    redactor = Redactor()
    monkeypatch.setattr("foundry.core.redaction.default_redactor", lambda: redactor)
    assert "NPM_TOKEN" not in _env(include=("NPM_*",))
    assert "NPM_TOKEN" not in _env(inherit="all")
    assert redactor.registered_values() == ()
    env = _env(include=("npm_token",))
    assert env["NPM_TOKEN"] == "npm-secret-0123456789" and "GITHUB_TOKEN" not in env
    assert redactor.scrub("using npm-secret-0123456789") == "using [redacted]"


def test_inherit_all_or_none():
    assert set(_env(inherit="all")) == set(SOURCE) - {"NPM_TOKEN", "GITHUB_TOKEN"}
    assert _env(inherit="none", include=("MY_FLAG",)) == {"MY_FLAG": "1"}


def test_exclude_takes_out_even_what_a_build_would_get():
    assert set(_env(include=("UV_*",), exclude=("HTTP_PROXY", "uv_cache*"))) == (
        CORE - {"HTTP_PROXY"}) | {"UV_INDEX_URL"}


def test_set_gives_plain_values_over_what_was_inherited():
    env = _env(set=(("CI", "1"), ("PATH", "C:\\only")))
    assert env["CI"] == "1" and env["PATH"] == "C:\\only"


def test_include_only_keeps_only_what_it_names_and_foundry_still_sets_its_own():
    env = child_environment(SOURCE, policy=ShellEnvironmentPolicy(
        include_only=("PATH", "SYSTEM*")))
    assert env == {"PATH": "C:\\bin", "SYSTEMROOT": "C:\\Windows", "PYTHONUTF8": "1",
                   "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


# --- the setting -------------------------------------------------------------------------


def test_the_users_config_sets_it(tmp_path):
    config = load_config(home=_config(tmp_path / "home", (
        '[shell_environment_policy]\ninherit = "all"\ninclude = ["NPM_TOKEN"]\n'
        'exclude = ["CONDA_*"]\ninclude_only = ["PATH", "NPM_*"]\nset = { CI = "1" }\n')))
    assert config.shell_environment == ShellEnvironmentPolicy(
        inherit="all", include=("NPM_TOKEN",), exclude=("CONDA_*",),
        include_only=("PATH", "NPM_*"), set=(("CI", "1"),))
    plain = load_config(home=_config(tmp_path / "plain", ""))
    assert plain.shell_environment == ShellEnvironmentPolicy()


def test_a_profile_may_change_it(tmp_path):
    config = load_config(home=_config(tmp_path / "home", (
        '[shell_environment_policy]\ninclude = ["UV_*"]\n'
        '[profiles.build.shell_environment_policy]\nset = { CI = "1" }\n')), profile="build")
    assert config.shell_environment == ShellEnvironmentPolicy(include=("UV_*",),
                                                              set=(("CI", "1"),))


@pytest.mark.parametrize("text, message", [
    ('inherit = "some"\n', "inherit must be one of core, all, none"),
    ('include = "UV_*"\n', "include must be a list of strings"),
    ('exclude = ["A=B"]\n', "exclude must name variables"),
    ('set = { CI = 1 }\n', "set must be a table of strings"),
    ('set = { API_TOKEN = "x" }\n',
     "API_TOKEN looks like a secret, and shell_environment_policy.set writes its value"),
    ('ignore_default_excludes = true\n', "a variable whose name looks like a credential is "
                                         "passed only when include names it exactly"),
    ('inheritt = "all"\n', "no setting 'inheritt'"),
])
def test_a_bad_setting_is_refused(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(home=_config(tmp_path / "home", "[shell_environment_policy]\n" + text))


def test_a_repository_may_not_set_it(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        '[shell_environment_policy]\ninherit = "all"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not set shell_environment_policy"):
        load_config(workspace, home=_config(tmp_path / "home", ""))


# --- what a command gets -----------------------------------------------------------------


def test_commands_in_a_session_get_it(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_TEST_FLAG", "from-the-shell")
    monkeypatch.setenv("FOUNDRY_TEST_HIDDEN", "not-passed")
    home = _config(tmp_path / "home", '[shell_environment_policy]\ninclude = '
                                      '["FOUNDRY_TEST_FLAG"]\nset = { FOUNDRY_TEST_SET = "set" }\n')
    wiring = _wire(repo, home, [])
    assert wiring.runtime.tool_ctx.env_policy == wiring.config.shell_environment
    shown = _shown(_repl(wiring, '!Write-Output "<$env:FOUNDRY_TEST_FLAG|$env:FOUNDRY_TEST_SET|'
                                 '$env:FOUNDRY_TEST_HIDDEN>"'))
    assert "<from-the-shell|set|>" in shown


def test_the_models_commands_get_it_too(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_TEST_FLAG", "from-the-shell")
    home = _config(tmp_path / "home", '[shell_environment_policy]\ninclude = '
                                      '["FOUNDRY_TEST_FLAG"]\n')
    wiring = _wire(repo, home, [])
    wiring.runtime.backend = _Live([
        turn(call("c1", "run_command", {"command": 'Write-Output "<$env:FOUNDRY_TEST_FLAG>"'})),
        turn(text="done")])
    wiring.runtime.run_turn("what is the flag?")
    _close(wiring, TerminalStatus.CANCELLED, "session ended")
    results = [block.content for message in wiring.runtime.context.history
               for block in message.blocks if getattr(block, "call_id", "") == "c1"
               and hasattr(block, "is_error")]
    assert any("<from-the-shell>" in content for content in results), results
