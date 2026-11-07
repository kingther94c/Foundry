"""``foundry mcp get NAME`` (D-105), as ``codex mcp get``: one MCP server's
settings in full, where ``list`` shows only a program and a host.

What carries it: what the listing leaves out is what may hold a secret -- a
server's arguments, its environment, its address's query -- so each is shown
with a value a credential's name marks replaced by ``[hidden]``, and the rest
through the redactor, which takes out what it knows to be one.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app
from foundry.core.redaction import default_redactor

EXIT_FAILED = 12


@pytest.fixture()
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(path))
    monkeypatch.delenv("FOUNDRY_PROFILE", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    path.mkdir()
    return path


def _config(home, text):
    (home / "config.toml").write_text(text, encoding="utf-8")


def _foundry(capsys, *argv):
    code = app.main(list(argv))
    out = capsys.readouterr().out
    return code, " ".join(out.split())


def _json(capsys, *argv):
    assert app.main(["mcp", "get", *argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


DOCS = """[mcp_servers.docs]
command = "C:/tools/uvx.exe"
args = ["our-docs", "--from", "git+https://example.com/docs"]
env = { LOG = "debug" }
env_vars = ["DOCS_HOME"]
read_only = true
"""


# --- what it shows -------------------------------------------------------------------


def test_a_program_in_full(home, capsys):
    _config(home, DOCS + 'enabled_tools = ["search"]\ndisabled_tools = ["delete"]\n'
            "startup_timeout_s = 5\ntool_timeout_s = 30.5\n")
    code, out = _foundry(capsys, "mcp", "get", "docs")
    assert code == 0
    assert out == ("docs (stdio) enabled, read-only "
                   "command C:/tools/uvx.exe "
                   "args our-docs --from git+https://example.com/docs "
                   "env LOG=debug "
                   "passes DOCS_HOME "
                   "tools only search; never delete "
                   "timeouts start 5s, each call 30.5s "
                   "remove foundry mcp remove docs")


def test_a_server_at_an_address(home, capsys):
    _config(home, '[mcp_servers.tickets]\nurl = "https://mcp.example.com/mcp?team=core"\n'
            'bearer_token_env_var = "TICKETS_TOKEN"\nenabled = false\n')
    code, out = _foundry(capsys, "mcp", "get", "tickets")
    assert code == 0
    assert out == ("tickets (http) disabled, may change things "
                   "url https://mcp.example.com/mcp?team=core "
                   "token from TICKETS_TOKEN "
                   "tools all it lists "
                   "timeouts start 20s, each call 120s "
                   "remove foundry mcp remove tickets")


def test_what_a_server_has_not_is_not_said(home, capsys):
    _config(home, '[mcp_servers.bare]\ncommand = "uvx"\n')
    code, out = _foundry(capsys, "mcp", "get", "bare")
    assert code == 0
    assert out == ("bare (stdio) enabled, may change things command uvx tools all it lists "
                   "timeouts start 20s, each call 120s remove foundry mcp remove bare")


def test_no_tools_said_as_much(home, capsys):
    _config(home, '[mcp_servers.bare]\ncommand = "uvx"\nenabled_tools = []\n')
    _, out = _foundry(capsys, "mcp", "get", "bare")
    assert "tools only no tools" in out


def test_args_with_spaces_are_quoted_as_typed(home, capsys):
    _config(home, '[mcp_servers.docs]\ncommand = "uvx"\nargs = ["C:/My Docs/server.py", "-v"]\n')
    _, out = _foundry(capsys, "mcp", "get", "docs")
    assert 'args "C:/My Docs/server.py" -v' in out


def test_as_json(home, capsys):
    _config(home, DOCS)
    assert _json(capsys, "docs") == {
        "name": "docs", "transport": "stdio", "where": "uvx.exe", "enabled": True,
        "read_only": True, "enabled_tools": None, "disabled_tools": [],
        "env_vars": ["DOCS_HOME"], "bearer_token_env_var": None, "startup_timeout_s": 20.0,
        "tool_timeout_s": 120.0, "command": "C:/tools/uvx.exe",
        "args": ["our-docs", "--from", "git+https://example.com/docs"],
        "env": {"LOG": "debug"}, "url": None}


def test_as_json_at_an_address(home, capsys):
    _config(home, '[mcp_servers.tickets]\nurl = "https://mcp.example.com/mcp"\n')
    record = _json(capsys, "tickets")
    assert (record["transport"], record["command"], record["args"], record["env"],
            record["url"]) == ("http", None, [], {}, "https://mcp.example.com/mcp")


def test_as_a_profile_would_see_it(home, capsys):
    _config(home, DOCS + '\n[profiles.quiet.mcp_servers.docs]\ncommand = "uvx"\nenabled = false\n')
    assert _json(capsys, "docs")["enabled"] is True
    assert _json(capsys, "docs", "--profile", "quiet")["enabled"] is False


def test_markup_in_a_setting_is_shown_as_text(home, capsys):
    _config(home, '[mcp_servers.docs]\ncommand = "uvx"\nargs = ["[red]x[/red]", "\\u001b[2Jy"]\n')
    _, out = _foundry(capsys, "mcp", "get", "docs")
    assert "args [red]x[/red]" in out and "\x1b" not in out


# --- what it does not show -----------------------------------------------------------


@pytest.mark.parametrize("args, shown", [
    (["--api-key", "sekrit-1", "-v"], ["--api-key", "[hidden]", "-v"]),
    (["--token=sekrit-1", "x"], ["--token=[hidden]", "x"]),
    (["-Password", "sekrit-1"], ["-Password", "[hidden]"]),
    (["GITHUB_TOKEN=sekrit-1", "run"], ["GITHUB_TOKEN=[hidden]", "run"]),
    (["--secret"], ["--secret"]),
    (["--key", "--other"], ["--key", "[hidden]"]),
    (["--from=git+https://example.com", "--port", "8080"],
     ["--from=git+https://example.com", "--port", "8080"]),
    (["token-store", "--verbose"], ["token-store", "--verbose"]),
    (["--", "--auth", "x"], ["--", "--auth", "x"]),
])
def test_a_value_a_credential_name_marks_is_hidden(home, capsys, args, shown):
    _config(home, '[mcp_servers.docs]\ncommand = "uvx"\nargs = '
            + json.dumps(args) + "\n")
    assert _json(capsys, "docs")["args"] == shown
    _, out = _foundry(capsys, "mcp", "get", "docs")
    assert "sekrit" not in out


def test_an_environment_value_a_credential_name_marks_is_hidden(home, capsys):
    _config(home, '[mcp_servers.docs]\ncommand = "uvx"\n'
            'env = { DOCS_API_KEY = "sekrit-1", region = "eu", db_password = "sekrit-2" }\n')
    assert _json(capsys, "docs")["env"] == {"DOCS_API_KEY": "[hidden]", "region": "eu",
                                            "db_password": "[hidden]"}
    _, out = _foundry(capsys, "mcp", "get", "docs")
    assert "env DOCS_API_KEY=[hidden], db_password=[hidden], region=eu" in out
    assert "sekrit" not in out


@pytest.mark.parametrize("url, shown", [
    ("https://mcp.example.com/mcp?api_key=sekrit-1&team=core",
     "https://mcp.example.com/mcp?api_key=[hidden]&team=core"),
    ("https://mcp.example.com/mcp?team=core&Access%54oken=sekrit-1",
     "https://mcp.example.com/mcp?team=core&Access%54oken=[hidden]"),
    ("https://mcp.example.com/mcp?q=a%20b&flag&token",
     "https://mcp.example.com/mcp?q=a%20b&flag&token"),
    ("https://mcp.example.com/mcp/v1#part", "https://mcp.example.com/mcp/v1#part"),
])
def test_a_query_value_a_credential_name_marks_is_hidden(home, capsys, url, shown):
    _config(home, f'[mcp_servers.tickets]\nurl = "{url}"\n')
    assert _json(capsys, "tickets")["url"] == shown
    _, out = _foundry(capsys, "mcp", "get", "tickets")
    assert f"url {shown}" in out and "sekrit" not in out


def test_what_the_redactor_knows_is_taken_out(home, capsys):
    held = "held-credential-0123"
    default_redactor().register(held)
    shaped = "sk-" + "a" * 24
    _config(home, '[mcp_servers.docs]\ncommand = "C:/' + held + '/uvx"\n'
            f'args = ["--header", "Authorization: Bearer {"b" * 24}", "{shaped}"]\n'
            f'env = {{ LOG = "{held}" }}\n'
            '[mcp_servers.tickets]\n'
            f'url = "https://mcp.example.com/{held}?q={shaped}"\n')
    for name in ("docs", "tickets"):
        code, out = _foundry(capsys, "mcp", "get", name)
        assert code == 0 and "[redacted]" in out
        assert held not in out and shaped not in out and "b" * 24 not in out
        assert held not in json.dumps(_json(capsys, name))


# --- what it refuses -----------------------------------------------------------------


def test_a_name_it_does_not_have_names_those_it_does(home, capsys):
    _config(home, DOCS + '[mcp_servers.alpha]\ncommand = "uvx"\n')
    code, out = _foundry(capsys, "mcp", "get", "dcos")
    assert code == EXIT_FAILED
    assert out == "your configuration has no MCP server named dcos; it names alpha, docs"


def test_with_no_servers_it_says_so(home, capsys):
    code, out = _foundry(capsys, "mcp", "get", "docs")
    assert code == EXIT_FAILED and out == "your configuration has no MCP server named docs"
    assert app.main(["mcp", "get", "docs", "--json"]) == EXIT_FAILED
    assert not capsys.readouterr().out.lstrip().startswith("{")


def test_a_configuration_it_cannot_read_is_said(home, capsys):
    _config(home, "[mcp_servers.docs]\nargs = []\n")
    code, out = _foundry(capsys, "mcp", "get", "docs")
    assert code == EXIT_FAILED and "mcp_servers.docs" in out


def test_a_name_is_required(home, capsys):
    with pytest.raises(SystemExit) as exc:
        app.main(["mcp", "get"])
    assert exc.value.code == 2


# --- found on the way ----------------------------------------------------------------


def test_the_listing_shows_brackets_once_escaped(home, capsys):
    # It escaped what safe() had escaped already, so a [ came out as a
    # backslash and a [.
    _config(home, '[mcp_servers.docs]\ncommand = "uvx"\ndisabled_tools = ["[x]"]\n')
    code, out = _foundry(capsys, "mcp", "list")
    assert code == 0 and "never [x]" in out and "\\" not in out
