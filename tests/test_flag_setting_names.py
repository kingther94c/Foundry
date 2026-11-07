"""`-c NAME=...` that Foundry does not read, said with where it belongs, as a
file's key is (D-098).

codex's own `-c model_reasoning_effort=high` -- the way its documentation sets
a setting for one run -- was refused with `-c: no setting
'model_reasoning_effort'` and nothing more, while the same key in a file was
told Foundry's name (D-091). What it may not do: take a name it did not take
before, or say the value, which may be a secret pasted by mistake.
"""

from __future__ import annotations

import pytest

from foundry.cli import app
from foundry.core.config import _CALLED_HERE, load_config, parse_settings
from foundry.core.errors import ConfigError

from test_anthropic_wiring import _home


def _told(item):
    with pytest.raises(ConfigError) as caught:
        parse_settings([item])
    return str(caught.value)


@pytest.mark.parametrize("codex, foundry", sorted(_CALLED_HERE.items()))
def test_codex_s_name_is_told_foundry_s(codex, foundry):
    assert _told(f"{codex}=1") == f"-c: no setting {codex!r}; Foundry calls it {foundry}"


@pytest.mark.parametrize("item, told", [
    ("sandbox_mode=read-only", "Foundry has no sandbox; runtime.mode decides"),
    ("sandbox_workspace_write.network_access=true", "Foundry has no sandbox"),
    ("model_provider=oss", "Foundry has no model providers"),
    ("model_providers.oss.base_url=http://localhost:11434/v1",
     "Foundry has no model providers"),
    ("proxy=http://p:8080", "the proxy comes from HTTPS_PROXY"),
    ("profile=fast", "choose one with -p NAME"),
    ("backend.max_tool_rounds=5", "it is runtime.max_tool_rounds"),
    ("runtime.model=gpt-5", "it is backend.model"),
    ("permissions.tool=x", "-c sets backend and runtime settings; [permissions] goes in "
                           "your config.toml"),
    ("mcp_servers.docs.command=uvx", "[mcp_servers] goes in your config.toml"),
    ("shell_environment_policy.inherit=all", "[shell_environment_policy] goes in"),
    ("max_tool_round=5", "did you mean 'max_tool_rounds'?"),
    ("backend.modle=x", "did you mean 'backend.model'?"),
    ("runtime.max_tool_round=5", "did you mean 'runtime.max_tool_rounds'?"),
])
def test_where_it_belongs(item, told):
    message = _told(item)
    assert message.startswith(f"-c: no setting {item.partition('=')[0]!r}; ")
    assert told in message


def test_nothing_to_say_says_nothing_more():
    assert _told("zzzzzz=1") == "-c: no setting 'zzzzzz'"


def test_the_value_is_never_said():
    for item in ("model_reasoning_effort=sk-not-a-real-key-123",
                 "sandbox_mode=sk-not-a-real-key-123", "max_tool_round=sk-not-a-real-key-123"):
        assert "sk-not-a-real-key" not in _told(item)


@pytest.mark.parametrize("item", ["backend.name=x", "name=x"])
def test_a_backend_s_name_is_still_not_one_c_sets(item):
    assert _told(item).startswith(f"-c: no setting {item.partition('=')[0]!r}")


def test_what_it_took_it_still_takes(tmp_path):
    config = load_config(home=_home(tmp_path, ""), settings=parse_settings(
        ["model=gpt-5", "backend.effort=high", "max_tool_rounds=7", "runtime.mode=plan"]))
    assert (config.backend.model, config.backend.effort, config.max_tool_rounds,
            config.mode.value) == ("gpt-5", "high", 7, "plan")


def test_a_file_s_codex_sandbox_table_is_told_too(tmp_path):
    with pytest.raises(ConfigError, match="no setting 'sandbox_workspace_write'; Foundry has "
                                          "no sandbox"):
        load_config(home=_home(tmp_path, "[sandbox_workspace_write]\nnetwork_access = true\n"))


def test_a_run_is_refused_with_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path))
    assert app.main(["exec", "-c", "model_reasoning_effort=high", "say ok"]) == 12
    assert "Foundry calls it backend.effort" in capsys.readouterr().err
