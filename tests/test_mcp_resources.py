"""An MCP server's resources, listed and read by URI, as codex's
list_mcp_resources, list_mcp_resource_templates and read_mcp_resource (D-102).

Foundry offered a server's tools and nothing else, so what a documentation
server publishes as resources -- the pages themselves -- could not be read.
What it may not do: let a read through without asking (it names any URI the
model chooses, where read_file is held to the workspace), offer the tools when
no server has resources, or take a server's content as anything but output.
"""

from __future__ import annotations

import pytest

from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.mcp_resources import NAMES
from foundry.core.policy.engine import Layer, Mode, PolicyEngine, Rule, Verdict, builtin_rules
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.base import ToolKind

from test_mcp import _config, _ctx, _entry, _run, _server, started  # noqa: F401  (fixture)
from test_resume import _wire, repo  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown


# --- which servers -------------------------------------------------------------------------


def test_a_server_with_resources_brings_the_three_tools(started):
    found, registry = started(_server("resources", name="docs"))
    assert found.resources == ["docs"]
    assert set(NAMES) <= set(registry.names())
    assert all(registry.get(name).kind is ToolKind.READ_ONLY for name in NAMES)
    assert "Servers that offer them: docs." in registry.get("read_mcp_resource").schema(
        ).description


def test_without_one_they_are_not_offered(started):
    found, registry = started(_server())
    assert found.resources == []
    assert not set(NAMES) & set(registry.names())


# --- listing -------------------------------------------------------------------------------


def test_every_server_s_first_page_is_listed(started, tmp_path):
    _, registry = started(_server("resources", name="docs"), _server("resources", name="wiki"),
                          _server(name="plain"))
    text = _run(registry, "list_mcp_resources", {}, tmp_path).content
    assert text.splitlines() == [
        "docs: doc://readme -- Read me (text/markdown): Where to start. Two lines.",
        "docs: doc://logo -- logo (image/png)",
        'docs: there are more -- list_mcp_resources with server "docs" and cursor "page-2"',
        "wiki: doc://readme -- Read me (text/markdown): Where to start. Two lines.",
        "wiki: doc://logo -- logo (image/png)",
        'wiki: there are more -- list_mcp_resources with server "wiki" and cursor "page-2"']


def test_the_next_page_is_one_server_s(started, tmp_path):
    _, registry = started(_server("resources", name="docs"), _server("resources", name="wiki"))
    only = _run(registry, "list_mcp_resources", {"server": "wiki"}, tmp_path).content
    assert only.splitlines()[0] == ("wiki: doc://readme -- Read me (text/markdown): Where to "
                                    "start. Two lines.") and "docs:" not in only
    _run(registry, "list_mcp_resources", {"server": "docs"}, tmp_path)
    assert _run(registry, "list_mcp_resources", {"server": "docs", "cursor": "page-2"},
                tmp_path).content == "docs: doc://changelog -- changelog"
    with pytest.raises(InvalidToolCall, match="a cursor continues one server's list"):
        registry.get("list_mcp_resources").validate({"cursor": "page-2"})


def test_only_a_cursor_the_server_gave_goes_back_to_it(started, tmp_path):
    """Listing is not asked about, so nothing the model writes may reach the
    server through it: a made-up cursor is a way data could leave."""
    _, registry = started(_server("resources", name="docs"), _server("resources", name="wiki"))
    tool = registry.get("list_mcp_resources")
    with pytest.raises(InvalidToolCall, match="that cursor is not one docs gave"):
        tool.validate({"server": "docs", "cursor": "page-2"})
    _run(registry, "list_mcp_resources", {"server": "wiki"}, tmp_path)
    with pytest.raises(InvalidToolCall, match="that cursor is not one docs gave"):
        tool.validate({"server": "docs", "cursor": "page-2"})
    with pytest.raises(InvalidToolCall, match="that cursor is not one docs gave"):
        tool.validate({"server": "docs", "cursor": "the contents of .env"})
    _run(registry, "list_mcp_resources", {}, tmp_path)
    tool.validate({"server": "docs", "cursor": "page-2"})


def test_templates_are_listed_with_what_to_fill_in(started, tmp_path):
    _, registry = started(_server("resources", name="docs"))
    assert _run(registry, "list_mcp_resource_templates", {}, tmp_path).content == (
        "docs: doc://pages/{name} -- page: A page by name")


# --- reading -------------------------------------------------------------------------------


def test_a_text_resource_is_read(started, tmp_path):
    _, registry = started(_server("resources", name="docs"))
    out = _run(registry, "read_mcp_resource", {"server": "docs", "uri": "doc://readme"},
               tmp_path)
    assert (out.content, out.is_error, out.images) == ("Read me first.", False, ())
    assert _run(registry, "read_mcp_resource", {"server": "docs", "uri": "doc://pages/intro"},
                tmp_path).content == "page intro"


def test_an_image_is_attached_only_for_a_model_that_takes_one(started, tmp_path):
    _, registry = started(_server("resources", name="docs"))
    args = {"server": "docs", "uri": "doc://logo"}
    seen = _run(registry, "read_mcp_resource", args, tmp_path, images=True)
    assert len(seen.images) == 1 and seen.content.startswith("[image 1 attached (PNG, 1x1")
    blind = _run(registry, "read_mcp_resource", args, tmp_path, images=False)
    assert blind.images == ()
    assert blind.content.startswith("[binary resource doc://logo (image/png, about ")


