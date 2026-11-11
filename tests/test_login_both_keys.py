"""``foundry login`` asks for both providers' keys, each skippable (D-182).

Which key a run reads is the configuration's choice, made when it runs, and a
key it lacks is an error then; asking only for the configured one meant a
login made before the configuration was written stored the other provider's
key. What carries it: each key goes to its own field, an answer left empty
stores nothing and keeps what is there, a cancel part-way stores nothing, the
same key at both prompts is refused (one provider's key would reach the
other), a gateway profile is asked for its token only, and no key is shown.
"""

from __future__ import annotations

import getpass
import sys

import pytest

from foundry.cli import app
from foundry.core.auth import CredentialVault
from foundry.core.redaction import Redactor

from test_mcp import _config

OPENAI = "sk-openai-test-0123456789abcdef"
ANTHROPIC = "sk-ant-test-0123456789abcdef"
ANTHROPIC_TOML = '[backend]\nprotocol = "anthropic_messages"\n'


class _Terminal:
    def isatty(self):
        return True


@pytest.fixture()
def home(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FOUNDRY_GATEWAY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    home = _config(tmp_path / "home", "")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    monkeypatch.setattr(sys, "stdin", _Terminal())
    return home


def _answers(monkeypatch, **by_provider):
    """getpass answering each prompt by the provider it names; the prompts
    asked, in order."""
    asked = []

    def answer(prompt=""):
        asked.append(prompt)
        for provider, value in by_provider.items():
            if provider in prompt:
                if isinstance(value, BaseException):
                    raise value
                return value
        return ""

    monkeypatch.setattr(getpass, "getpass", answer)
    return asked


def _login(capsys, *argv):
    code = app.main(["login", *argv])
    return code, " ".join(capsys.readouterr().out.split())


def _stored(home):
    return CredentialVault(home / "auth.json", redactor=Redactor()).load() or {}


def test_both_are_asked_the_configured_one_first(home, monkeypatch, capsys):
    (home / "config.toml").write_text(ANTHROPIC_TOML, encoding="utf-8")
    asked = _answers(monkeypatch, anthropic=ANTHROPIC, openai=OPENAI)
    code, out = _login(capsys)
    assert code == 0
    assert asked == ["anthropic api key (Enter to skip): ", "openai api key (Enter to skip): "]
    stored = _stored(home)
    assert (stored["anthropic_api_key"], stored["api_key"]) == (ANTHROPIC, OPENAI)
    assert "This configuration uses the Anthropic API key" in out
    assert "(Anthropic API key, OpenAI API key)" in out
    assert ANTHROPIC not in out and OPENAI not in out


def test_the_other_provider_s_key_is_taken_before_the_configuration_says(home, monkeypatch,
                                                                          capsys):
    """The case that asked for this: the configuration still names OpenAI
    when the user has only an Anthropic key."""
    asked = _answers(monkeypatch, anthropic=ANTHROPIC)
    code, out = _login(capsys)
    assert code == 0
    assert asked == ["openai api key (Enter to skip): ", "anthropic api key (Enter to skip): "]
    stored = _stored(home)
    assert set(stored) == {"anthropic_api_key", "saved_at"}
    assert stored["anthropic_api_key"] == ANTHROPIC
    assert ("this configuration uses the OpenAI API key, which is not stored: set "
            "OPENAI_API_KEY, or run foundry login again") in out


def test_a_skipped_key_keeps_what_is_stored(home, monkeypatch, capsys):
    CredentialVault(home / "auth.json", redactor=Redactor()).save(
        {"anthropic_api_key": ANTHROPIC})
    _answers(monkeypatch, openai=OPENAI)
    code, out = _login(capsys)
    assert code == 0
    stored = _stored(home)
    assert (stored["anthropic_api_key"], stored["api_key"]) == (ANTHROPIC, OPENAI)
    assert "not stored" not in out


def test_no_warning_when_the_configured_key_is_in_the_environment(home, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI)
    _answers(monkeypatch, anthropic=ANTHROPIC)
    code, out = _login(capsys)
    assert code == 0 and "not stored" not in out


def test_nothing_entered_stores_nothing(home, monkeypatch, capsys):
    _answers(monkeypatch)
    code, out = _login(capsys)
    assert code == 1 and "nothing entered" in out
    assert not (home / "auth.json").exists()


def test_a_cancel_part_way_stores_nothing(home, monkeypatch, capsys):
    _answers(monkeypatch, openai=OPENAI, anthropic=KeyboardInterrupt())
    code, out = _login(capsys)
    assert code == 1 and "cancelled: nothing stored" in out
    assert not (home / "auth.json").exists()


def test_the_same_key_at_both_prompts_is_refused(home, monkeypatch, capsys):
    _answers(monkeypatch, openai=ANTHROPIC, anthropic=ANTHROPIC)
    code, out = _login(capsys)
    assert code == 1 and "the same key at both prompts" in out
    assert not (home / "auth.json").exists()
    assert ANTHROPIC not in out


def test_a_gateway_profile_is_asked_for_its_token_only(home, monkeypatch, capsys):
    (home / "config.toml").write_text('[backend]\ncredential_source = "gateway_token"\n',
                                      encoding="utf-8")
    asked = _answers(monkeypatch, gateway="tok-abcdef123456")
    code, out = _login(capsys)
    assert code == 0 and asked == ["gateway token: "]
    assert set(_stored(home)) == {"token", "saved_at"}
    assert "Enter skips" not in out


def test_status_names_both_and_shows_neither(home, monkeypatch, capsys):
    CredentialVault(home / "auth.json", redactor=Redactor()).save(
        {"api_key": OPENAI, "anthropic_api_key": ANTHROPIC})
    (home / "config.toml").write_text(ANTHROPIC_TOML, encoding="utf-8")
    code, out = _login(capsys, "status")
    assert code == 0
    assert out.startswith("Anthropic API key: stored by foundry login (")
    assert "OpenAI API key, which this configuration does not use: stored by foundry login (" \
        in out
    assert ANTHROPIC not in out and OPENAI not in out
