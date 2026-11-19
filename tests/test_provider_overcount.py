"""A provider's count is a request's size only where the request could hold it (D-215).

Against a local agent gateway (OpenClaw's, 2026-11-19) 229 of 532 requests
were counted at 1.8 to 6 times the most they could hold -- the gateway reports
what its whole run spent, every call it made behind the request -- and the
other 303 at no more than that most. 51 of the 66 compactions in those runs
came while the history being summarised was under the 12,000-token limit,
because the request before had been counted past it; after each the model
read the same files again.
"""

from __future__ import annotations

import json

from foundry.core.context import ContextManager, _most_tokens, schema_chars
from foundry.core.conversation import (
    ImageBlock,
    Message,
    OpaqueBlock,
    Role,
    TextBlock,
    ToolSchema,
    ToolUseBlock,
)
from foundry.core.events import Notice
from foundry.core.images import IMAGE_TOKEN_ESTIMATE


def _context(text: str = "x" * 3_000, **kwargs) -> ContextManager:
    settings = {"system_prompt": "t", "max_context_tokens": 200_000,
                "compact_above_tokens": 12_000, **kwargs}
    context = ContextManager(**settings)
    context.start_turn()
    context.append(Message.text(Role.USER, text))
    return context


# A CJK character, which a provider counts as a token or more.
HAN = chr(0x5B57)

# Three characters to the token, rounded up -- the system prompt's one and the
# user's 3,000 -- and four a message for two messages.
MOST = 1_001 + 8


# --- the most a request could hold ---------------------------------------------------------


def test_the_most_a_request_could_hold():
    assert _most_tokens(_context().project()) == MOST
    # Every character outside ASCII is a token of its own, as CJK text takes.
    assert _most_tokens(_context(HAN * 3_000).project()) == 1 + 3_000 + 8
    # Reasoning bound to the conversation goes back with the request.
    context = _context()
    context.append(Message(role=Role.ASSISTANT,
                           blocks=(OpaqueBlock("anthropic", "r" * 2_997), TextBlock("ok."))))
    assert _most_tokens(context.project()) == 2_001 + 12
    # A tool call's arguments and name.
    context = _context()
    context.append(Message(role=Role.ASSISTANT,
                           blocks=(ToolUseBlock("c1", "read_file", "a" * 2_991),)))
    assert _most_tokens(context.project()) == 2_001 + 12
    # An image is what a provider charges for one.
    context = _context()
    context.append(Message(role=Role.USER, blocks=(ImageBlock("image/png", "AAAA"),)))
    assert _most_tokens(context.project()) == MOST + 4 + IMAGE_TOKEN_ESTIMATE


def test_the_tools_offered_count_too():
    tool = ToolSchema("read_file", "Read a file.", {"type": "object", "properties": {}})
    assert schema_chars([tool, tool]) == 2 * (9 + 12 + len(json.dumps(tool.parameters)))
    context = _context()
    context.tool_chars = 3_000
    context.measured(MOST)
    # What the tools add is part of what the request could hold.
    assert context.measured(MOST + 1_000 + (MOST + 1_000) // 2) is None


# --- which counts stand ----------------------------------------------------------------------


def test_a_count_up_to_half_again_past_it_stands_and_one_more_does_not():
    context = _context()
    assert context.measured(MOST) is None
    assert context.measured(MOST + MOST // 2) is None
    assert context.estimated_context_tokens() == MOST + MOST // 2
    assert context.measured(MOST + MOST // 2 + 1) == MOST
    # The context is sized as the most the request could hold, not as counted.
    assert context.estimated_context_tokens() == MOST


def test_the_first_count_stands_whatever_it_is():
    """There is nothing yet to weigh it against."""
    context = _context()
    assert context.measured(30_000) is None
    assert context.estimated_context_tokens() == 30_000
    # A count the request could hold shows the provider adds nothing; the
    # same count again is then past it.
    assert context.measured(MOST) is None
    assert context.measured(30_000) == MOST


def test_what_a_provider_adds_to_every_request_stands():
    """A gateway's own instructions are in the window too: counted on every
    request, they are part of its size."""
    context = _context()
    assert context.measured(MOST + 15_000) is None
    context.append(Message.text(Role.ASSISTANT, "y" * 3_000))
    grown = MOST + 1_004
    assert context.measured(grown + 15_000) is None
    assert context.estimated_context_tokens() == grown + 15_000
    # Past what it adds by more than half again the request, a count is not
    # the request's size; what it adds still is.
    assert context.measured(3 * grown + 15_000) == grown + 15_000


def test_cjk_text_counted_a_token_a_character_stands():
    context = _context(HAN * 3_000)
    context.measured(2_000)
    assert context.measured(3_400) is None
    assert context.estimated_context_tokens() == 3_400


def test_no_count_changes_nothing():
    context = _context()
    context.measured(MOST)
    assert context.measured(0) is None
    assert context.measured(30_000) == MOST


# --- compaction --------------------------------------------------------------------------------


def test_no_compaction_follows_a_count_past_its_request():
    context = _context()
    context.append(Message.text(Role.ASSISTANT, "on it"))
    context.measured(MOST)
    context.measured(38_000)
    assert not context.needs_compaction()


def test_a_window_s_floor_is_never_a_count_past_its_request():
    """The first request after a compaction is what no compaction removes
    (D-209); a count past it would have compaction wait for nothing."""
    context = _context()
    context.measured(MOST)
    context.replace_history([Message.text(Role.USER, "x" * 3_000),
                             Message.text(Role.USER, "summary")])
    after = MOST + 2 + 4
    assert context.measured(23_000) == after
    assert context.compaction_floor() == after
    assert context.compaction_due() == 12_000


# --- in a session ---------------------------------------------------------------------------


def _token_records(tmp_path) -> list[dict]:
    [journal] = (tmp_path / "sessions").glob("*/events.jsonl")
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    return [e["payload"] for e in events if e["type"] == "token_usage"]


def test_a_gateway_that_counts_its_whole_run_does_not_compact_a_small_context(tmp_path):
    from test_compaction import _summary_requests
    from test_compaction_floor import _session

    runtime, model, events = _session(tmp_path, [3_000, 30_000, 38_000, 3_100])
    runtime.run_turn("read the four files")

    assert _summary_requests(model) == []
    said = [e.text for e in events if isinstance(e, Notice) and "holds at most" in e.text]
    assert len(said) == 1
    assert said[0].startswith("the provider counted 30,000 input tokens for a request that "
                              "holds at most about ")
    records = _token_records(tmp_path)
    # Spent as counted; sized as what the request could hold.
    assert [r["input_tokens"] for r in records] == [3_000, 30_000, 38_000, 3_100, 10]
    assert ["sized_as" in r for r in records] == [False, True, True, False, False]
    assert records[1]["sized_as"] < 4_000
    assert runtime.context.session_usage.input_tokens == 3_000 + 30_000 + 38_000 + 3_100 + 10


def test_the_tools_a_request_offered_are_weighed_with_it(tmp_path):
    from test_compaction_floor import _session

    runtime, model, events = _session(tmp_path, [3_000])
    runtime.run_turn("read the file")
    assert runtime.context.tool_chars == schema_chars(model.seen[0].tools) > 0
