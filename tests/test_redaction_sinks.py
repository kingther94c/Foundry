"""The three redaction sinks, and the protected-path component check.

redaction.py names three places a credential must be removed: the journal
(including artifacts), events leaving the runtime, and anything assembled into
model context. The third was missing -- the journal copy of a command's output
was scrubbed while the copy sent to the model was not.
"""

from __future__ import annotations

import json

import pytest

from foundry.core.context import ContextManager
from foundry.core.conversation import Role
from foundry.core.policy.engine import check_breaker
from foundry.core.redaction import PLACEHOLDER, Redactor
from foundry.core.session import SessionStore
from foundry.core.tools.base import Operation, ToolKind
from foundry.core.workspace import PathRejected, Workspace

CANARY = "sk-canary-context-0123456789"


def test_tool_output_is_scrubbed_before_entering_model_context():
    redactor = Redactor()
    redactor.register(CANARY)
    context = ContextManager(system_prompt="s", redactor=redactor)
    context.start_turn()
    context.append_tool_result("c1", f"the key is {CANARY} in the log")

    rendered = " ".join(
        block.content for message in context.project() for block in message.blocks
        if hasattr(block, "content")
    )
    assert CANARY not in rendered
    assert PLACEHOLDER in rendered


def test_scrubbing_survives_the_projection_used_for_requests():
    redactor = Redactor()
    redactor.register(CANARY)
    context = ContextManager(system_prompt="s", redactor=redactor)
    context.start_turn()
    context.append_tool_result("c1", CANARY)
    payload = json.dumps([
        [getattr(b, "content", getattr(b, "text", "")) for b in m.blocks]
        for m in context.project()
    ])
    assert CANARY not in payload


def test_journal_and_context_are_scrubbed_by_the_same_registration(tmp_path):
    redactor = Redactor()
    redactor.register(CANARY)

    store = SessionStore(tmp_path / "sessions", redactor=redactor)
    store.append("tool_result", {"content": CANARY})
    store.close()

    context = ContextManager(redactor=redactor)
    context.start_turn()
    context.append_tool_result("c1", CANARY)

    assert CANARY not in store.path.read_text(encoding="utf-8")
    assert CANARY not in context.history[0].blocks[0].content


# --- protected paths are matched per component ---------------------------


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    return Workspace(tmp_path)


@pytest.mark.parametrize("path", [
    ".git/config",
    ".git",                 # the last component IS .git
    "sub/.git",
    "sub/.git/hooks/pre-commit",
    ".foundry",
    ".foundry/config.toml",
    "nested/.foundry",
])
def test_writes_into_protected_directories_are_refused(workspace, path):
    with pytest.raises(PathRejected, match="never permitted"):
        workspace.resolve(path, for_write=True)


def test_ordinary_paths_are_still_writable(workspace):
    assert workspace.resolve("src/app.py", for_write=True).relative == "src/app.py"
    assert workspace.resolve("gitignore.txt", for_write=True).relative == "gitignore.txt"


@pytest.mark.parametrize("path", [".git", "sub/.git", ".foundry", "a/.foundry"])
def test_breaker_matches_protected_paths_by_component(path):
    op = Operation(tool="apply_patch", kind=ToolKind.MUTATOR,
                   args={"paths": [path]}, display="patch", target=path)
    assert check_breaker(op) is not None


def test_breaker_allows_a_file_merely_named_like_one(workspace):
    op = Operation(tool="apply_patch", kind=ToolKind.MUTATOR,
                   args={"paths": ["docs/gitignore.md"]}, display="patch",
                   target="docs/gitignore.md")
    assert check_breaker(op) is None


# --- a multi-file patch is matched per path ------------------------------


def test_rule_matches_every_path_in_a_multi_file_patch():
    from foundry.core.policy.engine import PolicyEngine, Rule, Verdict

    engine = PolicyEngine()
    engine.add_rule(Rule(tool="apply_patch", pattern="secrets/*", verdict=Verdict.DENY,
                         reason="secrets are off limits"))
    op = Operation(tool="apply_patch", kind=ToolKind.MUTATOR,
                   args={"paths": ["src/app.py", "secrets/keys.txt"]},
                   display="patch", target="src/app.py secrets/keys.txt")
    decision, _ = engine.evaluate(op)
    assert decision.verdict is Verdict.DENY
