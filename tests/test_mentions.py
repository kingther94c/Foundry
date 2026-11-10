"""`@path` in a typed message attaches the file (D-058).

What carries the feature: the file goes with the message as read_file would
show it, and counts as read; only a file in the workspace is attached, a rule
refusing to read it refuses this too, and what is attached is scrubbed. Only
what the user types is looked at: a slash command's prompt is not.
"""

from __future__ import annotations

import sys

import pytest

from foundry.cli.attachments import MAX_MENTIONS
from foundry.core.policy.engine import Rule, Verdict
from foundry.core.redaction import PLACEHOLDER, default_redactor
from foundry.core.tools.files import DEFAULT_READ_LINES

from test_resume import _wire, repo, turn  # noqa: F401 (fixture)
from test_slash_commands import _repl, _sent, _shown

NOTE = "The user attached this file with @, as read_file shows it:\n"
SECRET = "mention-secret-0123456789abcdef"


def _sent_once(repo, tmp_path, message, setup=None):
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    if setup:
        setup(wiring)
    typist = _repl(wiring, message)
    [sent] = _sent(wiring)
    return wiring, typist, sent


# --- what is attached --------------------------------------------------------------


def test_the_file_goes_with_the_message_as_read_file_shows_it(repo, tmp_path):
    wiring, typist, sent = _sent_once(repo, tmp_path, "what is in @a.py?")
    assert sent == f"what is in @a.py?\n\n{NOTE}a.py (1 lines total)\n     1\tA = 1"
    assert "attached a.py" in _shown(typist)
    assert wiring.runtime.tool_ctx.read_tracker.has_read("a.py")


def test_punctuation_after_it_ends_the_sentence_not_the_path(repo, tmp_path):
    _, _, sent = _sent_once(repo, tmp_path, "see @b.py, then (@a.py).")
    first, second = sent.split(NOTE)[1:]
    assert first.startswith("b.py (") and second.startswith("a.py (")


def test_a_quoted_path_may_hold_spaces_and_a_folder_its_separator(repo, tmp_path):
    (repo / "my notes.txt").write_text("hello\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    _, _, sent = _sent_once(repo, tmp_path, 'read @"my notes.txt" and @src/x.py')
    assert f"{NOTE}my notes.txt (1 lines total)" in sent
    assert f"{NOTE}src/x.py (1 lines total)" in sent


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows path")
def test_a_backslash_path_is_the_same_file(repo, tmp_path):
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    _, _, sent = _sent_once(repo, tmp_path, "read @src\\x.py and @src/x.py")
    assert sent.count(NOTE) == 1 and f"{NOTE}src/x.py (1 lines total)" in sent


def test_a_file_named_twice_goes_once(repo, tmp_path):
    _, _, sent = _sent_once(repo, tmp_path, "@a.py and again @a.py")
    assert sent.count(NOTE) == 1


def test_a_long_file_goes_as_its_first_window(repo, tmp_path):
    (repo / "long.txt").write_text("".join(f"line {n}\n" for n in range(1, 301)),
                                   encoding="utf-8")
    _, _, sent = _sent_once(repo, tmp_path, "@long.txt")
    assert f"line {DEFAULT_READ_LINES}\n" in sent and f"line {DEFAULT_READ_LINES + 1}\n" not in sent
    assert sent.endswith(f"[showing lines 1-{DEFAULT_READ_LINES}; "
                         f"{300 - DEFAULT_READ_LINES} more below]")


def test_at_most_ten_go_with_one_message(repo, tmp_path):
    for number in range(MAX_MENTIONS + 1):
        (repo / f"f{number}.txt").write_text(f"{number}\n", encoding="utf-8")
    message = " ".join(f"@f{number}.txt" for number in range(MAX_MENTIONS + 1))
    _, typist, sent = _sent_once(repo, tmp_path, message)
    assert sent.count(NOTE) == MAX_MENTIONS
    assert f"f{MAX_MENTIONS}.txt (" not in sent
    assert f"@f{MAX_MENTIONS}.txt does not" in _shown(typist)


# --- what is not ---------------------------------------------------------------------


@pytest.mark.parametrize("message, said", [
    ("what does @property do?", False),
    ("mail k@a.py about it", False),
    ("see @missing.py", True),
    ('see @"no such file"', True),
    ("see @docs/", True),
])
def test_what_is_not_a_file_here_is_sent_as_written(repo, tmp_path, message, said):
    _, typist, sent = _sent_once(repo, tmp_path, message)
    assert sent == message
    assert ("no such file in the workspace; sent as written" in _shown(typist)) is said


def test_a_folder_is_not_a_file(repo, tmp_path):
    (repo / "src").mkdir()
    _, typist, sent = _sent_once(repo, tmp_path, "look in @src/")
    assert sent == "look in @src/"
    assert "@src/: no such file in the workspace; sent as written" in _shown(typist)


def test_a_file_outside_the_workspace_is_not_attached(repo, tmp_path):
    (tmp_path / "outside.txt").write_text("private\n", encoding="utf-8")
    message = f"@../outside.txt and @{tmp_path / 'outside.txt'}"
    _, typist, sent = _sent_once(repo, tmp_path, message)
    assert sent == message and "private" not in sent
    assert _shown(typist).count("no such file in the workspace") == 2


def test_a_rule_that_refuses_reading_it_refuses_this(repo, tmp_path):
    def deny(wiring):
        wiring.runtime.policy.rules.append(
            Rule(tool="read_file", pattern="a.py", verdict=Verdict.DENY, reason="private"))

    wiring, typist, sent = _sent_once(repo, tmp_path, "@a.py and @b.py", deny)
    assert NOTE + "a.py" not in sent and NOTE + "b.py" in sent
    assert "@a.py: not attached, the policy refuses reading it (private)" in _shown(typist)
    assert not wiring.runtime.tool_ctx.read_tracker.has_read("a.py")


def test_a_rule_that_would_ask_is_answered_by_typing_it(repo, tmp_path):
    def ask(wiring):
        wiring.runtime.policy.rules.append(
            Rule(tool="read_file", pattern="a.py", verdict=Verdict.ASK, reason="check"))

    _, _, sent = _sent_once(repo, tmp_path, "@a.py", ask)
    assert NOTE + "a.py" in sent


def test_a_binary_file_is_not_attached(repo, tmp_path):
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
    _, typist, sent = _sent_once(repo, tmp_path, "@blob.bin")
    assert sent == "@blob.bin"
    assert "@blob.bin: not attached, blob.bin appears to be binary" in _shown(typist)


def test_what_is_attached_is_scrubbed(repo, tmp_path):
    default_redactor().register(SECRET)
    (repo / "config.txt").write_text(f"key = {SECRET}\n", encoding="utf-8")
    _, _, sent = _sent_once(repo, tmp_path, "@config.txt")
    assert SECRET not in sent and f"key = {PLACEHOLDER}" in sent


def test_a_slash_commands_prompt_is_not_looked_at(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    _repl(wiring, "/review @a.py")
    [sent] = _sent(wiring)
    assert "@a.py" in sent and NOTE not in sent


def test_help_says_so(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "and @path in it attaches that file" in _shown(_repl(wiring, "/help"))
