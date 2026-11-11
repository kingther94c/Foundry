"""``foundry login --with-api-key`` and ``foundry login status`` (D-087), as
codex's: the key piped in (``printenv OPENAI_API_KEY | foundry login
--with-api-key``), and where this configuration's credential comes from.

What carries it: the key is never shown -- not echoed, not quoted back when
refused, not partly in the status -- so the flag refuses a terminal, where
typing it would echo it, and the status names a source, never a value.
"""

from __future__ import annotations

import getpass
import io
import sys

import pytest

from foundry.cli import app
from foundry.cli.login import STDIN_SECRET_LIMIT
from foundry.core.auth import CredentialVault
from foundry.core.redaction import Redactor

from test_mcp import _config

KEY = "sk-test-0123456789abcdefXYZ"
LIMIT = STDIN_SECRET_LIMIT


class _Stdin:
    """What a pipe (or a terminal) looks like to the command."""

    def __init__(self, data: bytes = b"", *, tty: bool = False, text_only: bool = False):
        self._tty = tty
        if text_only:
            self._text = io.StringIO(data.decode("utf-8"))
        else:
            self.buffer = io.BytesIO(data)

    def isatty(self):
        return self._tty

    def read(self, size=-1):
        return self._text.read(size)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FOUNDRY_GATEWAY_TOKEN", "MY_KEY"):
        monkeypatch.delenv(name, raising=False)
    home = _config(tmp_path / "home", "")
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    monkeypatch.setattr(getpass, "getpass",
                        lambda *a, **k: pytest.fail("getpass was reached"))
    return home


def _stored(home):
    return CredentialVault(home / "auth.json", redactor=Redactor()).load() or {}


def _login(monkeypatch, capsys, stdin, *argv):
    monkeypatch.setattr(sys, "stdin", stdin)
    code = app.main(["login", *argv])
    return code, " ".join(capsys.readouterr().out.split())


# --- --with-api-key ----------------------------------------------------------------------


def test_a_piped_key_is_stored_and_not_shown(home, monkeypatch, capsys):
    code, out = _login(monkeypatch, capsys, _Stdin(KEY.encode() + b"\n"), "--with-api-key")
    assert code == 0 and "saved" in out and "OpenAI API key" in out
    assert KEY not in out
    assert _stored(home)["api_key"] == KEY


@pytest.mark.parametrize("data", [
    b"\xef\xbb\xbf" + KEY.encode() + b"\r\n",     # a UTF-8 file with a byte order mark
    b"  " + KEY.encode() + b"\r\n\r\n",
])
def test_line_ends_spaces_and_a_byte_order_mark_are_not_the_key(home, monkeypatch, capsys,
                                                                data):
    code, _ = _login(monkeypatch, capsys, _Stdin(data), "--with-api-key")
    assert code == 0 and _stored(home)["api_key"] == KEY


def test_a_text_only_stdin_is_read_too(home, monkeypatch, capsys):
    code, _ = _login(monkeypatch, capsys, _Stdin(("\ufeff" + KEY + "\n").encode(),
                                                 text_only=True), "--with-api-key")
    assert code == 0 and _stored(home)["api_key"] == KEY


def test_a_terminal_is_refused_where_the_key_would_show(home, monkeypatch, capsys):
    code, out = _login(monkeypatch, capsys, _Stdin(KEY.encode(), tty=True), "--with-api-key")
    assert code == 1 and "reads the OpenAI API key from a pipe" in out
    assert not (home / "auth.json").exists()


@pytest.mark.parametrize("data, said", [
    (b"", "nothing on stdin"),
    (b"\r\n  \r\n", "nothing on stdin"),
    (KEY.encode() + b"\n" + b"second-line\n", "does not look like the OpenAI API key"),
    (b"sk-one two", "does not look like the OpenAI API key"),
    (b"sk-\x07bell", "does not look like the OpenAI API key"),
    (b"a" * (LIMIT + 1), "does not look like the OpenAI API key"),
    (b"\xef\xbb\xbf" + b"a" * (LIMIT - 2), "does not look like the OpenAI API key"),
    (b"sk-\xff\xfe", "cannot read stdin"),
])
def test_what_is_not_a_key_is_refused_unquoted(home, monkeypatch, capsys, data, said):
    code, out = _login(monkeypatch, capsys, _Stdin(data), "--with-api-key")
    assert code == 1 and said in out
    assert "second-line" not in out and "sk-one" not in out and "bell" not in out
    assert not (home / "auth.json").exists()


def test_a_text_only_stdin_is_held_to_the_same_limit(home, monkeypatch, capsys):
    code, out = _login(monkeypatch, capsys, _Stdin(b"a" * (LIMIT + 1), text_only=True),
                       "--with-api-key")
    assert code == 1 and "does not look like the OpenAI API key" in out


