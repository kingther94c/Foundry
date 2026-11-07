"""A setting nobody reads is an error in the files too (D-091), as it already
was on the command line (D-042).

Skipped without a word, a typo -- or a key spelled as codex spells it, at the
top of the file or in a flat profile -- looked like a setting that works. A
permission rule was worse: a misspelt ``pattern`` left it matching everything,
so ``allow`` with ``patern = "git *"`` allowed every command. Each is refused
by name, with where the setting lives here when it does.
"""

from __future__ import annotations

import pytest

from foundry.core.config import load_config
from foundry.core.errors import ConfigError

from test_anthropic_wiring import _home


def _refused(tmp_path, toml, **kwargs) -> str:
    with pytest.raises(ConfigError) as caught:
        load_config(home=_home(tmp_path, toml), **kwargs)
    return str(caught.value)


# --- the top of the file -----------------------------------------------------------------


@pytest.mark.parametrize("toml, said", [
    ('model = "o3"\n', "no setting 'model'; it is a backend setting: put it under [backend]"),
    ("max_tool_rounds = 9\n",
     "no setting 'max_tool_rounds'; it is a runtime setting: put it under [runtime]"),
    ('model_reasoning_effort = "high"\n', "Foundry calls it backend.effort"),
    ('approval_policy = "never"\n', "Foundry calls it runtime.mode"),
    ('sandbox_mode = "workspace-write"\n', "Foundry has no sandbox"),
    ('[model_providers.corp]\nbase_url = "https://x"\n', "Foundry has no model providers"),
    ("hide_agent_reasoning = true\n", "runtime.show_reasoning (true there is false here)"),
    ("[runtim]\nmode = \"plan\"\n", "no setting 'runtim'; did you mean 'runtime'?"),
    ("[tui]\nnotifications = true\n", "no setting 'tui'"),
])
def test_a_key_at_the_top_is_refused_with_where_it_lives(tmp_path, toml, said):
    message = _refused(tmp_path, toml)
    assert message.startswith("user config: no setting")
    assert said in message


def test_a_key_nobody_reads_has_no_guess(tmp_path):
    assert _refused(tmp_path, "zzzz = 1\n") == "user config: no setting 'zzzz'"


# --- inside a table ----------------------------------------------------------------------


@pytest.mark.parametrize("toml, said", [
    ('[backend]\nmodle = "o3"\n', "no setting 'backend.modle'; did you mean 'model'?"),
    ('[backend]\nmodel_verbosity = "low"\n', "Foundry calls it backend.verbosity"),
    ('[backend]\napi_key = "x"\n', "secrets come only from the environment or the credential"),
    ('[backend]\nproxy = "http://proxy:8080"\n', "the proxy comes from HTTPS_PROXY"),
    ('[backend]\nca_bundle = "corp.pem"\n', "FOUNDRY_CA_BUNDLE or SSL_CERT_FILE"),
    ("[runtime]\nmax_tool_round = 9\n",
     "no setting 'runtime.max_tool_round'; did you mean 'max_tool_rounds'?"),
    ('[runtime]\nmodel = "o3"\n',
     "no setting 'runtime.model'; it is a backend setting: put it under [backend]"),
    ('[backend]\nmode = "plan"\n',
     "no setting 'backend.mode'; it is a runtime setting: put it under [runtime]"),
])
def test_a_key_inside_a_table_is_refused(tmp_path, toml, said):
    assert said in _refused(tmp_path, toml)


