"""run_command's description says what a rule can allow (D-183).

Against a real model, the first command of a fix was
`$env:PYTHONPATH="src"; python -m pytest tests -q 2>&1 | Select-Object -Last 30`,
refused unattended although `python -m pytest*` was allowed: nothing told the
model that a rule never allows a command the policy cannot read. Each claim of
the sentence is held to what the policy and the tool do.
"""

from __future__ import annotations

import pytest

from foundry.core.policy import segmenter
from foundry.core.policy.engine import Layer, Mode, PolicyEngine, Rule, Verdict
from foundry.core.tools.command import (
    PLAIN_COMMANDS,
    CommandResult,
    RunCommand,
    combined_output,
)

from test_policy import cmd_op


def _engine(mode=Mode.DEFAULT):
    engine = PolicyEngine(mode=mode)
    engine.add_rule(Rule(tool="run_command", pattern="python -m pytest*",
                         verdict=Verdict.ALLOW, layer=Layer.USER, rule_id="user.pytest"))
    return engine


def _verdict(command, mode=Mode.DEFAULT):
    decision, _ = _engine(mode).evaluate(cmd_op(command))
    return decision.verdict


def test_the_description_says_it():
    assert PLAIN_COMMANDS in RunCommand().schema().description


def test_it_names_every_character_the_policy_cannot_read():
    for character in segmenter._STRUCTURAL:
        assert character in PLAIN_COMMANDS, character
    assert "a lone &" in PLAIN_COMMANDS


def test_a_plain_command_is_allowed_by_the_rule():
    assert _verdict("python -m pytest tests -q") is Verdict.ALLOW


@pytest.mark.parametrize("command", [
    '$env:PYTHONPATH="src"; python -m pytest tests -q',   # the real model's
    "python -m pytest tests -q 2>&1",
    "python -m pytest tests -q > out.txt",
    "python -m pytest tests -q < in.txt",
    "python -m pytest tests -q # all of them",
    "python -m pytest tests `-q",
    "python -m pytest tests ^-q",
    "python -m pytest tests -q &",
])
def test_what_it_names_no_rule_allows(command):
    assert _verdict(command) is Verdict.ASK
    assert _verdict(command, Mode.DONT_ASK) is Verdict.DENY


@pytest.mark.parametrize("command", [
    "python -m pytest tests -q -k 'a > b'",
    'python -m pytest tests -q -k "a > b"',
    "python -m pytest tests -q -k 'a`b'",
    "python -m pytest tests -q -k 'a & b'",
    'python -m pytest tests -q -k "$x"',
])
def test_anywhere_in_it_means_quoted_too(command):
    assert _verdict(command) is Verdict.ASK


@pytest.mark.parametrize("command", [
    "python -m pytest tests -q -k '$x'",
    "python -m pytest tests -q -k 'a^b'",
    "python -m pytest tests -q -k 'a#b'",
    'python -m pytest tests -q -k "a#b"',
])
def test_outside_quotes_means_quoted_it_is_plain(command):
    assert _verdict(command) is Verdict.ALLOW


def test_a_pipeline_is_allowed_only_when_each_command_is():
    assert _verdict("python -m pytest tests -q | Select-Object -Last 30") is Verdict.ASK
    assert _verdict("python -m pytest tests -q; python -m pytest -q") is Verdict.ALLOW


def test_both_output_streams_come_back():
    out = combined_output(CommandResult(exit_code=1, stdout=b"1 failed\n",
                                        stderr=b"warning: slow\n", duration_ms=5))
    assert "1 failed" in out and "warning: slow" in out
