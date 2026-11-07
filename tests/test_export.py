"""`/export [PATH]`: the whole conversation as Markdown (D-124), as codex's
/export puts it on the clipboard or saves it to a file.

It is the journal's, from the first message: what a compaction or a rewind
took out of the model's history is still in it, and what came before a
resume. It is shown as it was -- typed words as typed, no control sequences
-- and a file is never written over. The tests never touch the user's
clipboard.
"""

from __future__ import annotations

import pytest

from foundry.cli import exporting
from foundry.cli.copying import CopyFailed
from foundry.cli.exporting import TITLE, conversation_markdown
from foundry.core.session import EventType, JournalRecord

from test_compaction import SUMMARY, _Model
from test_resume import _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown


def _record(kind: str, **payload) -> JournalRecord:
    return JournalRecord(ts="", ordinal=0, type=kind, v=1, payload=payload)


def _clipboard(monkeypatch, fails: str = "") -> list[str]:
    copied: list[str] = []

    def write(text):
        if fails:
            raise CopyFailed(fails)
        copied.append(text)

    monkeypatch.setattr(exporting, "copy_text", write)
    return copied


# --- the Markdown --------------------------------------------------------------------------


def test_each_part_under_its_heading_in_order():
    markdown = conversation_markdown([
        _record(EventType.SESSION_META, workspace="x"),
        _record(EventType.USER_INPUT, text="note\n\nfix a.py", typed="fix a.py"),
        _record(EventType.MODEL_RESPONSE, text="", reasoning=["Looking at **a.py**"]),
        _record(EventType.TOOL_CALL, name="read_file", display="read a.py"),
        _record(EventType.TOOL_CALL, name="apply_patch", display="patch a.py"),
        _record(EventType.MODEL_RESPONSE, text="Fixed:\n\n```py\nA = 2\n```\n"),
        _record(EventType.TOOL_CALL, name="finish", display="finish completed"),
        _record(EventType.TERMINATION, status="completed", summary="A is 2 now."),
    ])
    assert markdown == (
        TITLE
        + "\n## User\n\nfix a.py\n"
        + "\n## Reasoning\n\nLooking at **a.py**\n"
        + "\n## Activity\n\n    -> read a.py\n    -> patch a.py\n"
        + "\n## Assistant\n\nFixed:\n\n```py\nA = 2\n```\n"
        + "\n## Result\n\n**completed**\n\nA is 2 now.\n")


def test_activity_after_a_message_is_a_section_of_its_own():
    markdown = conversation_markdown([
        _record(EventType.TOOL_CALL, name="read_file", display="read a.py"),
        _record(EventType.MODEL_RESPONSE, text="A is 1"),
        _record(EventType.TOOL_CALL, name="read_file", display="read b.py"),
    ])
    assert markdown == (TITLE + "\n## Activity\n\n    -> read a.py\n\n## Assistant\n\nA is 1\n"
                        "\n## Activity\n\n    -> read b.py\n")


def test_reasoning_is_left_out_where_it_was_not_shown():
    markdown = conversation_markdown(
        [_record(EventType.MODEL_RESPONSE, text="hi", reasoning=["thinking"])],
        show_reasoning=False)
    assert markdown == TITLE + "\n## Assistant\n\nhi\n"


def test_what_the_user_did_not_see_is_left_out():
    markdown = conversation_markdown([
        _record(EventType.USER_INPUT, text="go"),
        _record(EventType.USER_INPUT, text="From a Stop hook: more", hook="Stop"),
        _record(EventType.MODEL_RESPONSE, text="the summary", purpose="compact"),
        _record(EventType.MODEL_RESPONSE, text="  ", reasoning=["", " "]),
        _record(EventType.TOOL_CALL, name="read_file", rejected="invalid"),
        _record(EventType.APPROVAL, choice="once"),
    ])
    assert markdown == TITLE + "\n## User\n\ngo\n"


def test_the_user_s_own_commands_and_the_history_s_turns():
    markdown = conversation_markdown([
        _record(EventType.USER_COMMAND, command="git status", exit_code=0),
        _record(EventType.COMPACTED, reason="user"),
        _record(EventType.REWOUND, keep=1),
    ])
    assert markdown == (TITLE + "\n## Activity\n\n    $ git status\n"
                        "    (the conversation so far was compacted into a summary)\n"
                        "    (the conversation went back to an earlier point here)\n")


def test_images_are_named_and_a_result_without_a_summary_gives_its_reason():
    markdown = conversation_markdown([
        _record(EventType.USER_INPUT, text="what is this?", images=[{}, {}]),
        _record(EventType.USER_INPUT, text="", images=[{}]),
        _record(EventType.TERMINATION, status="blocked", summary="", reason="budget spent"),
        _record(EventType.TERMINATION, status="cancelled", summary="", reason=""),
    ])
    assert markdown == (TITLE + "\n## User\n\nwhat is this?\n\n[image 1]\n[image 2]\n"
                        "\n## User\n\n[image 1]\n"
                        "\n## Result\n\n**blocked**\n\nbudget spent\n"
                        "\n## Result\n\n**cancelled**\n")


