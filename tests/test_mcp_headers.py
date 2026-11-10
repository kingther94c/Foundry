"""A url server's own headers (D-111), as codex's ``http_headers`` and
``env_http_headers``: sent with every request to that server, the second
kind read from the environment variable each one names.

What carries it: a credential never sits in the file -- a header whose name
says it carries one is refused in ``http_headers`` and goes by its variable's
name in ``env_http_headers`` -- and a value read from a variable is sent only
over https or to this machine, as a bearer token is, and registered with the
redactor; no header may speak for the transport, and none is given twice; a
value is never echoed in an error.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app
from foundry.core.config import load_config
from foundry.core.config_mcp import McpServerConfig
from foundry.core.errors import ConfigError
from foundry.core.mcp import StreamableHttpClient
from foundry.core.redaction import Redactor, default_redactor

from test_mcp import _config
from test_mcp_http import Fake, _remote, _run, fake, started  # noqa: F401  (fixtures)

URL = '[mcp_servers.web]\nurl = "https://mcp.example.com/mcp"\n'
VALUE = "kept-out-of-errors-7f3e"


def _load(tmp_path, text):
    return load_config(home=_config(tmp_path / "home", text)).mcp_servers


def _refused(tmp_path, text):
    with pytest.raises(ConfigError) as caught:
        _load(tmp_path, text)
    message = str(caught.value)
    assert VALUE not in message
    return message


# --- the settings ------------------------------------------------------------------------


def test_both_tables_are_read(tmp_path):
    server = _load(tmp_path, URL + 'http_headers = { "X-Region" = "eu", "Accept-Language" = '
                   '"en GB" }\nenv_http_headers = { "X-Api-Key" = "WEB_KEY" }\n')["web"]
    assert server.http_headers == (("Accept-Language", "en GB"), ("X-Region", "eu"))
    assert server.env_http_headers == (("X-Api-Key", "WEB_KEY"),)


def test_unsaid_there_are_none(tmp_path):
    server = _load(tmp_path, URL)["web"]
    assert (server.http_headers, server.env_http_headers) == ((), ())


@pytest.mark.parametrize("table, value", [("http_headers", '{ "X-Region" = "eu" }'),
                                          ("env_http_headers", '{ "X-Api-Key" = "K" }')])
def test_they_are_for_a_server_reached_by_url(tmp_path, table, value):
    message = _refused(tmp_path, f'[mcp_servers.x]\ncommand = "node"\n{table} = {value}\n')
    assert message == f"mcp_servers.x.{table} is for a server reached by url"


def test_an_empty_table_beside_a_command_is_nothing(tmp_path):
    server = _load(tmp_path, '[mcp_servers.x]\ncommand = "node"\nhttp_headers = {}\n'
                   "env_http_headers = {}\n")["x"]
    assert server.command == "node" and server.http_headers == ()


@pytest.mark.parametrize("entry", [
    'http_headers = "X-Region: eu"',
    'http_headers = { "X Region" = "eu" }',
    'http_headers = { "X-Region" = 3 }',
    f'http_headers = {{ "X-Region" = "{VALUE}\\nInjected: yes" }}',
    f'http_headers = {{ "X-Region" = "{VALUE}\\u00e9" }}',
    'http_headers = { "X-Region" = "" }',
    f'http_headers = {{ "X-Region" = "{"a" * 4097}" }}',
])
def test_a_bad_http_headers_is_refused(tmp_path, entry):
    message = _refused(tmp_path, URL + entry + "\n")
    assert message == ("mcp_servers.web.http_headers must map header names to values of "
                       'printable characters, as { "X-Region" = "eu" }')


def test_a_value_of_printable_characters_at_most_4096_is_sent(tmp_path):
    server = _load(tmp_path, URL + f'http_headers = {{ "X-Long" = "{"a" * 4096}" }}\n')["web"]
    assert server.http_headers == (("X-Long", "a" * 4096),)


@pytest.mark.parametrize("entry", [
    f'env_http_headers = {{ "X-Api-Key" = "{VALUE}" }}',
    'env_http_headers = { "X-Api-Key" = 3 }',
    'env_http_headers = { "X Api" = "WEB_KEY" }',
    'env_http_headers = ["WEB_KEY"]',
])
def test_a_bad_env_http_headers_is_refused(tmp_path, entry):
    message = _refused(tmp_path, URL + entry + "\n")
    assert message.startswith("mcp_servers.web.env_http_headers must map header names to "
                              "the names of environment variables")


@pytest.mark.parametrize("header", ["Accept", "content-type", "Content-Length", "Host",
                                    "Mcp-Session-Id", "mcp-protocol-version", "Connection",
                                    "Transfer-Encoding", "Last-Event-ID",
                                    "Proxy-Authorization", "TE", "Upgrade"])
@pytest.mark.parametrize("table, value", [("http_headers", '"x"'),
                                          ("env_http_headers", '"WEB_X"')])
def test_a_header_the_transport_sends_is_refused(tmp_path, header, table, value):
    message = _refused(tmp_path, URL + f'{table} = {{ "{header}" = {value} }}\n')
    assert message == f"mcp_servers.web.{table}: Foundry sends {header} itself"


@pytest.mark.parametrize("header", ["X-Api-Key", "Authorization", "Cookie", "X-Auth-Token",
                                    "X-Client-Secret", "x-openai-org"])
def test_a_credential_never_goes_in_the_file(tmp_path, header):
    message = _refused(tmp_path, URL + f'http_headers = {{ "{header}" = "{VALUE}" }}\n')
    assert message == (f"mcp_servers.web.http_headers: {header} carries a credential, which "
                       "never goes in the file; name the variable that holds it in "
                       "mcp_servers.web.env_http_headers")


def test_by_its_variable_it_does(tmp_path):
    server = _load(tmp_path, URL + 'env_http_headers = { "Authorization" = "WEB_AUTH", '
                   '"Cookie" = "WEB_COOKIE" }\n')["web"]
    assert dict(server.env_http_headers) == {"Authorization": "WEB_AUTH",
                                             "Cookie": "WEB_COOKIE"}


@pytest.mark.parametrize("entry, header, table", [
    ('http_headers = { "X-Region" = "eu" }\nenv_http_headers = { "x-region" = "R" }',
     "x-region", "env_http_headers"),
    ('http_headers = { "X-Region" = "eu", "x-region" = "us" }', "x-region", "http_headers"),
    ('bearer_token_env_var = "WEB_TOKEN"\nenv_http_headers = { "Authorization" = "A" }',
     "Authorization", "env_http_headers"),
])
def test_a_header_is_given_once(tmp_path, entry, header, table):
    message = _refused(tmp_path, URL + entry + "\n")
    assert message == f"mcp_servers.web.{table}: {header} is given twice; give it once"


def test_a_header_from_a_variable_goes_only_over_https_or_to_this_machine(tmp_path):
    message = _refused(tmp_path, '[mcp_servers.web]\nurl = "http://intranet.example/mcp"\n'
                       'env_http_headers = { "X-Api-Key" = "WEB_KEY" }\n')
    assert message == ("mcp_servers.web: a header read from a variable is sent only over "
                       "https, or to this machine, and mcp_servers.web.url is "
                       "http://intranet.example")
    servers = _load(tmp_path, '[mcp_servers.a]\nurl = "http://127.0.0.1:8123/mcp"\n'
                    'env_http_headers = { "X-Api-Key" = "WEB_KEY" }\n'
                    '[mcp_servers.b]\nurl = "http://intranet.example/mcp"\n'
                    'http_headers = { "X-Region" = "eu" }\n')
    assert servers["a"].env_http_headers and servers["b"].http_headers


def test_a_bearer_token_is_still_said_so(tmp_path):
    message = _refused(tmp_path, '[mcp_servers.web]\nurl = "http://intranet.example/mcp"\n'
                       'bearer_token_env_var = "T"\nenv_http_headers = { "X-K" = "K" }\n')
    assert message.startswith("mcp_servers.web: a bearer token is sent only over https")


# --- what is sent ------------------------------------------------------------------------


def _headers(server, header):
    return [h.get(header) for h, _ in server.posts]


def test_they_go_with_every_request(fake, started):
    server = fake()
    found, registry = started(_remote(server, http_headers=(("X-Region", "eu"),),
                                      env_http_headers=(("X-Api-Key", "WEB_KEY"),)),
                              environ={"WEB_KEY": "key-0451"})
    assert found.failures == {}
    _run(registry, "mcp__web__echo", {"text": "a"})
    found.close()
    assert len(server.posts) == 4
    assert set(_headers(server, "x-region")) == {"eu"}
    assert set(_headers(server, "x-api-key")) == {"key-0451"}
    assert [(d["x-region"], d["x-api-key"]) for d in server.deletes] == [("eu", "key-0451")]


def test_an_unset_variable_leaves_its_header_out(fake, started):
    server = fake()
    found, registry = started(_remote(server, http_headers=(("X-Region", "eu"),),
                                      env_http_headers=(("X-Api-Key", "WEB_KEY"),
                                                        ("X-Org", "WEB_ORG"))),
                              environ={"WEB_ORG": "acme", "WEB_KEY": ""})
    assert found.failures == {}
    assert set(_headers(server, "x-api-key")) == {None}
    assert set(_headers(server, "x-org")) == {"acme"}
    assert set(_headers(server, "x-region")) == {"eu"}


def test_a_value_from_a_variable_is_registered_with_the_redactor(fake):
    server = fake()
    redactor = Redactor()
    client = StreamableHttpClient(_remote(server, env_http_headers=(("X-Api-Key", "WEB_KEY"),)),
                                  {"WEB_KEY": "key-0452"}, redactor=redactor)
    client.start()
    try:
        assert redactor.scrub("the server said key-0452") == "the server said [redacted]"
    finally:
        client.close()


def test_a_static_value_is_not_registered(fake):
    server = fake()
    redactor = Redactor()
    client = StreamableHttpClient(_remote(server, http_headers=(("X-Region", "eu-west"),)),
                                  {}, redactor=redactor)
    client.start()
    try:
        assert redactor.scrub("in eu-west") == "in eu-west"
    finally:
        client.close()


@pytest.mark.parametrize("value", [f"{VALUE}\r\nInjected: yes", f"{VALUE}é"])
def test_a_value_a_header_cannot_carry_is_refused_unquoted(fake, started, value):
    server = fake()
    found, _ = started(_remote(server, env_http_headers=(("X-Api-Key", "WEB_KEY"),)),
                       environ={"WEB_KEY": value})
    assert found.failures == {"web": "the value in WEB_KEY has characters the X-Api-Key "
                                     "header cannot carry"}
    assert server.posts == []


def test_the_transports_own_headers_are_foundrys(fake, started):
    """A configuration built in code is not checked as a file is; where
    Foundry sends a header of its own, its own is the one sent."""
    server = fake()
    found, registry = started(_remote(server, http_headers=(("Accept", "text/plain"),
                                                            ("Mcp-Session-Id", "other"))))
    assert found.failures == {}
    _run(registry, "mcp__web__echo", {"text": "a"})
    assert set(_headers(server, "accept")) == {"application/json, text/event-stream"}
    assert _headers(server, "mcp-session-id")[1:] == ["sess-42"] * 3


# --- what `mcp get` shows ----------------------------------------------------------------


@pytest.fixture()
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(path))
    monkeypatch.delenv("FOUNDRY_PROFILE", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    path.mkdir()
    return path


def test_mcp_get_shows_them(home, capsys, monkeypatch):
    monkeypatch.setenv("WEB_KEY", "key-0453")
    (home / "config.toml").write_text(
        URL + 'http_headers = { "X-Region" = "eu" }\n'
        'env_http_headers = { "X-Api-Key" = "WEB_KEY" }\n', encoding="utf-8")
    assert app.main(["mcp", "get", "web"]) == 0
    out = capsys.readouterr().out
    assert "  headers  X-Region: eu, X-Api-Key from WEB_KEY\n" in out
    assert "key-0453" not in out
    assert app.main(["mcp", "get", "web", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["http_headers"] == {"X-Region": "eu"}
    assert shown["env_http_headers"] == {"X-Api-Key": "WEB_KEY"}


def test_mcp_get_takes_out_what_the_redactor_knows(home, capsys):
    held = "held-credential-8910"
    default_redactor().register(held)
    (home / "config.toml").write_text(URL + f'http_headers = {{ "X-Region" = "{held}" }}\n',
                                      encoding="utf-8")
    assert app.main(["mcp", "get", "web"]) == 0
    out = capsys.readouterr().out
    assert held not in out and "X-Region: [redacted]" in out
    assert app.main(["mcp", "get", "web", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["http_headers"] == {"X-Region": "[redacted]"}


def test_mcp_get_says_nothing_of_none(home, capsys):
    (home / "config.toml").write_text(URL, encoding="utf-8")
    assert app.main(["mcp", "get", "web"]) == 0
    assert "headers" not in capsys.readouterr().out
