"""/rewind: go back to before one of the user's messages (D-056).

What carries the feature: the model no longer sees the message or anything
after it, while the workspace stays as it is and the model is told so, with
the files it patched after that point; what is kept is unchanged, so its
reasoning stays bound; and the journal keeps the history the rewind left, so
a resume continues from exactly there.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app
from foundry.cli.repl import PROMPT, REWIND_SHOWN, prompt_for, rewind_points
from foundry.core.context import SUMMARY_PREFIX, ContextManager
from foundry.core.conversation import ImageBlock, Message, Role, TextBlock
from foundry.core.errors import ResumeError
from foundry.core.events import TerminalStatus
from foundry.core.resume import read_journal
from foundry.core.session import EventType, SessionStore

from test_resume import PATCH_A, THINKING, _wire, call, repo, turn  # noqa: F401 (fixture)
from test_slash_commands import _repl, _shown

NOTHING_REVERTED = "but nothing in the workspace was reverted"


def _requests(wiring):
    return [[(m.role.value, m.text_content) for m in r.messages[1:]]
            for r in wiring.runtime.backend.seen]


def _history(wiring):
    return [(m.role.value, m.text_content) for m in wiring.runtime.context.history]


def _rewinds(wiring):
    return [r.payload for r in SessionStore.read_records(wiring.session.path)
            if r.type == EventType.REWOUND]


# --- going back ------------------------------------------------------------------


def test_the_last_message_and_what_followed_are_forgotten(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="a1"), turn(text="a2"),
                                             turn(text="a2 again")])
    typist = _repl(wiring, "q1", "q2", "/rewind", "q2, said better")
    assert "went back to before this message" in _shown(typist)
    assert "\nq2\n" in _shown(typist) + "\n"
    last = _requests(wiring)[-1]
    assert last[:2] == [("user", "q1"), ("assistant", "a1")]
    note, task = last[2][1].rsplit("\n\n", 1)
    assert len(last) == 3 and task == "q2, said better"
    assert NOTHING_REVERTED in note and "still changed" not in note


def test_n_goes_further_back_even_to_the_beginning(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="a1"), turn(text="a2")])
    _repl(wiring, "q1", "q2", "/rewind 2")
    assert wiring.runtime.context.history == []
    assert prompt_for(wiring) == PROMPT
    [record] = _rewinds(wiring)
    assert record["keep"] == 0 and record["removed"] == 4 and record["messages"] == []
    assert record["turns"] == [] and record["turn"] == wiring.runtime.context.turn


@pytest.mark.parametrize("arguments", ["0", "x", "-1", "1.5"])
def test_n_counts_from_one(repo, tmp_path, arguments):
    wiring = _wire(repo, tmp_path / "home", [turn(text="a1")])
    typist = _repl(wiring, "q1", f"/rewind {arguments}")
    assert "usage: /rewind [N]" in _shown(typist)
    assert _history(wiring) == [("user", "q1"), ("assistant", "a1")]
    assert _rewinds(wiring) == [] and wiring.note == ""


def test_nothing_to_go_back_to(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    typist = _repl(wiring, "/rewind")
    assert "nothing to go back to" in _shown(typist)
    assert _rewinds(wiring) == [] and wiring.note == ""


def test_not_further_than_there_are_messages(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="a1"), turn(text="a2")])
    typist = _repl(wiring, "q1", "q2", "/rewind 3")
    assert "only 2 of your messages can be gone back to" in _shown(typist)
    assert len(wiring.runtime.context.history) == 4 and _rewinds(wiring) == []


def test_a_long_message_is_shown_cut(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="a1")])
    long = "start " + "x" * (3 * REWIND_SHOWN) + " end"
    typist = _repl(wiring, long, "/rewind")
    shown = [p for p in typist.printed if p.startswith("start ")]
    assert len(shown) == 1 and len(shown[0]) <= REWIND_SHOWN and shown[0].endswith(" end")


# --- what may not be lost --------------------------------------------------------


def test_the_files_patched_after_the_point_are_named(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(text="a1"),
        turn(call("c1", "read_file", {"path": "a.py"})),
        turn(call("c2", "apply_patch", {"patch": PATCH_A})), turn(text="patched"),
        turn(text="a2 again"), turn(text="a3"), turn(text="a4"),
    ])
    _repl(wiring, "q1", "change A", "/rewind")
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"   # not reverted
    assert "Files you patched after that point are still changed: a.py." in wiring.note
    assert not wiring.runtime.tool_ctx.read_tracker.has_read("a.py")
    # Sent with the next message; going back past that message names it again,
    # going back to after it does not.
    _repl(wiring, "something else", "/rewind")
    assert "still changed: a.py." in wiring.note
    _repl(wiring, "again", "more", "/rewind")
    assert NOTHING_REVERTED in wiring.note and "still changed" not in wiring.note


def test_the_files_patched_before_the_point_are_not_named(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "read_file", {"path": "a.py"})),
        turn(call("c2", "apply_patch", {"patch": PATCH_A})), turn(text="patched"),
        turn(text="a2"),
    ])
    _repl(wiring, "change A", "q2", "/rewind")
    assert NOTHING_REVERTED in wiring.note and "still changed" not in wiring.note


def test_a_compaction_starts_the_count_again(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("c1", "read_file", {"path": "a.py"})),
        turn(call("c2", "apply_patch", {"patch": PATCH_A})), turn(text="patched"),
        turn(text="the summary"), turn(text="a2"),
    ])
    typist = _repl(wiring, "change A", "/compact", "q2", "/rewind", "/rewind")
    assert "still changed" not in wiring.note
    assert "nothing to go back to" in _shown(typist)
    assert wiring.runtime.context.history[-1].text_content.startswith(SUMMARY_PREFIX)


def test_the_reasoning_kept_is_still_sent(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="a1", opaque=THINKING),
                                             turn(text="a2", opaque=THINKING),
                                             turn(text="a2 again")])
    _repl(wiring, "q1", "q2", "/rewind", "q2 again")
    last = wiring.runtime.backend.seen[-1].messages
    assert THINKING in last[2].blocks


def test_truncating_keeps_only_what_is_left_of_the_sent_prefix():
    context = ContextManager(system_prompt="s")
    for text in ("u1", "a1", "u2", "a2"):
        context.append(Message.text(Role.USER if text[0] == "u" else Role.ASSISTANT, text))
    context.mark_sent()
    context._unbound = {2, 4}
    context.truncate(2)
    assert [m.text_content for m in context.history] == ["u1", "a1"]
    assert context._turn_of_message == [0, 0]
    assert [m.text_content for m in context._sent] == ["s", "u1", "a1"]
    assert context._unbound == {2}
    context.append(Message.text(Role.USER, "u2'"))
    context.append(Message(role=Role.ASSISTANT, blocks=(TextBlock("a2'"), THINKING)))
    assert THINKING in context.project()[4].blocks
    with pytest.raises(ValueError):
        context.truncate(5)
    with pytest.raises(ValueError):
        context.truncate(-1)


# --- resume ------------------------------------------------------------------------


def test_a_resume_continues_from_where_the_rewind_left(repo, tmp_path):
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(text="a1", opaque=THINKING), turn(text="a2"),
                               turn(text="a2 again")])
    _repl(first, "q1", "q2", "/rewind", "q2 again")
    live = _history(first)
    second = _wire(repo, home, [], resume="last")
    try:
        assert _history(second) == live
    finally:
        app._close(second, TerminalStatus.CANCELLED, "done")


def test_a_resume_right_after_a_rewind_starts_from_what_it_kept(repo, tmp_path):
    home = tmp_path / "home"
    first = _wire(repo, home, [turn(text="a1", opaque=THINKING), turn(text="a2")])
    _repl(first, "q1", "q2", "/rewind")
    kept, sent = first.runtime.context.history, first.runtime.context._sent
    second = _wire(repo, home, [], resume="last")
    try:
        assert second.runtime.context.history == kept
        assert second.runtime.context._turn_of_message == first.runtime.context._turn_of_message
        assert second.runtime.context._sent == sent and len(sent) == 3
        assert second.runtime.context._unbound == set()
    finally:
        app._close(second, TerminalStatus.CANCELLED, "done")


def _corrupt(path, change):
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines):
        record = json.loads(line)
        if record["type"] == EventType.REWOUND:
            change(record["payload"])
            lines[number] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("change", [
    lambda p: p.pop("turns"),
    lambda p: p.pop("messages"),
    lambda p: p.update(turns=p["turns"][:-1]),
    lambda p: p.update(turns=[True, True]),
    lambda p: p.update(turns=[2, 1]),
    lambda p: p.update(turns=[-1, 1]),
    lambda p: p.update(turns=[1, p["turn"] + 1]),
    lambda p: p.update(turns="12"),
], ids=["no turns", "no messages", "short", "bools", "backwards", "negative", "past the round",
        "not a list"])
def test_a_rewind_record_that_does_not_hold_together_is_refused(repo, tmp_path, change):
    wiring = _wire(repo, tmp_path / "home", [turn(text="a1"), turn(text="a2")])
    _repl(wiring, "q1", "q2", "/rewind")
    assert read_journal(wiring.session.path).history[-1].text_content == "a1"
    _corrupt(wiring.session.path, change)
    with pytest.raises(ResumeError, match="a rewind without the history it kept"):
        read_journal(wiring.session.path)


# --- where it can go back to ------------------------------------------------------------


def test_the_points_are_the_users_messages_since_the_last_compaction():
    picture = ImageBlock(media_type="image/png", data=b"png")
    history = [Message.text(Role.USER, "u1"), Message.text(Role.USER, SUMMARY_PREFIX + "s"),
               Message.text(Role.ASSISTANT, "a"), Message(role=Role.USER, blocks=(picture,
                                                                                  TextBlock("u2"))),
               Message.text(Role.TOOL, "result"), Message(role=Role.USER, blocks=(picture,))]
    assert rewind_points(history) == [3]
    assert rewind_points(history[:1]) == [0]
    assert rewind_points([]) == []


def test_the_record_is_synced_as_a_compactions_is():
    from foundry.core.session import _FSYNC_TYPES
    assert EventType.REWOUND in _FSYNC_TYPES


def test_help_lists_it(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "/rewind [N]" in _shown(_repl(wiring, "/help"))