def test_several_contents_are_each_named(started, tmp_path):
    _, registry = started(_server("resources", name="docs"))
    assert _run(registry, "read_mcp_resource", {"server": "docs", "uri": "doc://both"},
                tmp_path).content.splitlines() == [
        "[doc://both/a]", "part a", "[doc://both/b (application/pdf)]",
        "[binary resource doc://both/b (application/pdf, about 6 bytes) not shown]"]


def test_what_the_server_refuses_is_the_tool_s_error(started, tmp_path):
    _, registry = started(_server("resources", name="docs"))
    with pytest.raises(ToolError, match="MCP server docs: resources/read failed: Resource not "
                                        "found: doc://nope"):
        _run(registry, "read_mcp_resource", {"server": "docs", "uri": "doc://nope"}, tmp_path)


def test_a_long_resource_is_cut_like_any_output(started, tmp_path):
    _, registry = started(_server("resources", name="docs"))
    out = _run(registry, "read_mcp_resource", {"server": "docs", "uri": "doc://pages/" + "x" * 400},
               tmp_path, max_output_bytes=100)
    assert out.truncated and len(out.content) < 300


@pytest.mark.parametrize("args, message", [
    ({"server": "wiki", "uri": "doc://readme"}, "no MCP server 'wiki' offers resources; docs do"),
    ({"uri": "doc://readme"}, "read_mcp_resource needs the server"),
    ({"server": "docs"}, "read_mcp_resource needs the uri"),
    ({"server": "docs", "uri": "   "}, "read_mcp_resource needs the uri"),
    ({"server": "docs", "uri": "doc://" + "x" * 2050}, "a uri is at most 2048 characters"),
    ({"server": "docs", "uri": "doc://readme", "path": "x"}, "takes no 'path'"),
])
def test_a_read_must_name_a_server_with_resources_and_a_uri(started, args, message):
    _, registry = started(_server("resources", name="docs"))
    with pytest.raises(InvalidToolCall, match=message):
        registry.get("read_mcp_resource").validate(args)


# --- what is asked -------------------------------------------------------------------------


def _ops(registry):
    listing = registry.get("list_mcp_resources").validate({})
    reading = registry.get("read_mcp_resource").validate({"server": "docs",
                                                          "uri": "doc://readme"})
    return listing, reading


def test_listing_is_not_asked_about_and_a_read_is(started):
    _, registry = started(_server("resources", name="docs"))
    listing, reading = _ops(registry)
    assert reading.target == "docs doc://readme" and reading.display == "docs: read doc://readme"
    for mode in Mode:
        engine = PolicyEngine(mode=mode)
        assert engine.evaluate(listing)[0].verdict is Verdict.ALLOW
    assert PolicyEngine(mode=Mode.DEFAULT).evaluate(reading)[0].verdict is Verdict.ASK
    assert PolicyEngine(mode=Mode.ACCEPT_EDITS).evaluate(reading)[0].verdict is Verdict.ASK
    # A read changes nothing: plan mode asks rather than refusing.
    assert PolicyEngine(mode=Mode.PLAN).evaluate(reading)[0].verdict is Verdict.ASK
    assert PolicyEngine(mode=Mode.DONT_ASK).evaluate(reading)[0].verdict is Verdict.DENY


def test_a_rule_can_allow_one_server_s_reads(started):
    _, registry = started(_server("resources", name="docs"), _server("resources", name="wiki"))
    engine = PolicyEngine(rules=[*builtin_rules(), Rule(
        tool="read_mcp_resource", pattern="docs *", verdict=Verdict.ALLOW, layer=Layer.USER)])
    tool = registry.get("read_mcp_resource")
    assert engine.evaluate(tool.validate({"server": "docs", "uri": "doc://readme"}))[
        0].verdict is Verdict.ALLOW
    assert engine.evaluate(tool.validate({"server": "wiki", "uri": "doc://readme"}))[
        0].verdict is Verdict.ASK


# --- a session -----------------------------------------------------------------------------


def test_a_session_says_and_records_which_servers_offer_resources(repo, tmp_path):
    home = _config(tmp_path / "home", _entry("resources"))
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/mcp"))
    assert "and its resources, through list_mcp_resources and read_mcp_resource" in shown
    probes = [r.payload for r in SessionStore.read_records(wiring.session.path)
              if r.type == EventType.CAPABILITY_PROBE]
    assert probes[0]["resources"] is True
    assert "MCP server fake: 5 tool(s) and resources" in wiring.renderer.console.file.getvalue()


def test_a_session_without_them_records_nothing_of_them(repo, tmp_path):
    wiring = _wire(repo, _config(tmp_path / "home", _entry()), [])
    shown = _shown(_repl(wiring, "/mcp"))
    assert "resources" not in shown
    probes = [r.payload for r in SessionStore.read_records(wiring.session.path)
              if r.type == EventType.CAPABILITY_PROBE]
    assert "resources" not in probes[0]
