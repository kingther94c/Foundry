"""codex's command-line flags, answered with what Foundry takes instead (D-097).

A codex user's `--full-auto`, `-a never` or `--yolo` was refused with argparse's
`unrecognized arguments` and nothing more. None of them means the same thing
here -- Foundry has no sandbox -- so they are not taken as aliases; the refusal
says what to use. Only `-p` (`--profile`) and `-V` (`--version`), which mean
exactly what codex means, are taken.

What it may not do: change anything a valid command line does, treat a word
after `--` (the task's) as a flag, or suggest a mode the subcommand does not
take.
"""

from __future__ import annotations

import argparse
import re

import pytest

from foundry import __version__
from foundry.cli import app, codex_flags


def _refused(capsys, *argv):
    with pytest.raises(SystemExit) as caught:
        app.main(list(argv))
    assert caught.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize("argv, told", [
    (["exec", "--full-auto", "fix it"],
     "--full-auto is codex's; use --mode accept_edits: patches go in without asking, but "
     "commands still need an allow rule (Foundry has no sandbox"),
    (["run", "--full-auto"], "commands still need an allow rule or your approval"),
    (["run", "-a", "never"], "-a never is codex's; use --mode dont_ask"),
    (["run", "--ask-for-approval=on-request"],
     "--ask-for-approval=on-request is codex's; use --mode default"),
    (["run", "-a", "untrusted"], "use --mode default: Foundry asks before anything"),
    (["run", "-a"], "use --mode: default asks before anything no rule allows"),
    (["exec", "-a", "never", "fix it"],
     "-a never is codex's; exec asks nobody: what no rule allows is refused (--mode dont_ask"),
    (["run", "-s", "read-only"], "-s read-only is codex's; use --mode plan"),
    (["run", "-sread-only"], "-sread-only is codex's; use --mode plan"),
    (["exec", "--sandbox", "workspace-write", "t"],
     "--sandbox workspace-write is codex's; use --mode accept_edits"),
    (["run", "-s", "danger-full-access"], "Foundry has no sandbox (docs/threat-model.md"),
    (["run", "--yolo"], "no mode here runs every command unasked"),
    (["run", "--dangerously-bypass-approvals-and-sandbox"],
     '[[permissions]] tool = "run_command" pattern = "pytest*" decision = "allow"'),
    (["exec", "--skip-git-repo-check", "t"], "works only inside a Git repository"),
    (["run", "--search"], "no web search of its own; an MCP server that searches can be "
                          "added with foundry mcp add"),
    (["run", "--oss"], 'protocol = "openai_compat", base_url = "http://localhost:11434/v1"'),
    (["exec", "--add-dir", "../other", "t"],
     "--add-dir ../other is codex's; a session works in one repository, -C DIR"),
])
def test_codex_s_flag_is_told_what_to_use(capsys, argv, told):
    err = _refused(capsys, *argv)
    assert "unrecognized arguments:" in err
    assert told in err


def test_the_usage_shown_is_the_subcommand_s(capsys):
    err = _refused(capsys, "exec", "--full-auto", "t")
    assert "usage: foundry exec" in err and "foundry exec: error:" in err


def test_a_flag_first_is_run_s(capsys):
    assert "foundry run: error: unrecognized arguments: --yolo" in _refused(capsys, "--yolo")


def test_each_is_told_once_and_all_are_told(capsys):
    err = _refused(capsys, "run", "--search", "--oss", "--search")
    assert err.count("--search is codex's") == 1 and "--oss is codex's" in err


def test_a_flag_after_it_is_not_its_value(capsys):
    err = _refused(capsys, "run", "-a", "--oss")
    assert "-a is codex's; use --mode: default" in err and "--oss is codex's" in err


def test_an_unknown_flag_that_is_not_codex_s_is_refused_as_before(capsys):
    err = _refused(capsys, "run", "--bogus")
    assert "unrecognized arguments: --bogus" in err and "codex" not in err


def test_after_a_double_dash_it_is_the_task_s():
    assert app.build_parser().parse_args(["exec", "--", "--yolo"]).task == "--yolo"
    # And in the hints, which only ever see a line already refused.
    assert codex_flags.hints(["exec", "--bogus", "--", "--yolo"], ["--bogus", "--yolo"]) == []


def test_a_valid_command_line_is_untouched():
    args = app.build_parser().parse_args(["exec", "--mode", "accept_edits", "-m", "gpt-5", "t"])
    assert (args.mode, args.model, args.task) == ("accept_edits", "gpt-5", "t")


@pytest.mark.parametrize("command", ["run", "resume", "exec", "record"])
def test_p_is_profile_as_in_codex(command):
    extra = ["--output", "f.json"] if command == "record" else []
    assert app.build_parser().parse_args([command, *extra, "-p", "fast", "t"]).profile == "fast"


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_V_is_version_as_in_codex(capsys, flag):
    with pytest.raises(SystemExit) as caught:
        app.main([flag])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == f"foundry {__version__}"


def _subparsers(parser):
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices


def test_no_flag_hinted_is_one_foundry_takes():
    """Were Foundry to take one of these, its hint would never be read."""
    taken = {flag for sub in _subparsers(app.build_parser()).values()
             for action in sub._actions for flag in action.option_strings}
    assert not taken & set(codex_flags._NAMES)


@pytest.mark.parametrize("command", ["run", "exec"])
@pytest.mark.parametrize("flag", sorted(codex_flags._NAMES))
@pytest.mark.parametrize("value", [None, "never", "untrusted", "read-only", "workspace-write",
                                   "danger-full-access"])
def test_every_mode_suggested_is_one_the_subcommand_takes(command, flag, value):
    words = [command, flag, *([value] if value else [])]
    [line] = codex_flags.hints(words, [flag])
    modes = next(a for a in _subparsers(app.build_parser())[command]._actions
                 if "--mode" in a.option_strings).choices
    for mode in re.findall(r"--mode (\w+)", line):
        assert mode in modes, line
