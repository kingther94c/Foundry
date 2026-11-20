"""A compaction's summary is a handoff to the next step (D-216).

In a seven-step task on a local agent gateway (r-long, 2026-11-20) each of
eight compactions came in the middle of a step, and after each the model read
the whole working set again before writing anything -- the specification, the
package, every earlier step's tests: 11,000 to 21,000 characters -- then did
about one step and compacted again. Its summaries said what was done, not what
the next step needed, and the note before them said to re-read files. Amp's
handoff carries a goal and the files it needs, opencode's summary the relevant
files (at most fifteen, not every one read), and Claude Code reads up to five
recent files again; this summary names the files the next step opens.
"""

from __future__ import annotations

from foundry.cli.slash_commands import rewind_points
from foundry.core.commands import builtin_prompt
from foundry.core.context import SUMMARY_MARK, SUMMARY_PREFIX, ContextManager, is_summary
from foundry.core.conversation import Message, Role
from test_resume_transcript import END, _fake, _screen

PROMPT = builtin_prompt("compact").strip()

# How every summary began from D-040 until D-216.
BEFORE_D216 = (
    "This conversation was compacted to free context. Earlier replies and tool "
    "output are gone; the user's messages from before it are kept above, and "
    "below is the summary you wrote of the work so far. Re-read a file before "
    "patching it, and run a command again rather than trusting its remembered "
    "output.\n\n"
)


def _sections() -> list[str]:
    return [line for line in PROMPT.splitlines() if line[:1].isdigit() and line[1:2] == "."]


def user(text: str) -> Message:
    return Message.text(Role.USER, text)


# --- what the summary is asked for ----------------------------------------------------------


def test_the_summary_names_the_files_the_next_step_opens():
    sections = _sections()
    assert [s.split(":")[0] for s in sections] == [
        "1. The task", "2. Done so far", "3. Checks", "4. Current state", "5. Next steps",
        "6. Files for the next step", "7. Decisions and dead ends"]
    files = sections[5]
    # A few, with what each is for and which part of it, not every file read.
    assert "at most eight" in files and "which part" in files
    assert "only finished steps needed" in files and "this summary already gives" in files


def test_what_later_steps_need_from_a_file_is_written_down_instead():
    assert "so those files need not be read again" in _sections()[1]


def test_the_summary_does_not_carry_what_it_need_not():
    # The runtime adds the plan after the summary (D-040); a copy is the same twice.
    assert "do not copy it" in _sections()[3]
    # A summary is written from the one before it: finished detail need not grow.
    assert "Where an earlier summary is above, keep from it only what the remaining work " \
           "needs." in PROMPT


def test_after_it_the_model_opens_the_files_it_names_and_reads_again_before_a_patch():
    assert "Open the files it names for the next step" in SUMMARY_PREFIX
    assert "read them again only where the work needs more than the summary gives" \
        in SUMMARY_PREFIX
    assert "Re-read a file before patching it" in SUMMARY_PREFIX
    assert "so far. Re-read a file" not in SUMMARY_PREFIX


# --- a summary written before ---------------------------------------------------------------


def test_a_summary_is_known_by_its_first_sentence():
    assert SUMMARY_PREFIX.startswith(SUMMARY_MARK) and BEFORE_D216.startswith(SUMMARY_MARK)
    assert is_summary(SUMMARY_PREFIX + "s") and is_summary(BEFORE_D216 + "s")
    assert not is_summary("about this: " + SUMMARY_MARK)


def test_one_written_before_d216_is_not_kept_as_a_user_message():
    context = ContextManager(system_prompt="t", max_context_tokens=10_000)
    for message in (user("task"), user(BEFORE_D216 + "old summary"),
                    Message.text(Role.ASSISTANT, "a")):
        context.append(message)
    assert [m.text_content for m in context.compacted_history("new")] == [
        "task", SUMMARY_PREFIX + "new"]


def test_one_written_before_d216_bounds_rewind_and_shows_as_a_summary():
    history = [user("u1"), user(BEFORE_D216 + "s"), Message.text(Role.ASSISTANT, "a"),
               user("u2")]
    assert rewind_points(history) == [3]
    screen = _screen(_fake([user(BEFORE_D216 + "we fixed the parser"), user("next")]))
    assert screen == ["(what came before was compacted into a summary)", "foundry> next", END]
