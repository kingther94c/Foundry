"""Compaction waits where it would free too little (D-209).

Against a local agent gateway (OpenClaw's, 2026-11-18) every request was
counted past the five-step probe's 12,000-token compaction limit, the first
one after a compaction included: the gateway's own prompt, Foundry's
instructions and tools came to some 22,000 tokens before any conversation.
So every round compacted -- 34 compactions in 75 requests -- the model read
the same files again after each, and the task ran out of rounds.

A compaction keeps the instructions, the tools and the user's messages. Where
the provider counted a window's first request (the session's, or the first
after a compaction) past half the threshold, compaction waits until the
context has grown half the threshold past that count.
"""

from __future__ import annotations

import types
from dataclasses import replace

from foundry.cli.slash_commands import _compacts_past
from foundry.core.context import ContextManager
from foundry.core.conversation import Message, Role, Usage
from foundry.core.events import Notice


def _context(**kwargs) -> ContextManager:
    settings = {"system_prompt": "t", "max_context_tokens": 200_000,
                "compact_above_tokens": 12_000, **kwargs}
    context = ContextManager(**settings)
    context.start_turn()
    context.append(Message.text(Role.USER, "build it"))
    context.append(Message.text(Role.ASSISTANT, "on it"))
    return context


# --- the floor -------------------------------------------------------------------------------


def test_the_first_request_s_count_is_the_floor_and_later_ones_do_not_move_it():
    context = _context()
    assert context.compaction_floor() is None and context.compaction_due() == 12_000
    context.measured(22_000)
    assert context.compaction_floor() == 22_000
    assert context.compaction_due() == 28_000
    context.measured(30_000)
    assert context.compaction_floor() == 22_000


def test_a_floor_up_to_half_the_threshold_changes_nothing():
    for floor, due in ((5_000, 12_000), (6_000, 12_000), (6_001, 12_001)):
        context = _context()
        context.measured(floor)
        assert context.compaction_due() == due, floor


def test_a_context_counted_past_the_limit_waits_for_room_to_free():
    context = _context()
    context.measured(22_000)
    assert context.estimated_context_tokens() >= 22_000
    assert not context.needs_compaction()
    context.measured(27_900)
    assert not context.needs_compaction()
    context.measured(28_100)
    assert context.needs_compaction()


def test_a_compaction_starts_a_new_window():
    context = _context()
    context.measured(22_000)
    context.replace_history([Message.text(Role.USER, "summary"),
                             Message.text(Role.ASSISTANT, "next")])
    assert context.compaction_floor() is None and context.compaction_due() == 12_000
    context.measured(9_000)
    assert context.compaction_floor() == 9_000 and context.compaction_due() == 15_000


def test_no_count_sets_no_floor():
    context = _context()
    context.measured(0)
    assert context.compaction_floor() is None
    context.measured(20_000)
    assert context.compaction_floor() == 20_000


def test_a_resumed_session_has_no_floor_until_it_compacts():
    """Its first request carries the history it resumed with, which a
    compaction would summarise: not a floor."""
    context = ContextManager(system_prompt="t", max_context_tokens=200_000,
                             compact_above_tokens=12_000)
    context.restore([Message.text(Role.USER, "build it"), Message.text(Role.ASSISTANT, "x")],
                    [1, 1], 1)
    context.measured(30_000)
    assert context.compaction_floor() is None and context.compaction_due() == 12_000
    assert context.needs_compaction()
    context.replace_history([Message.text(Role.USER, "summary")])
    context.measured(20_000)
    assert context.compaction_floor() == 20_000


def test_never_past_the_point_the_window_counts_as_full():
    context = _context(max_context_tokens=40_000, reserve_output_tokens=8_000)
    context.measured(30_000)
    assert context.compaction_due() == 32_000
    # The window's own share as the threshold, no limit set: the same.
    context = _context(max_context_tokens=20_000, reserve_output_tokens=0,
                       compact_above_tokens=None)
    assert context.compaction_threshold() == 16_000
    context.measured(12_000)
    assert context.compaction_due() == 20_000


# --- in a session ----------------------------------------------------------------------------


def _session(tmp_path, counts):
    from test_compaction import _Model, _core
    from test_resume import call, turn

    work = [replace(turn(call(f"r{i}", "read_file", {"path": f"f{i}.txt"})), usage=Usage(n, 5))
            for i, n in enumerate(counts)]
    model = _Model([*work, turn(text="done")])
    runtime, session, repo = _core(tmp_path, [], max_context=200_000)
    runtime.backend = model
    runtime.context.compact_above_tokens = 12_000
    for i in range(len(counts)):
        (repo / f"f{i}.txt").write_text("small\n", encoding="utf-8")
    events = []
    runtime.events.subscribe(events.append)
    return runtime, model, events


def test_a_gateway_that_counts_every_request_past_the_limit_compacts_once_there_is_room(tmp_path):
    from test_compaction import _summary_requests

    runtime, model, events = _session(tmp_path, [22_000, 23_000, 24_000, 29_000])
    runtime.run_turn("read the four files")

    # Not after every request: once the count passed 28,000.
    assert _summary_requests(model) == [4]
    said = [e.text for e in events if isinstance(e, Notice) and "would free little" in e.text]
    assert said == ["the provider counts 22,000 tokens for what no compaction removes, so "
                    "compacting at 12,000 would free little: compaction waits until the context "
                    "passes 28,000"]


def test_where_the_limit_leaves_room_nothing_is_said(tmp_path):
    from test_compaction import _summary_requests

    runtime, model, events = _session(tmp_path, [4_000, 13_000])
    runtime.run_turn("read the two files")

    assert _summary_requests(model) == [2]
    assert not [e for e in events if isinstance(e, Notice) and "would free little" in e.text]


def test_with_automatic_compaction_off_nothing_is_said(tmp_path):
    from test_compaction import _summary_requests

    runtime, model, events = _session(tmp_path, [22_000, 23_000])
    runtime.auto_compact = False
    runtime.run_turn("read the two files")

    assert _summary_requests(model) == []
    assert not [e for e in events if isinstance(e, Notice) and "would free little" in e.text]


def test_status_names_where_compaction_starts():
    on = types.SimpleNamespace(auto_compact=True)
    context = _context()
    assert _compacts_past(on, context) == "; compacts past 12,000"
    context.measured(22_000)
    assert _compacts_past(on, context) == "; compacts past 28,000 (12,000 would free little here)"
    assert _compacts_past(types.SimpleNamespace(auto_compact=False), context) == ""
    # No limit set and nothing waiting: nothing to say, as before.
    plain = _context(compact_above_tokens=None)
    plain.measured(5_000)
    assert _compacts_past(on, plain) == ""
