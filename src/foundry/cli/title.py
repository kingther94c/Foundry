"""The terminal's title, as codex's (D-139): what an interactive session is
doing -- working, or waiting on your answer -- where a tab bar or a task bar
shows it, so a session in a tab out of sight can be seen to need you.

The title is built from items the user picks (``[runtime] terminal_title``,
or ``/title`` for one session), by codex's ids, and cleaned before it is
written: its text comes from a session's name, a folder's, a model's, and
none of it may end the escape sequence it travels in, or reorder what is
shown.
"""

from __future__ import annotations

import unicodedata

# Tab bars show far less, and terminals cut long titles anyway (codex's bound).
MAX_TITLE = 240
ACTION_REQUIRED = "[ ! ] Action Required"
WORKING = "Working"
_RUN_STATE = {"ready": "Ready", "working": "Working", "action": "Waiting"}


def clean(text: str) -> str:
    """``text`` as one line fit for a title: control and format characters
    -- an escape that would end the sequence, a bidi override that would
    reorder what is shown -- dropped, each run of whitespace one space, at
    most MAX_TITLE characters."""
    kept: list[str] = []
    space = False
    for ch in text:
        if ch.isspace():
            space = bool(kept)
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        if space:
            kept.append(" ")
            space = False
        kept.append(ch)
    return "".join(kept)[:MAX_TITLE]


def compose(items, facts: dict, state: str) -> str:
    """The title ``items`` make while the session is ``state`` -- ready,
    working, or action (waiting on an answer). The activity is set apart by
    a space, the others by `` | ``, as codex's are; an item with nothing to
    show, such as an unnamed session's name, is left out."""
    title = ""
    previous = None
    for item in items:
        if item == "activity":
            text = {"action": ACTION_REQUIRED, "working": WORKING}.get(state, "")
        elif item == "run-state":
            text = _RUN_STATE[state]
        else:
            text = facts.get(item) or ""
        text = clean(text)
        if not text:
            continue
        if title:
            title += " " if "activity" in (previous, item) else " | "
        title += text
        previous = item
    return clean(title)
