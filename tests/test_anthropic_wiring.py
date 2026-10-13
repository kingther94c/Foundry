"""Wiring the anthropic_messages backend into config, credentials, CLI and replay.

The adapter itself is covered by test_backend_anthropic.py; this file checks
everything around it, and in particular that an Anthropic key only ever goes to
Anthropic and an OpenAI key only ever goes to OpenAI.
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.app import _PROTOCOLS, build
from foundry.core.auth import ApiKeySource, CredentialVault
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.base import collect_turn
from foundry.core.backends.recording import RecordingBackend
from foundry.core.backends.replay import ReplayBackend, ScriptedBackend
from foundry.core.config import load_config
from foundry.core.conversation import (
    Message,
    ModelTurn,
    OpaqueBlock,
    Role,
    StopReason,
    ToolUseBlock,
    TurnRequest,
    Usage,
)
from foundry.core.errors import AuthError, ConfigError
from foundry.core.redaction import Redactor
from foundry.core.tools.git import run_git

OPENAI_KEY = "sk-openai-canary-0123456789abcdef"
ANTHROPIC_KEY = "sk-ant-canary-0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FOUNDRY_MODEL",
                 "FOUNDRY_BASE_URL", "FOUNDRY_MODE", "FOUNDRY_GATEWAY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    # Keep a machine-wide managed policy out of the picture.
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))


def _home(tmp_path, toml: str):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(toml, encoding="utf-8")
    return home


ANTHROPIC_TOML = '[backend]\nprotocol = "anthropic_messages"\n'


# --- config -----------------------------------------------------------------


def test_anthropic_protocol_defaults_base_url_and_model(tmp_path):
    config = load_config(home=_home(tmp_path, ANTHROPIC_TOML))
    assert config.backend.base_url == "https://api.anthropic.com/v1"
    assert config.backend.model == "claude-opus-5-5"
    assert "default" in config.provenance["backend.base_url"].layer


def test_explicit_base_url_and_model_win(tmp_path):
    home = _home(tmp_path, ANTHROPIC_TOML
                 + 'base_url = "https://proxy.example/v1"\nmodel = "claude-other"\n')
    config = load_config(home=home)
    assert config.backend.base_url == "https://proxy.example/v1"
    assert config.backend.model == "claude-other"


def test_an_explicit_base_url_equal_to_the_openai_default_is_respected(tmp_path):
    """Provenance, not value comparison, decides 'set' from 'default'."""
    home = _home(tmp_path, ANTHROPIC_TOML + 'base_url = "https://api.openai.com/v1"\n')
    assert load_config(home=home).backend.base_url == "https://api.openai.com/v1"


def test_environment_and_cli_overrides_count_as_set(tmp_path, monkeypatch):
    home = _home(tmp_path, ANTHROPIC_TOML)
    monkeypatch.setenv("FOUNDRY_BASE_URL", "https://env.example/v1")
    config = load_config(home=home, overrides={"model": "claude-cli"})
    assert config.backend.base_url == "https://env.example/v1"
    assert config.backend.model == "claude-cli"


def test_other_protocols_keep_the_openai_defaults(tmp_path):
    config = load_config(home=_home(tmp_path, ""))
    assert config.backend.base_url == "https://api.openai.com/v1"
    assert config.backend.model == "gpt-5"


def test_new_settings_parse(tmp_path):
    home = _home(tmp_path, ANTHROPIC_TOML
                 + 'effort = "xhigh"\nfallbacks = "off"\nmax_context_tokens = 500000\n')
    backend = load_config(home=home).backend
    assert (backend.effort, backend.fallbacks, backend.max_context_tokens) == (
        "xhigh", "off", 500000)


def test_new_settings_default(tmp_path):
    backend = load_config(home=_home(tmp_path, ANTHROPIC_TOML)).backend
    assert (backend.effort, backend.fallbacks, backend.max_context_tokens) == (
        None, "default", None)


@pytest.mark.parametrize("line, setting", [
    ('effort = "extreme"', "effort"),
    ("effort = 3", "effort"),
    ('fallbacks = "on"', "fallbacks"),
    ("fallbacks = true", "fallbacks"),
    ("max_context_tokens = 999", "max_context_tokens"),
    ('max_context_tokens = "200000"', "max_context_tokens"),
    ("max_context_tokens = 200000.0", "max_context_tokens"),
    ("max_context_tokens = true", "max_context_tokens"),
])
def test_invalid_new_settings_are_config_errors(tmp_path, line, setting):
    home = _home(tmp_path, ANTHROPIC_TOML + line + "\n")
    with pytest.raises(ConfigError, match=setting):
        load_config(home=home)


def test_max_context_tokens_floor_is_inclusive(tmp_path):
    home = _home(tmp_path, ANTHROPIC_TOML + "max_context_tokens = 1000\n")
    assert load_config(home=home).backend.max_context_tokens == 1000


# --- credentials ------------------------------------------------------------


def test_save_merges_into_what_is_stored(tmp_path):
    vault = CredentialVault(tmp_path / "auth.json", redactor=Redactor())
    vault.save({"api_key": OPENAI_KEY})
    vault.save({"anthropic_api_key": ANTHROPIC_KEY})
    stored = vault.load()
    assert stored["api_key"] == OPENAI_KEY
    assert stored["anthropic_api_key"] == ANTHROPIC_KEY


def test_load_registers_the_anthropic_key_with_the_redactor(tmp_path):
    vault = CredentialVault(tmp_path / "auth.json", redactor=Redactor())
    vault.save({"anthropic_api_key": ANTHROPIC_KEY})
    fresh = Redactor()
    CredentialVault(tmp_path / "auth.json", redactor=fresh).load()
    assert ANTHROPIC_KEY in fresh.registered_values()
    assert ANTHROPIC_KEY not in fresh.scrub(f"key={ANTHROPIC_KEY}")


def test_anthropic_source_ignores_the_openai_vault_field(tmp_path):
    vault = CredentialVault(tmp_path / "auth.json", redactor=Redactor())
    vault.save({"api_key": OPENAI_KEY})
    with pytest.raises(AuthError, match="ANTHROPIC_API_KEY"):
        ApiKeySource.anthropic(vault).acquire()


def test_anthropic_source_reads_its_own_field_and_env(tmp_path, monkeypatch):
    vault = CredentialVault(tmp_path / "auth.json", redactor=Redactor())
    vault.save({"api_key": OPENAI_KEY, "anthropic_api_key": ANTHROPIC_KEY})
    assert ApiKeySource.anthropic(vault).acquire().reveal() == ANTHROPIC_KEY
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    assert ApiKeySource.anthropic(vault).acquire().reveal() == ANTHROPIC_KEY
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env-0123456789")
    assert ApiKeySource.anthropic(vault).acquire().reveal() == "sk-ant-from-env-0123456789"


def test_openai_source_ignores_the_anthropic_field(tmp_path):
    vault = CredentialVault(tmp_path / "auth.json", redactor=Redactor())
    vault.save({"anthropic_api_key": ANTHROPIC_KEY})
    with pytest.raises(AuthError, match="OPENAI_API_KEY"):
        ApiKeySource(vault).acquire()


def test_storing_one_provider_key_keeps_the_other(tmp_path):
    vault = CredentialVault(tmp_path / "auth.json", redactor=Redactor())
    ApiKeySource(vault, redactor=Redactor()).store(OPENAI_KEY)
    source = ApiKeySource.anthropic(vault)
    source.redactor = Redactor()
    source.store(ANTHROPIC_KEY)
    assert ApiKeySource(vault).acquire().reveal() == OPENAI_KEY
    assert ApiKeySource.anthropic(vault).acquire().reveal() == ANTHROPIC_KEY


class _Stdin:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_login_non_tty_names_anthropic_variable(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, ANTHROPIC_TOML)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    monkeypatch.setattr(sys, "stdin", _Stdin(False))
    assert app.cmd_login(argparse.Namespace()) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "ANTHROPIC_API_KEY" in out
    assert "OPENAI_API_KEY" not in out
    assert "Anthropic API key" in out


def test_login_stores_to_the_anthropic_field(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, ANTHROPIC_TOML)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    CredentialVault(home / "auth.json", redactor=Redactor()).save({"api_key": OPENAI_KEY})
    monkeypatch.setattr(sys, "stdin", _Stdin(True))
    import getpass
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": ANTHROPIC_KEY)
    assert app.cmd_login(argparse.Namespace()) == 0
    stored = CredentialVault(home / "auth.json", redactor=Redactor()).load()
    assert stored["anthropic_api_key"] == ANTHROPIC_KEY
    assert stored["api_key"] == OPENAI_KEY
    assert "Anthropic API key" in " ".join(capsys.readouterr().out.split())


def test_login_for_openai_still_names_openai(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, "")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    monkeypatch.setattr(sys, "stdin", _Stdin(False))
    assert app.cmd_login(argparse.Namespace()) == 1
    assert "OPENAI_API_KEY" in " ".join(capsys.readouterr().out.split())


# --- build() ------------------------------------------------------------------


def _repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init"], ["config", "user.email", "t@e.com"],
                 ["config", "user.name", "T"], ["add", "-A"], ["commit", "-m", "i"]):
        run_git(args, repo)
    return repo


def _build(tmp_path, toml: str):
    home = _home(tmp_path, toml)
    log = open(tmp_path / "console.txt", "w", encoding="utf-8")
    try:
        wiring = build(_repo(tmp_path), home=home, interactive=False,
                       console=Console(file=log))
    except BaseException:
        log.close()
        raise
    wiring.session.close()
    log.close()
    return wiring


def test_protocol_table_lists_anthropic():
    assert _PROTOCOLS["anthropic_messages"] is AnthropicBackend


def test_build_wires_effort_fallbacks_and_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    wiring = _build(tmp_path, ANTHROPIC_TOML
                    + 'effort = "max"\nfallbacks = "off"\nmax_context_tokens = 400000\n')
    backend = wiring.runtime.backend
    assert isinstance(backend, AnthropicBackend)
    assert backend.effort == "max"
    assert backend.fallbacks is None
    assert backend.capabilities().max_context_tokens == 400000
    assert backend.capabilities().prompt_caching is True
    assert wiring.runtime.context.max_context_tokens == 400000
    assert backend.base_url == "https://api.anthropic.com/v1"
    assert backend.model == "claude-opus-5-5"
    assert backend.api_key == ANTHROPIC_KEY


def test_build_defaults_keep_fallbacks_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    backend = _build(tmp_path, ANTHROPIC_TOML).runtime.backend
    assert backend.effort is None
    assert backend.fallbacks == "default"
    assert backend.capabilities().max_context_tokens == 200_000


def test_build_never_sends_the_openai_key_to_anthropic(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    home = _home(tmp_path, ANTHROPIC_TOML)
    CredentialVault(home / "auth.json", redactor=Redactor()).save({"api_key": OPENAI_KEY})
    backend = _build(tmp_path, ANTHROPIC_TOML).runtime.backend
    assert backend.api_key == ""


def test_unknown_protocol_error_lists_anthropic(tmp_path):
    with pytest.raises(ConfigError, match="anthropic_messages"):
        _build(tmp_path, '[backend]\nprotocol = "nope"\n')


def test_doctor_lists_anthropic_for_an_unknown_protocol(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, '[backend]\nprotocol = "nope"\n')
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    assert app.cmd_doctor(argparse.Namespace()) == 1
    assert "anthropic_messages" in capsys.readouterr().out


def test_doctor_accepts_anthropic(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, ANTHROPIC_TOML)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    app.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out
    assert "protocol  anthropic_messages" in out
    assert "api.anthropic.com" in out


# --- replay -------------------------------------------------------------------


OPAQUE = OpaqueBlock(provider="anthropic", data=json.dumps(
    [{"type": "thinking", "thinking": "", "signature": "sig"},
     {"type": "tool_use", "id": "toolu_1", "name": "read_file",
      "input": {"path": "a"}, "caller": {"type": "direct"}}]))


def test_record_then_replay_keeps_opaque_and_notices(tmp_path):
    turn = ModelTurn(
        text="reading",
        tool_calls=(ToolUseBlock(call_id="toolu_1", name="read_file",
                                 arguments='{"path": "a"}'),),
        usage=Usage(10, 5, 3), stop_reason=StopReason.TOOL_USE,
        model="claude-opus-5-5", opaque=OPAQUE,
        notices=("thinking dropped at messages.1", "fallback took over"),
    )
    request = TurnRequest(messages=(Message.text(Role.USER, "go"),), tools=(),
                          model="claude-opus-5-5")
    fixture = tmp_path / "fixture.json"
    recorder = RecordingBackend(inner=ScriptedBackend([turn]), fixture_path=fixture)
    collect_turn(iter(recorder.stream_turn(request)))
    recorder.save()

    replayed = collect_turn(iter(ReplayBackend(fixture).stream_turn(request)))
    assert replayed.opaque == OPAQUE
    assert replayed.notices == turn.notices
    assert replayed.as_message() == turn.as_message()


def test_old_fixtures_without_opaque_still_load(tmp_path):
    fixture = tmp_path / "old.json"
    fixture.write_text(json.dumps({
        "fixture_version": 1,
        "entries": [{"signature": {"message_count": 1, "tool_names": [], "model": "m"},
                     "turn": {"text": "hi", "tool_calls": [],
                              "usage": {"input_tokens": 1, "output_tokens": 1},
                              "stop_reason": "end_turn", "model": "m"}}],
    }), encoding="utf-8")
    request = TurnRequest(messages=(Message.text(Role.USER, "x"),), tools=(), model="m")
    turn = collect_turn(iter(ReplayBackend(fixture).stream_turn(request)))
    assert turn.text == "hi"
    assert turn.opaque is None
    assert turn.notices == ()


def test_turns_without_opaque_write_no_extra_keys(tmp_path):
    request = TurnRequest(messages=(Message.text(Role.USER, "x"),), tools=(), model="m")
    fixture = tmp_path / "plain.json"
    recorder = RecordingBackend(inner=ScriptedBackend([ModelTurn(text="ok")]),
                                fixture_path=fixture)
    collect_turn(iter(recorder.stream_turn(request)))
    recorder.save()
    entry = json.loads(fixture.read_text(encoding="utf-8"))["entries"][0]["turn"]
    assert "opaque" not in entry and "notices" not in entry
