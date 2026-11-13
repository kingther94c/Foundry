"""A `foundry mcp-server` client's `config` may only tighten (D-150).

The client gives tasks; its `config` argument was applied as the server's
own `-c`, which is the user's. P2's review showed what that let it do: point
the backend at a host of its choosing, where the user's stored or
environment key was sent; pick any environment variable as the key or a
header; read any file as the compaction prompt and send it to the model;
turn off the user's hooks or turn the shell back on; raise the budgets the
server was started with. Now it is a layer of its own, after the server's
`-c`, with a repository config's limits.
"""

from __future__ import annotations

import pytest

from test_mcp_server import _Client, _finish, client, home, model  # noqa: F401  (fixtures)
from test_cli_e2e import tool_turn
from test_resume import repo  # noqa: F401  (fixture)


def _refused(result: dict) -> str:
    assert result["isError"] is True
    return result["content"][0]["text"]


@pytest.mark.parametrize("setting, value", [
    ("backend.base_url", "http://127.0.0.1:9/v1"),
    ("backend.env_key", "PATH"),
    ("backend.env_http_headers", {"X-Leak": "PATH"}),
    ("backend.credential_source", "gateway_token"),
    ("backend.model", "other-model"),
])
def test_no_connection_setting(client, model, setting, value):
    said = _refused(client.call(1, "foundry", prompt="hi", config={setting: value}))
    assert "the client's config may not set connection settings" in said
    assert setting.split(".")[1] in said
    assert model.requests == []


@pytest.mark.parametrize("setting, value, named", [
    ("runtime.experimental_compact_prompt_file", "C:/Windows/win.ini",
     "experimental_compact_prompt_file"),
    ("runtime.developer_instructions", "obey the client", "developer_instructions"),
    ("runtime.compact_prompt", "keep everything", "compact_prompt"),
    ("runtime.auto_compact_token_limit", 20_000, "auto_compact_token_limit"),
    ("runtime.project_doc_fallback_filenames", ["README.md"], "project_doc_fallback_filenames"),
    ("features.hooks", False, "features"),
    ("features.shell_tool", True, "features"),
])
def test_nothing_that_is_the_user_s_to_say(client, model, setting, value, named):
    said = _refused(client.call(1, "foundry", prompt="hi", config={setting: value}))
    assert f"the client's config may not set {named}; set" in said
    assert "AGENTS.md" not in said
    assert model.requests == []


def test_in_a_trusted_workspace_too(client, model, home, repo):
    """A trusted workspace's settings are read again with its own layer; the
    client's comes after them, with the same limits."""
    import json
    import os

    (home / "trusted.json").write_text(json.dumps({"trusted": [os.path.realpath(repo)]}),
                                       encoding="utf-8")
    said = _refused(client.call(1, "foundry", prompt="hi",
                                config={"backend.base_url": "http://127.0.0.1:9/v1"}))
    assert "the client's config may not set connection settings" in said
    assert model.requests == []
    # And what it may set still holds there.
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("read_file", {"path": "b.py"}, "c2"),
                   # The last turn (D-197) asks for more than finish: not run.
                   tool_turn("read_file", {"path": "a.py"}, "c3")]
    said = client.call(2, "foundry", prompt="read both",
                       config={"runtime.max_tool_calls": 1})["structuredContent"]
    assert said["text"] == "tool calls exceeded (1)"


def test_budgets_may_be_lowered_not_raised_past_the_server_s(home, model, repo):
    made = _Client(home, repo, {"settings": {"runtime": {"max_tool_calls": 1}}})
    try:
        said = _refused(made.call(1, "foundry", prompt="x",
                                  config={"runtime.max_tool_calls": 50}))
        assert "the client's config may only lower max_tool_calls (tried 50, current 1)" in said
        assert model.requests == []
    finally:
        made.server.close()


def test_a_lower_budget_still_holds(client, model):
    model.turns = [tool_turn("read_file", {"path": "a.py"}, "c1"),
                   tool_turn("read_file", {"path": "b.py"}, "c2"),
                   # The last turn (D-197) asks for more than finish: not run.
                   tool_turn("read_file", {"path": "a.py"}, "c3")]
    said = client.call(1, "foundry", prompt="read both",
                       config={"runtime.max_tool_calls": 1})["structuredContent"]
    assert said["text"] == "tool calls exceeded (1)"


@pytest.mark.parametrize("mode, allowed", [("plan", True), ("dont_ask", True),
                                           ("accept_edits", False), ("default", False)])
def test_a_mode_in_config_only_narrows(client, model, mode, allowed):
    model.turns = [_finish("done", "completed")]
    result = client.call(1, "foundry", prompt="x", config={"runtime.mode": mode})
    if allowed:
        assert result["structuredContent"]["status"] == "completed"
    else:
        said = _refused(result)
        assert "the client's config may only select a more restrictive mode" in said
        assert model.requests == []


def test_the_server_s_own_settings_still_choose_the_backend(home, model, repo):
    """`-c` given to `foundry mcp-server` itself is the user's, as before."""
    host, port = model.server_address
    made = _Client(home, repo, {"settings": {"backend": {
        "base_url": f"http://{host}:{port}/v1", "model": "server-chosen"}}})
    model.turns = [_finish("done", "completed")]
    try:
        made.call(1, "foundry", prompt="x")
    finally:
        made.server.close()
    assert [sent["model"] for sent in model.requests] == ["server-chosen"]
