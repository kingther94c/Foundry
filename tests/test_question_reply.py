"""A question is answered in the reply; `finish` is for a task (D-180).

Stand-in models in P9 and P11 were unsure whether a question at the prompt
needed `finish`: the base prompt said only "Call `finish` when the task is
done". The interactive prompt takes a reply as the turn's end (D-063); an
unattended run's permissions paragraph says nobody reads one.
"""

from __future__ import annotations

from foundry.core.policy.engine import Mode, PolicyEngine
from foundry.core.prompts import base_system_prompt, permissions_paragraph


def _said(text: str) -> str:
    return " ".join(text.split())


def test_a_question_is_answered_in_the_reply():
    assert ("A question that asks for no change -- what a file does, what a review found -- "
            "is answered in your reply; `finish` is for a task.") in _said(base_system_prompt())


def test_the_exception_it_names_is_what_an_unattended_run_is_told():
    assert ("Where the permissions paragraph says nobody reads a reply, end with `finish` "
            "all the same.") in _said(base_system_prompt())
    unattended = PolicyEngine(mode=Mode.DEFAULT)
    unattended.interactive = False
    for policy in (unattended, PolicyEngine(mode=Mode.DONT_ASK)):
        told = _said(permissions_paragraph(policy))
        assert "read a reply" in told or "reads a reply" in told
        assert "end the task with `finish`" in told
    assert "reads a reply" not in _said(permissions_paragraph(PolicyEngine(mode=Mode.DEFAULT)))
