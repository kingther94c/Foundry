"""Named placeholders in custom prompts (D-071): ``$FILE`` filled from
``FILE=src/x.py``, as codex's custom prompts take them.

What carries the feature: a prompt is never sent with a hole in it -- a
named value not given stops it, and says which -- while what was already
written keeps its meaning: ``$PSVersionTable`` and ``$env:PATH`` are not
placeholders, ``$$NAME`` is a literal one, and ``KEY=value`` stays an
ordinary word for a prompt that does not use KEY.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foundry.core.commands import CustomPrompt, expand, named_placeholders, usage_hint
from foundry.core.errors import PromptError

from test_resume import _wire, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _sent, _shown


def _prompt(body: str, hint: str = "") -> CustomPrompt:
    return CustomPrompt(name="draft", body=body, path=Path("draft.md"), argument_hint=hint)


@pytest.mark.parametrize("body, arguments, sent", [
    ("Review $FILE.", "FILE=src/calc.py", "Review src/calc.py."),
    ("Review $FILE.", 'FILE="my notes.md"', "Review my notes.md."),
    ("$TICKET_ID: $FILE and $FILE", "FILE=a.py TICKET_ID=FND-12", "FND-12: a.py and a.py"),
    ("Fix $1 in $FILE", "test_add FILE=calc.py", "Fix test_add in calc.py"),
    ("Fix $1 in $FILE", "FILE=calc.py test_add", "Fix test_add in calc.py"),
    ("Use $FILE", "FILE=a FILE=b", "Use b"),
    ("Use [$FILE]", "FILE=", "Use []"),
    ("Use $FILE", "FILE=a=b", "Use a=b"),
    ("All: $ARGUMENTS", "FILE=x more", "All: FILE=x more"),
    ("Value $FILE then $2", "FILE='$2' x", "Value $2 then "),
])
def test_named_values_are_filled(body, arguments, sent):
    assert expand(_prompt(body), arguments) == sent


@pytest.mark.parametrize("body, arguments, sent", [
    ("Run with $1", "DEBUG=1", "Run with DEBUG=1"),
    ("No placeholders.", "DEBUG=1", "No placeholders.\n\nDEBUG=1"),
    ("Costs $$FILE", "", "Costs $FILE"),
    ("Show $PSVersionTable and $env:PATH and $fileName", "", None),
    ("Lower $file and mixed $File_x", "", None),
])
def test_what_is_not_a_named_placeholder(body, arguments, sent):
    assert expand(_prompt(body), arguments) == (sent if sent is not None else body)


def test_a_missing_value_stops_the_prompt_and_says_which():
    with pytest.raises(PromptError) as caught:
        expand(_prompt("$TICKET for $FILE and $OWNER"), "FILE=a.py")
    assert str(caught.value).startswith("/draft needs TICKET=... OWNER=...;")
    assert "$$NAME is a literal $NAME" in str(caught.value)


def test_the_names_a_prompt_uses_in_order_once():
    assert named_placeholders("$B $A $B $$C $1 $ARGUMENTS $PSVersion $D_2") == ["B", "A", "D_2"]


@pytest.mark.parametrize("body, hint, shown", [
    ("For $FILE and $TICKET_ID", "", "FILE=... TICKET_ID=..."),
    ("For $FILE", "FILE=PATH", "FILE=PATH"),
    ("Fix $1", "", ""),
])
def test_help_says_what_a_prompt_needs(body, hint, shown):
    assert usage_hint(_prompt(body, hint)) == shown


# --- at the prompt ------------------------------------------------------------------------


def _with_prompt(repo, tmp_path, turns):
    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "draft.md").write_text(
        "---\ndescription: Draft a PR\n---\nDraft a PR for $TICKET touching $FILE.\n",
        encoding="utf-8")
    return _wire(repo, home, turns)


def test_a_prompt_missing_a_value_sends_nothing(repo, tmp_path):
    wiring = _with_prompt(repo, tmp_path, [])
    shown = _shown(_repl(wiring, "/draft FILE=a.py"))
    assert "/draft needs TICKET=..." in shown and "sending" not in shown
    assert wiring.runtime.backend.seen == []


def test_a_prompt_with_its_values_is_sent(repo, tmp_path):
    wiring = _with_prompt(repo, tmp_path, [turn(text="ok")])
    shown = _shown(_repl(wiring, "/draft TICKET=FND-7 FILE=a.py"))
    assert _sent(wiring) == ["Draft a PR for FND-7 touching a.py."]
    assert "/draft: sending your prompt" in shown


def test_help_lists_the_values(repo, tmp_path):
    shown = _shown(_repl(_with_prompt(repo, tmp_path, []), "/help"))
    assert "/draft TICKET=... FILE=..." in shown