def test_it_is_as_shown():
    markdown = conversation_markdown([
        _record(EventType.USER_INPUT, text="\x1b]0;title\x07hi\r\n"),
        _record(EventType.MODEL_RESPONSE, text="\n\n  indented\x1b[31m\r\nred\n\n"),
    ])
    assert markdown == TITLE + "\n## User\n\nhi\n\n## Assistant\n\n  indented\nred\n"


def test_nothing_yet_is_none():
    assert conversation_markdown([_record(EventType.SESSION_META)]) is None


# --- /export ---------------------------------------------------------------------------------


def test_export_alone_puts_it_on_the_clipboard(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(call("c1", "read_file", {"path": "a.py"})),
                                             turn(text="A is 1")])
    shown = _shown(_repl(wiring, "what is A?", "/export"))
    assert copied == [TITLE + "\n## User\n\nwhat is A?\n\n## Activity\n\n    -> read a.py\n"
                      "\n## Assistant\n\nA is 1\n"]
    assert "copied the conversation to the clipboard (13 lines)" in shown


def test_a_clipboard_that_cannot_be_written_points_to_a_file(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch, fails="PowerShell exit 1")
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi")])
    shown = _shown(_repl(wiring, "hello", "/export"))
    assert "cannot copy the conversation: PowerShell exit 1" in shown
    assert "/export PATH saves it to a file" in shown
    assert "copied the conversation" not in shown


def test_export_path_saves_it_in_the_workspace(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    (repo / "notes").mkdir()
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi there")])
    shown = _shown(_repl(wiring, "hello", "/export notes/chat.md"))
    saved = repo / "notes" / "chat.md"
    assert saved.read_bytes().decode("utf-8") == (
        TITLE + "\n## User\n\nhello\n\n## Assistant\n\nhi there\n")
    assert f"saved the conversation to {saved} (9 lines)" in shown
    assert copied == []


def test_a_folder_that_is_not_there_says_so(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi there")])
    shown = _shown(_repl(wiring, "hello", "/export notes/chat.md"))
    assert f"cannot write {repo / 'notes' / 'chat.md'}: " in shown
    assert not (repo / "notes").exists()


def test_a_file_is_never_written_over(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch)
    there = tmp_path / "chat.md"
    there.write_text("keep me", encoding="utf-8")
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi")])
    shown = _shown(_repl(wiring, "hello", f"/export {there}"))
    assert f"{there} already exists; /export another name" in shown
    assert there.read_text(encoding="utf-8") == "keep me"


def test_a_path_from_home(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    wiring = _wire(repo, tmp_path / "home", [turn(text="hi")])
    _repl(wiring, "hello", "/export ~/chat.md")
    assert (tmp_path / "chat.md").read_text(encoding="utf-8").startswith(TITLE)


def test_reasoning_the_session_does_not_show_is_not_exported(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.show_reasoning = False
    wiring.session.append(EventType.MODEL_RESPONSE, {"text": "hi", "reasoning": ["thinking"]})
    _repl(wiring, "/export")
    assert copied == [TITLE + "\n## Assistant\n\nhi\n"]


def test_nothing_to_export_yet(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [])
    assert "there is no conversation to export yet" in _shown(_repl(wiring, "/export"))
    assert copied == []


def test_what_a_compaction_took_is_still_there(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [])
    wiring.runtime.backend = _Model([turn(text="A is 1"), turn(text="B is 1")])
    _repl(wiring, "what is A?", "/compact", "and B?", "/export")
    [markdown] = copied
    assert markdown == (TITLE + "\n## User\n\nwhat is A?\n\n## Assistant\n\nA is 1\n"
                        "\n## Activity\n\n    (the conversation so far was compacted into a "
                        "summary)\n\n## User\n\nand B?\n\n## Assistant\n\nB is 1\n")
    assert SUMMARY not in markdown


def test_a_resumed_session_exports_what_came_before_too(repo, tmp_path, monkeypatch):
    copied = _clipboard(monkeypatch)
    home = tmp_path / "home"
    _repl(_wire(repo, home, [turn(text="first answer")]), "first question")
    again = _wire(repo, home, [turn(text="second answer")], resume="last")
    _repl(again, "second question", "/export")
    [markdown] = copied
    order = ["first question", "first answer", "second question", "second answer"]
    assert [markdown.index(part) for part in order] == sorted(
        markdown.index(part) for part in order)
    # What the user typed, not the note a resume put before it.
    assert "\n## User\n\nsecond question\n" in markdown


def test_help_lists_it(repo, tmp_path):
    assert "/export [PATH]" in _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))
