"""The context is sized by the provider's own count once there is one (D-190).

Probing auto mode against Claude, a 12,000-token compaction limit never fired:
four characters to a token counted the context at 11,000-12,000 where the
provider counted 18,000, as it leaves out the tool definitions and reads code
as wordier than it is.
"""

from __future__ import annotations

from foundry.core.context import ContextManager, _estimate_tokens
from foundry.core.conversation import Message, Role, ToolResultBlock, Usage


def _context(**kwargs) -> ContextManager:
    context = ContextManager(system_prompt="t", max_context_tokens=200_000, **kwargs)
    context.start_turn()
    context.append(Message.text(Role.USER, "x" * 40_000))  # about 10,000 by the estimate
    context.append(Message.text(Role.ASSISTANT, "y" * 4_000))
    return context


def _plain(context: ContextManager) -> int:
    return _estimate_tokens(context.project())


def test_before_any_request_the_estimate_stands_alone():
    context = _context()
    assert context.estimated_context_tokens() == _plain(context)


def test_the_provider_s_count_is_what_the_size_starts_from():
    context = _context()
    sent = _plain(context)
    context.measured(sent + 7_000)
    assert context.estimated_context_tokens() == sent + 7_000
    # What joins the history afterwards is added by the estimate.
    context.append(Message.text(Role.USER, "z" * 4_000))
    assert context.estimated_context_tokens() == _plain(context) + 7_000


def test_a_count_below_the_estimate_never_lowers_it():
    context = _context()
    context.measured(100)
    assert context.estimated_context_tokens() == _plain(context)


def test_no_count_is_no_measurement():
    context = _context()
    context.measured(0)
    assert context.estimated_context_tokens() == _plain(context)


def test_the_limit_the_probe_set_now_fires():
    """Probe F: counted at about 11,000, sent as 18,000, limit 12,000."""
    context = _context(compact_above_tokens=12_000)
    assert _plain(context) < 12_000 and not context.needs_compaction()
    context.measured(18_000)
    assert context.needs_compaction()


def test_the_overflow_check_uses_it_too():
    context = _context(reserve_output_tokens=8_000)
    context.max_context_tokens = 20_000
    assert not context.context_exhausted()
    context.measured(18_000)
    assert context.context_exhausted()


def test_a_compaction_forgets_the_count():
    # The count was mostly of what the summary replaced.
    context = _context(compact_above_tokens=12_000)
    context.measured(18_000)
    context.replace_history([Message.text(Role.USER, "summary")])
    assert context.estimated_context_tokens() == _plain(context)


def test_a_rewind_forgets_the_count():
    context = _context()
    context.measured(_plain(context) + 7_000)
    context.truncate(1)
    assert context.estimated_context_tokens() == _plain(context)


def test_masking_counts_what_the_estimate_missed():
    # Tool output past the masking threshold only once the provider's count is in.
    context = ContextManager(system_prompt="t", max_context_tokens=40_000, mask_after_turns=0,
                             mask_above_fraction=0.5)
    context.start_turn()
    context.append(Message.text(Role.USER, "go"))
    context.append(Message(role=Role.TOOL, blocks=(ToolResultBlock("call-1", "r" * 60_000),)))  # about 15,000
    context.start_turn()
    context.append(Message.text(Role.USER, "more"))
    unmasked = context.project()
    assert unmasked[2].blocks[0].content == "r" * 60_000
    context.measured(_estimate_tokens(unmasked) + 8_000)
    assert context.project()[2].blocks[0].content != "r" * 60_000


# --- in a session ------------------------------------------------------------------------------


def test_a_session_compacts_on_the_provider_s_count(tmp_path):
    """Nothing the estimate would compact; the provider counted past the threshold."""
    from dataclasses import replace

    from test_compaction import _Model, _core, _summary_requests, call, turn

    work = [replace(turn(call("r0", "read_file", {"path": "f0.txt"})), usage=Usage(17_000, 5)),
            turn(text="done")]
    model = _Model(work)
    runtime, session, repo = _core(tmp_path, [], max_context=20_000, reserve=0)
    runtime.backend = model
    (repo / "f0.txt").write_text("small\n", encoding="utf-8")
    assert runtime.context.compaction_threshold() == 16_000
    runtime.run_turn("read f0.txt")
    assert _summary_requests(model) == [1]
    # The count kept is the last work request's, not the summary request's.
    assert runtime.context._measured[0] == 10
