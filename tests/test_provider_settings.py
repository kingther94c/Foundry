"""Where a backend's key, headers and query come from (D-084), as codex's
``model_providers``: ``env_key``, ``env_http_headers`` and ``query_params``
in ``[backend]`` or a profile's.

What carries it: a backend that names its variable reads its key only from
there -- never the stored key, which belongs to another provider -- header
values read from variables are scrubbed like a key and left out when unset,
and the query reaches every request, the model list included. None of it can
come from a repository, and none of it is ever a secret in the file.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.cli.wiring import _credential_source, build, request_headers
from foundry.core.auth import CredentialVault, EnvKeySource
from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.base import with_query
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.config import BackendConfig, load_config
from foundry.core.errors import AuthError, ConfigError
from foundry.core.redaction import Redactor, default_redactor

from test_cli_e2e import repo, text_turn  # noqa: F401  (fixture)
from test_mcp import _config

QUERY = {"api-version": "2025-04-01-preview"}


# --- the settings ------------------------------------------------------------------------


def test_the_users_config_sets_them(tmp_path):
    config = load_config(home=_config(tmp_path / "home", (
        '[backend]\nenv_key = "CORP_GATEWAY_KEY"\n'
        'env_http_headers = { "X-Org-Id" = "CORP_ORG_ID" }\n'
        'query_params = { api-version = "2025-04-01-preview" }\n')))
    assert config.backend.env_key == "CORP_GATEWAY_KEY"
    assert config.backend.env_http_headers == {"X-Org-Id": "CORP_ORG_ID"}
    assert config.backend.query_params == QUERY
    assert BackendConfig().env_key is None


def test_a_profile_sets_its_own(tmp_path):
    home = _config(tmp_path / "home", ('[profiles.router.backend]\n'
                                       'base_url = "https://openrouter.ai/api/v1"\n'
                                       'env_key = "OPENROUTER_API_KEY"\n'))
    assert load_config(home=home).backend.env_key is None
    assert load_config(home=home, profile="router").backend.env_key == "OPENROUTER_API_KEY"


@pytest.mark.parametrize("text, message", [
    ('env_key = "sk-live-0123456789abcdef.x"\n',
     "env_key must be the name of an environment variable, not the key itself"),
    ('env_http_headers = { "X Org" = "A" }\n', "env_http_headers must map header names"),
    ('env_http_headers = { "X-Org" = "not a name" }\n', "env_http_headers must map header names"),
    ('query_params = { api-version = 2025 }\n', "query_params must be a table of strings"),
])
def test_a_bad_setting_is_refused_without_echoing_it(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message) as caught:
        load_config(home=_config(tmp_path / "home", "[backend]\n" + text))
    assert "sk-live" not in str(caught.value)


@pytest.mark.parametrize("key", ["env_key", "env_http_headers", "query_params"])
def test_a_repository_may_not_set_them(tmp_path, key):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    value = '"A"' if key == "env_key" else '{ X = "A" }'
    (workspace / ".foundry" / "config.toml").write_text(f"[backend]\n{key} = {value}\n",
                                                        encoding="utf-8")
    with pytest.raises(ConfigError, match=f"may not set connection settings \\({key}\\)"):
        load_config(workspace, home=_config(tmp_path / "home", ""))


# --- the key -----------------------------------------------------------------------------


def test_a_named_variable_is_the_only_place_the_key_comes_from(tmp_path, monkeypatch):
    vault = CredentialVault(tmp_path / "auth.json")
    vault.save({"api_key": "the-stored-openai-key"})
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "the-default-variables-key")
    source = EnvKeySource("OPENROUTER_API_KEY", redactor=Redactor())
    with pytest.raises(AuthError, match="Set OPENROUTER_API_KEY, which backend.env_key names"):
        source.acquire()
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key-0123456789")
    assert source.acquire().reveal() == "router-key-0123456789"
    assert source.redactor.scrub("key router-key-0123456789") == "key [redacted]"
    with pytest.raises(AuthError, match="foundry login stores nothing for it"):
        source.store("x")


@pytest.mark.parametrize("backend", [
    {"env_key": "MY_KEY"},
    {"env_key": "MY_KEY", "protocol": "anthropic_messages"},
    {"env_key": "MY_KEY", "credential_source": "gateway_token"},
])
def test_every_kind_of_backend_reads_the_named_variable(tmp_path, backend):
    config = load_config(home=_config(tmp_path / "home", "[backend]\n" + "".join(
        f'{k} = "{v}"\n' for k, v in backend.items())))
    source = _credential_source(config, CredentialVault(tmp_path / "auth.json"))
    assert isinstance(source, EnvKeySource) and source.env_var == "MY_KEY"


def test_login_says_where_the_key_goes(tmp_path, monkeypatch, capsys):
    home = _config(tmp_path / "home", '[backend]\nenv_key = "MY_KEY"\n')
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    assert app.main(["login"]) == 1
    assert "this backend reads its key from MY_KEY" in " ".join(capsys.readouterr().out.split())


# --- headers and query -------------------------------------------------------------------


def test_header_values_come_from_variables_and_are_scrubbed(monkeypatch):
    monkeypatch.setenv("CORP_ORG_ID", "org-7f3a9c2e5d")
    monkeypatch.delenv("CORP_TEAM", raising=False)
    monkeypatch.setenv("CORP_EMPTY", "")
    backend = BackendConfig(headers={"X-Static": "s"},
                            env_http_headers={"X-Org-Id": "CORP_ORG_ID", "X-Team": "CORP_TEAM",
                                              "X-Empty": "CORP_EMPTY"})
    assert request_headers(backend) == {"X-Static": "s", "X-Org-Id": "org-7f3a9c2e5d"}
    assert default_redactor().scrub("org-7f3a9c2e5d") == "[redacted]"


def test_a_header_value_with_a_line_break_is_refused_unquoted(monkeypatch):
    monkeypatch.setenv("CORP_ORG_ID", "org\r\nX-Evil: 1")
    with pytest.raises(ConfigError, match="CORP_ORG_ID holds a line break") as caught:
        request_headers(BackendConfig(env_http_headers={"X-Org-Id": "CORP_ORG_ID"}))
    assert "Evil" not in str(caught.value)


def test_the_query_goes_after_any_the_url_has():
    assert with_query("https://h/v1/models", {}) == "https://h/v1/models"
    assert with_query("https://h/v1/models", QUERY) == (
        "https://h/v1/models?api-version=2025-04-01-preview")
    assert with_query("https://h/v1/models?limit=1000", {"a": "b c"}) == (
        "https://h/v1/models?limit=1000&a=b+c")


@pytest.mark.parametrize("backend, path", [
    (OpenAICompatBackend, "/v1/chat/completions"),
    (ResponsesBackend, "/v1/responses"),
    (AnthropicBackend, "/v1/messages"),
])
def test_every_protocol_puts_the_query_on_its_requests(backend, path):
    made = backend(base_url="https://h/v1", model="m", query_params=QUERY)
    assert made.endpoint == f"https://h{path}?api-version=2025-04-01-preview"


class _Seen(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self, payload):
        self.server.seen.append((self.command, self.path, {k.lower(): v for k, v in
                                                           self.headers.items()}))
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._answer({"data": [{"id": "fake"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._answer({"model": "fake", "choices": [text_turn("hi")],
                      "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


@pytest.fixture()
def seen_server():
    httpd = HTTPServer(("127.0.0.1", 0), _Seen)
    httpd.seen = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_a_session_sends_them(repo, tmp_path, monkeypatch, seen_server):
    host, port = seen_server.server_address
    monkeypatch.setenv("CORP_GATEWAY_KEY", "corp-key-0123456789")
    monkeypatch.setenv("OPENAI_API_KEY", "not-this-one-0123456789")
    monkeypatch.setenv("CORP_ORG_ID", "org-42")
    home = _config(tmp_path / "home", (
        f'[backend]\nbase_url = "http://{host}:{port}/v1"\nmodel = "fake"\nstream = false\n'
        'env_key = "CORP_GATEWAY_KEY"\nenv_http_headers = { "X-Org-Id" = "CORP_ORG_ID" }\n'
        'query_params = { api-version = "2025-04-01-preview" }\n'))
    wiring = build(repo, home=home, console=Console(file=io.StringIO(), width=100))
    wiring.runtime.run_turn("hello")
    assert wiring.runtime.backend.list_models() == ["fake"]
    wiring.session.close()
    assert [(method, path) for method, path, _ in seen_server.seen] == [
        ("POST", "/v1/chat/completions?api-version=2025-04-01-preview"),
        ("GET", "/v1/models?api-version=2025-04-01-preview")]
    for _, _, headers in seen_server.seen:
        assert headers["authorization"] == "Bearer corp-key-0123456789"
        assert headers["x-org-id"] == "org-42"
