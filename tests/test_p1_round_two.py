"""What the second round of P1 found (D-149), each pinned here.

P1 (the roadmap's "After parity") plays the model with a stand-in behind a
local relay, so a model's view of Foundry's requests is read by something
that has to act on them. Its second round found:

* An unattended run offered tools no rule allowed -- an MCP server's tool,
  say -- and every call to one was refused: now the request leaves them out,
  as D-148 leaves a read-only task's mutators out.
* The unattended prompt said files with uncommitted changes "need explicit
  approval to edit", in a run where nobody can give it.
* `list_files` says its path "Defaults to the root", but `{"path": ""}` was
  refused as an empty path; so was `search_text`'s.
* `search_text` stripped each line's indentation, which a patch's SEARCH
  text has to match.
* A command's output reached the model with Windows line ends.
* Nothing said that several calls in one reply run in order.
"""

from __future__ import annotations

import pytest

from foundry.cli.unattended import unattended
from foundry.core.policy.engine import Mode, PolicyEngine, Rule, Verdict
from foundry.core.prompts import base_system_prompt, permissions_paragraph
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import Operation, ReadTracker, ToolContext, ToolKind
from foundry.core.tools.command import CommandResult, combined_output
from foundry.core.tools.files import ListFiles, SearchText
from foundry.core.workspace import Workspace

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)

MCP = "mcp__docs__lookup"


def _allow(tool, pattern="*"):
    return Rule(tool=tool, pattern=pattern, verdict=Verdict.ALLOW)


# --- what the policy could allow ---------------------------------------------


def test_with_someone_to_ask_every_tool_may_be_allowed():
    policy = PolicyEngine()
    assert policy.may_allow(MCP, ToolKind.MUTATOR)
    assert policy.may_allow("run_command", ToolKind.MUTATOR)


@pytest.mark.parametrize("unattended_how", ["not interactive", "dont_ask"])
def test_with_nobody_to_ask_a_tool_no_rule_allows_may_not(unattended_how):
    policy = PolicyEngine()
    if unattended_how == "dont_ask":
        policy.mode = Mode.DONT_ASK
    else:
        policy.interactive = False
    assert not policy.may_allow(MCP, ToolKind.READ_ONLY)
    assert not policy.may_allow("apply_patch", ToolKind.MUTATOR)
    assert not policy.may_allow("run_command", ToolKind.MUTATOR)
    # The built-in rules allow reading, and a read-only sub-agent.
    assert policy.may_allow("read_file", ToolKind.READ_ONLY)
    assert policy.may_allow("spawn_agent", ToolKind.MUTATOR)


def test_an_allow_rule_naming_the_tool_or_a_pattern_of_it_is_enough():
    """Even one whose pattern a given call may not match: that turns on the
    call's arguments, which the request cannot know."""
    policy = PolicyEngine(interactive=False)
    policy.add_rule(_allow("mcp__docs__*"))
    policy.add_rule(_allow("run_command", "python -m pytest*"))
    # A rule that refuses or asks names a tool without allowing it.
    policy.add_rule(Rule(tool="mcp__other__*", pattern="*", verdict=Verdict.DENY))
    policy.add_rule(Rule(tool="apply_patch", pattern="*", verdict=Verdict.ASK))
    assert policy.may_allow(MCP, ToolKind.MUTATOR)
    assert policy.may_allow("run_command", ToolKind.MUTATOR)
    assert not policy.may_allow("mcp__other__lookup", ToolKind.MUTATOR)
    assert not policy.may_allow("apply_patch", ToolKind.MUTATOR)


def test_accept_edits_allows_patches_and_a_grant_its_tool():
    policy = PolicyEngine(interactive=False, mode=Mode.ACCEPT_EDITS)
    assert policy.may_allow("apply_patch", ToolKind.MUTATOR)
    assert not policy.may_allow("run_command", ToolKind.MUTATOR)
    policy.grant_for_session(Operation(tool="run_command", kind=ToolKind.MUTATOR,
                                       args={}, display="x", target="x"))
    assert policy.may_allow("run_command", ToolKind.MUTATOR)