def test_the_managed_policy_is_checked_too(tmp_path, monkeypatch):
    """A policy key nobody reads is a restriction an administrator believes
    in and nobody applies."""
    (tmp_path / "pd" / "Foundry").mkdir(parents=True)
    (tmp_path / "pd" / "Foundry" / "policy.toml").write_text(
        "[runtime]\nmax_tool_call = 5\n", encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    assert _refused(tmp_path, "") == (
        "managed config: no setting 'runtime.max_tool_call'; did you mean 'max_tool_calls'?")


def test_a_secret_is_never_echoed(tmp_path):
    message = _refused(tmp_path, '[backend]\napi_key = "sk-live-do-not-print-0123456789"\n')
    assert "sk-live" not in message


@pytest.mark.parametrize("toml, said", [
    ('backend = "gpt-5"\n', "user config: backend must be a table, as [backend]"),
    ('runtime = "plan"\n', "user config: runtime must be a table, as [runtime]"),
])
def test_a_section_that_is_not_a_table_is_refused(tmp_path, toml, said):
    assert _refused(tmp_path, toml) == said


def test_what_works_still_loads(tmp_path):
    config = load_config(home=_home(tmp_path, (
        'profile = "fast"\nnotify = ["notify-send"]\n'
        '[backend]\nmodel = "gpt-5"\neffort = "high"\nverbosity = "low"\n'
        '[runtime]\nmax_tool_rounds = 9\nmode = "plan"\nshow_reasoning = false\n'
        '[[permissions]]\ntool = "run_command"\npattern = "git *"\ndecision = "allow"\n'
        'reason = "mine"\n'
        '[shell_environment_policy]\ninherit = "core"\n'
        '[mcp_servers.docs]\ncommand = "docs-server"\n'
        '[profiles.fast.backend]\nmodel = "gpt-5-mini"\n')))
    assert (config.backend.model, config.max_tool_rounds, config.backend.verbosity) == (
        "gpt-5-mini", 9, "low")


# --- profiles ----------------------------------------------------------------------------


def test_a_flat_profile_as_codex_writes_it_is_refused_with_the_table_it_needs(tmp_path):
    toml = '[profiles.fast]\nmodel = "gpt-5-mini"\n'
    message = _refused(tmp_path, toml, profile="fast")
    assert message == ("profile fast config: no setting 'model'; it is a backend setting: "
                       "put it under [profiles.fast.backend]")


def test_a_profile_not_chosen_is_not_read(tmp_path):
    home = _home(tmp_path, '[profiles.fast]\nmodel = "gpt-5-mini"\n')
    assert load_config(home=home).backend.model == "gpt-5"


@pytest.mark.parametrize("toml", [
    '[profiles.fast.backend]\nmodle = "x"\n',
    '[profiles.fast]\nprofile = "slow"\n',
])
def test_a_profiles_own_keys_are_checked(tmp_path, toml):
    message = _refused(tmp_path, toml, profile="fast")
    assert message.startswith("profile fast config: no setting")


def test_profiles_do_not_nest(tmp_path):
    message = _refused(tmp_path, '[profiles.fast.profiles.x.backend]\nmodel = "y"\n',
                       profile="fast")
    assert "profiles are tables of your own config.toml, and do not nest" in message


# --- other files ---------------------------------------------------------------------------


def _repository(tmp_path, toml):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(toml, encoding="utf-8")
    return workspace


@pytest.mark.parametrize("toml, said", [
    ('profile = "fast"\n', "project config: no setting 'profile'; profiles are tables"),
    ("[runtime]\nmax_tool_round = 9\n", "project config: no setting 'runtime.max_tool_round'"),
    ('model = "o3"\n', "project config: no setting 'model'"),
])
def test_a_repository_is_checked_too(tmp_path, toml, said):
    with pytest.raises(ConfigError) as caught:
        load_config(_repository(tmp_path, toml), home=_home(tmp_path, ""))
    assert str(caught.value).startswith(said)


def test_a_repository_backend_names_every_key_it_may_not_set(tmp_path):
    """The message listed only the endpoint's keys, so a repository setting
    effort was told it may not set "connection settings ()"."""
    with pytest.raises(ConfigError) as caught:
        load_config(_repository(tmp_path, '[backend]\neffort = "low"\nmodel = "x"\n'),
                    home=_home(tmp_path, ""))
    assert str(caught.value) == ("project config may not set connection settings "
                                 "(effort, model)")


# --- permission rules --------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["allow", "deny", "ask"])
def test_a_misspelt_rule_key_is_refused_not_read_as_everything(tmp_path, decision):
    toml = (f'[[permissions]]\ntool = "run_command"\npatern = "git *"\n'
            f'decision = "{decision}"\n')
    assert _refused(tmp_path, toml) == (
        "permission 0: no setting 'patern' (a rule takes decision, pattern, reason, tool)")


def test_a_rule_is_counted_from_zero_in_its_file(tmp_path):
    toml = ('[[permissions]]\ntool = "read_file"\ndecision = "allow"\n'
            '[[permissions]]\ntool = "run_command"\ncommand = "npm test"\ndecision = "allow"\n')
    assert _refused(tmp_path, toml).startswith("permission 1: no setting 'command'")