def test_a_key_as_long_as_allowed_is_taken(home, monkeypatch, capsys):
    code, _ = _login(monkeypatch, capsys, _Stdin(b"a" * LIMIT), "--with-api-key")
    assert code == 0 and _stored(home)["api_key"] == "a" * LIMIT


@pytest.mark.parametrize("config, field, label", [
    ('[backend]\nprotocol = "anthropic_messages"\n', "anthropic_api_key", "Anthropic API key"),
    ('[backend]\ncredential_source = "gateway_token"\n', "token", "gateway token"),
])
def test_it_stores_what_the_configured_source_reads(home, monkeypatch, capsys, config, field,
                                                    label):
    (home / "config.toml").write_text(config, encoding="utf-8")
    code, out = _login(monkeypatch, capsys, _Stdin(KEY.encode()), "--with-api-key")
    assert code == 0 and label in out
    assert _stored(home) == {field: KEY, "saved_at": _stored(home)["saved_at"]}


def test_a_backend_with_its_own_variable_takes_nothing(home, monkeypatch, capsys):
    (home / "config.toml").write_text('[backend]\nenv_key = "MY_KEY"\n', encoding="utf-8")
    code, out = _login(monkeypatch, capsys, _Stdin(KEY.encode()), "--with-api-key")
    assert code == 1 and "this backend reads its key from MY_KEY" in out
    assert not (home / "auth.json").exists()


def test_without_the_flag_a_pipe_is_pointed_at_it(home, monkeypatch, capsys):
    code, out = _login(monkeypatch, capsys, _Stdin(KEY.encode()))
    assert code == 1 and "pipe it to foundry login --with-api-key" in out
    assert not (home / "auth.json").exists()


# --- status ------------------------------------------------------------------------------


def _status(monkeypatch, capsys):
    return _login(monkeypatch, capsys, _Stdin(b""), "status")


def test_status_names_the_variable_and_not_the_key(home, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    code, out = _status(monkeypatch, capsys)
    assert (code, out) == (0, "OpenAI API key: from OPENAI_API_KEY Anthropic API key, "
                              "which this configuration does not use: none")


def test_status_names_the_store(home, monkeypatch, capsys):
    CredentialVault(home / "auth.json", redactor=Redactor()).save({"api_key": KEY})
    code, out = _status(monkeypatch, capsys)
    assert code == 0 and out.startswith("OpenAI API key: stored by foundry login (")
    assert KEY not in out and KEY[-5:] not in out


def test_status_says_there_is_none(home, monkeypatch, capsys):
    assert _status(monkeypatch, capsys) == (
        1, "no OpenAI API key: set OPENAI_API_KEY, or run foundry login Anthropic API key, "
        "which this configuration does not use: none")


def test_status_reads_the_field_the_configured_source_reads(home, monkeypatch, capsys):
    """An OpenAI key in the store is not an Anthropic one."""
    CredentialVault(home / "auth.json", redactor=Redactor()).save({"api_key": KEY})
    (home / "config.toml").write_text('[backend]\nprotocol = "anthropic_messages"\n',
                                      encoding="utf-8")
    code, out = _status(monkeypatch, capsys)
    assert code == 1 and out.startswith(
        "no Anthropic API key: set ANTHROPIC_API_KEY, or run foundry login OpenAI API key, "
        "which this configuration does not use: stored by foundry login (")
    assert KEY not in out
    (home / "config.toml").write_text('[backend]\ncredential_source = "gateway_token"\n',
                                      encoding="utf-8")
    CredentialVault(home / "auth.json", redactor=Redactor()).save({"token": KEY})
    code, out = _status(monkeypatch, capsys)
    assert code == 0 and out.startswith("gateway token: stored by foundry login (")


def test_status_for_a_backend_with_its_own_variable(home, monkeypatch, capsys):
    CredentialVault(home / "auth.json", redactor=Redactor()).save({"api_key": KEY})
    (home / "config.toml").write_text('[backend]\nenv_key = "MY_KEY"\n', encoding="utf-8")
    assert _status(monkeypatch, capsys) == (
        1, "no key: this backend reads it from MY_KEY (backend.env_key), which is not set")
    monkeypatch.setenv("MY_KEY", KEY)
    assert _status(monkeypatch, capsys) == (0, "key: from MY_KEY")


def test_status_reads_no_key(home, monkeypatch, capsys):
    code, out = _login(monkeypatch, capsys, _Stdin(KEY.encode()), "status", "--with-api-key")
    assert code == 2 and "login status reads no key" in out
    assert not (home / "auth.json").exists()