def test_a_read_only_task_may_not_use_a_mutator_even_with_a_rule():
    policy = PolicyEngine()
    policy.add_rule(_allow("run_command"))
    policy.read_only = "a review changes nothing"
    assert not policy.may_allow("run_command", ToolKind.MUTATOR)
    assert policy.may_allow("read_file", ToolKind.READ_ONLY)


# --- what an unattended request offers ---------------------------------------


def _offered(request) -> set[str]:
    return {schema.name for schema in request.tools}


def test_an_unattended_run_is_offered_only_what_a_rule_allows(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="nothing to do")])
    unattended(wiring)
    wiring.runtime.policy.add_rule(_allow("run_command", "python -m pytest*"))
    wiring.runtime.run_turn("check it")
    offered = _offered(wiring.runtime.backend.seen[0])
    assert "run_command" in offered and "read_file" in offered and "finish" in offered
    assert "apply_patch" not in offered


def test_one_with_someone_to_ask_is_offered_everything(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    wiring.runtime.run_turn("check it")
    assert {"apply_patch", "run_command"} <= _offered(wiring.runtime.backend.seen[0])


def test_a_call_to_a_tool_left_out_is_refused_by_the_policy(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("p1", "run_command", {"command": "python -c 1"})),
        turn(text="could not"),
    ])
    unattended(wiring)
    wiring.runtime.run_turn("run it")
    [refused] = [b for m in wiring.runtime.backend.seen[1].messages for b in m.blocks
                 if getattr(b, "call_id", None) == "p1" and hasattr(b, "is_error")]
    assert refused.is_error and "approval is unavailable" in refused.content


# --- the prompt ---------------------------------------------------------------


def test_dirty_files_are_said_to_be_refused_when_nobody_can_approve():
    policy = PolicyEngine(dirty_files={"src/app.py"})
    assert "need explicit approval to edit: src/app.py" in permissions_paragraph(policy)
    policy.interactive = False
    said = permissions_paragraph(policy)
    assert "need explicit approval to edit" not in said
    assert "nobody can give in this run, so it is refused: src/app.py" in said
    assert "so it is refused: src/app.py" in permissions_paragraph(
        PolicyEngine(dirty_files={"src/app.py"}, mode=Mode.DONT_ASK))


def test_the_prompt_says_calls_in_one_reply_run_in_order():
    assert "in the order you give them" in " ".join(base_system_prompt().split())


# --- the tools ----------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(
        "def run():\n    if ready:\n        return go()  \n", encoding="utf-8")
    return ToolContext(workspace=Workspace(root),
                       artifacts=ArtifactStore(tmp_path / "artifacts"),
                       read_tracker=ReadTracker())


def test_an_empty_path_is_the_root(ctx):
    tool = ListFiles()
    assert tool.execute(tool.validate({"path": ""}), ctx).content == "src/app.py"
    search = SearchText()
    assert "src/app.py:3:" in search.execute(
        search.validate({"query": "go", "path": ""}), ctx).content


def test_a_search_keeps_each_line_s_indentation(ctx):
    tool = SearchText()
    found = tool.execute(tool.validate({"query": "go|ready"}), ctx).content
    assert "src/app.py:2:     if ready:" in found
    # Trailing blanks still go.
    assert "src/app.py:3:         return go()\n" in found + "\n"


def test_a_long_indented_line_keeps_200_characters_after_its_indentation(ctx):
    (ctx.workspace.root / "long.py").write_text(" " * 40 + "x" * 300 + "\n", encoding="utf-8")
    tool = SearchText()
    found = tool.execute(tool.validate({"query": "x", "glob": "long.py"}), ctx).content
    line = found.splitlines()[0].split(": ", 1)[1]
    assert line == " " * 40 + "x" * 200


def test_a_command_s_output_reaches_the_model_with_plain_line_ends():
    result = CommandResult(exit_code=1, stdout=b"F.\r\n== FAILURES ==\r\n",
                           stderr=b"warning\r\n", duration_ms=5)
    assert combined_output(result) == "F.\n== FAILURES ==\n\n[stderr]\nwarning\n"
