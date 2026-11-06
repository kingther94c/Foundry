"""Slash commands and custom prompts (D-039).

What carries the feature: a command is only what the user types, and the
commands that look at the session (/status, /diff) report it truthfully, the
one that reviews (/review) changes nothing whatever the mode, and a prompt a
repository supplies is read only where the user trusts it.
"""

from __future__ import annotations

import builtins

import pytest

from foundry.cli import app
from foundry.cli.repl import COMMANDS, REVIEW_READ_ONLY
from foundry.core.commands import (
    PROMPT_MAX_BYTES,
    CustomPrompt,
    builtin_prompt,
    expand,
    load_custom_prompts,
    parse_command,
)
from foundry.core.events import ApprovalChoice, TerminalStatus
from foundry.core.policy.engine import Mode, PolicyEngine, Verdict
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.command import RunCommand
from foundry.core.tools.files import ReadFile
from foundry.core.tools.patch import ApplyPatch

from test_plan import plan
from test_resume import PATCH_A, _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from test_undo import _Typist


class _Recorder(_Typist):
    """Keeps a highlighted block's text, not the object's repr."""

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(getattr(a, "code", None) or str(a) for a in args))


def _repl(wiring, *lines):
    typist = _Recorder([*lines, "/exit"])
    app._close(wiring, app._repl(wiring, typist), "session ended")
    return typist


def _shown(typist) -> str:
    return "\n".join(typist.printed)


def _sent(wiring) -> list[str]:
    """The user text of every request, last message only."""
    return [r.messages[-1].blocks[0].text for r in wiring.runtime.backend.seen
            if r.messages[-1].role.value == "user"]


# --- what is a command ------------------------------------------------------------


@pytest.mark.parametrize("line, parsed", [
    ("/help", ("help", "")),
    ("  /Review   the parser  ", ("review", "the parser")),
    ("/fix-test test_add\nand more", ("fix-test", "test_add\nand more")),
    ("/usr/bin/python fails to start", None),
    ("fix /tmp handling", None),
    ("/ help", None),
    ("/1st", None),
    ("/", None),
])
def test_a_command_is_a_slash_and_a_word(line, parsed):
    assert parse_command(line) == parsed


def test_an_unknown_command_is_not_sent_to_the_model(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    typist = _repl(wiring, "/udno", "/undo now")
    assert "unknown command /udno; /help lists the commands" in _shown(typist)
    assert "/undo takes no arguments" in _shown(typist)
    assert wiring.runtime.backend.seen == []


def test_a_line_that_only_starts_with_a_slash_is_a_task(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="ok")])
    _repl(wiring, "/usr/bin/python fails to start")
    assert _sent(wiring) == ["/usr/bin/python fails to start"]


def test_help_lists_every_command_and_the_custom_prompts(repo, tmp_path):
    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "fix-test.md").write_text(
        "---\ndescription: Fix one failing test\nargument-hint: TEST\n---\nFix $1.\n",
        encoding="utf-8")
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/help"))
    for name in COMMANDS:
        assert f"/{name}" in shown
    assert "/fix-test TEST" in shown and "Fix one failing test" in shown and "(yours)" in shown


# --- custom prompts ----------------------------------------------------------------------


def _prompt(body: str) -> CustomPrompt:
    return CustomPrompt(name="p", body=body, path=__import__("pathlib").Path("p.md"))


@pytest.mark.parametrize("body, arguments, sent", [
    ("Fix $1 in $2.", 'test_add "src/calc.py"', "Fix test_add in src/calc.py."),
    ("Do: $ARGUMENTS", "all of it, now", "Do: all of it, now"),
    ("Two words: $1", '"two words" more', "Two words: two words"),
    ("Missing: [$3]", "a b", "Missing: []"),
    ("Costs $$5 for $1", "x", "Costs $5 for x"),
    ("No placeholders here.", "extra context", "No placeholders here.\n\nextra context"),
    ("No placeholders, no arguments.", "", "No placeholders, no arguments."),
    ("Odd quote: $1", 'it"s here', 'Odd quote: it"s'),
])
def test_placeholders_are_filled_and_nothing_typed_is_lost(body, arguments, sent):
    assert expand(_prompt(body), arguments) == sent


def test_prompts_load_from_markdown_files_with_front_matter(tmp_path):
    folder = tmp_path / "home" / "prompts"
    folder.mkdir(parents=True)
    (folder / "Release-Notes.md").write_text(
        "---\ndescription:  Draft   the notes\nargument-hint: VERSION\n---\n\nWrite notes for $1.\n",
        encoding="utf-8")
    (folder / "plain.md").write_text("Just text.", encoding="utf-8")
    (folder / "has space.md").write_text("ignored", encoding="utf-8")
    (folder / "empty.md").write_text("---\ndescription: nothing\n---\n  \n", encoding="utf-8")
    (folder / "notes.txt").write_text("not markdown", encoding="utf-8")
    (folder / "huge.md").write_text("x" * (PROMPT_MAX_BYTES + 50), encoding="utf-8")

    prompts = load_custom_prompts(tmp_path / "home", None, trusted=False)
    assert sorted(prompts) == ["huge", "plain", "release-notes"]
    notes = prompts["release-notes"]
    assert notes.body == "Write notes for $1."
    assert notes.description == "Draft the notes" and notes.argument_hint == "VERSION"
    assert prompts["plain"].description == ""
    assert len(prompts["huge"].body) == PROMPT_MAX_BYTES


def test_a_repositorys_prompts_load_only_where_it_is_trusted(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "work"
    (home / "prompts").mkdir(parents=True)
    (workspace / ".foundry" / "prompts").mkdir(parents=True)
    (workspace / ".foundry" / "prompts" / "deploy.md").write_text("Deploy it.", encoding="utf-8")
    (workspace / ".foundry" / "prompts" / "shared.md").write_text("Repo's.", encoding="utf-8")
    (home / "prompts" / "shared.md").write_text("Mine.", encoding="utf-8")

    assert sorted(load_custom_prompts(home, workspace, trusted=False)) == ["shared"]
    trusted = load_custom_prompts(home, workspace, trusted=True)
    assert trusted["deploy"].from_repository
    assert trusted["shared"].body == "Mine." and not trusted["shared"].from_repository


def test_a_repository_with_only_prompts_is_asked_about(tmp_path, monkeypatch):
    workspace = tmp_path / "work"
    (workspace / ".foundry" / "prompts").mkdir(parents=True)
    from rich.console import Console
    import io

    out = io.StringIO()
    assert not app._confirm_trust(Console(file=out), tmp_path / "home", workspace,
                                  interactive=False)
    assert "provides Foundry instructions" in out.getvalue()


def test_a_custom_prompt_is_sent_as_the_task_and_says_where_it_came_from(repo, tmp_path):
    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "explain.md").write_text("Explain $1 briefly.", encoding="utf-8")
    (home / "prompts" / "status.md").write_text("Never sent: a built-in wins.", encoding="utf-8")
    wiring = _wire(repo, home, [turn(text="It is a constant.")])
    typist = _repl(wiring, "/explain a.py", "/status")
    assert _sent(wiring) == ["Explain a.py briefly."]
    assert "sending your prompt" in _shown(typist) and "explain.md" in _shown(typist)
    assert "Never sent" not in "".join(_sent(wiring))


def test_a_trusted_repositorys_prompt_is_marked_as_the_repositorys(repo, tmp_path):
    home = tmp_path / "home"
    (repo / ".foundry" / "prompts").mkdir(parents=True)
    (repo / ".foundry" / "prompts" / "check.md").write_text("Run the checks.", encoding="utf-8")
    app._remember_trust(home, repo)
    wiring = _wire(repo, home, [turn(text="ok")])
    typist = _repl(wiring, "/check")
    assert _sent(wiring) == ["Run the checks."]
    assert "sending this repository's prompt" in _shown(typist)


# --- /review ------------------------------------------------------------------------------


def test_a_review_changes_nothing_even_where_edits_need_no_approval(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"}),
             call("p1", "apply_patch", {"patch": PATCH_A}),
             call("c1", "run_command", {"command": "python -m pytest"})),
        turn(text="Nothing to report."),
        # After the review, edits are allowed again.
        turn(call("p2", "apply_patch", {"patch": PATCH_A})),
        turn(text="done"),
    ], overrides={"mode": Mode.ACCEPT_EDITS})
    typist = _repl(wiring, "/review the parser", "now make A 2")

    sent = _sent(wiring)
    assert sent[0] == builtin_prompt("review") + "\n\nThe user asks you to focus on: the parser"
    decisions = {r.payload["call_id"]: r.payload for r in SessionStore.read_records(
        wiring.session.path) if r.type == EventType.POLICY_DECISION}
    for cid in ("p1", "c1"):
        assert decisions[cid]["verdict"] == "deny"
        assert decisions[cid]["rule_id"] == "task.read_only"
        assert decisions[cid]["reason"] == REVIEW_READ_ONLY
    assert decisions["r1"]["verdict"] == "allow"
    assert decisions["p2"]["verdict"] == "allow"
    assert wiring.runtime.policy.read_only == ""
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert "nothing is changed while it runs" in _shown(typist)


def test_the_review_prompt_says_it_is_read_only():
    text = builtin_prompt("review")
    assert "read-only" in text and "git_diff" in text


# --- /init --------------------------------------------------------------------------------


def test_init_asks_for_foundry_md_and_not_twice(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [turn(text="written")])
    typist = _repl(wiring, "/init")
    assert _sent(wiring) == [builtin_prompt("init")]
    assert "FOUNDRY.md" in builtin_prompt("init")
    assert "once you trust this workspace" in _shown(typist)

    (repo / "AGENTS.md").write_text("# notes\n", encoding="utf-8")
    again = _wire(repo, tmp_path / "home2", [])
    typist = _repl(again, "/init")
    assert again.runtime.backend.seen == []
    assert "AGENTS.md already exists" in _shown(typist)


# --- /status and /diff ------------------------------------------------------------------


def test_status_tells_the_truth_about_the_session(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"}),
             call("u1", "update_plan", plan(("Change A", "completed"), ("Test", "pending")))),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="done")], overrides={"mode": Mode.ACCEPT_EDITS})
    # Changed after the session started, by someone else: git cannot tell that
    # apart from the session's own work, so it counts as changed since.
    (repo / "b.py").write_text("B = 5\n", encoding="utf-8")
    typist = _repl(wiring, "make A 2", "/status")
    shown = _shown(typist)
    assert wiring.session.session_id in shown
    assert "accept_edits" in shown and "fake" in shown
    assert "30 in (0 cached), 15 out" in shown
    assert "1 of 2 steps completed" in shown
    assert "2 file(s) since the session started" in shown


def test_diff_shows_every_change_and_whose_it_is(repo, tmp_path):
    # Dirty before the session starts, with an escape sequence in the text.
    (repo / "b.py").write_text("B = 1  # mine \x1b]0;evil\x07\n", encoding="utf-8")
    wiring = _wire(repo, tmp_path / "home", [
        turn(call("r1", "read_file", {"path": "a.py"})),
        turn(call("p1", "apply_patch", {"patch": PATCH_A})),
        turn(text="done")], overrides={"mode": Mode.ACCEPT_EDITS})
    (repo / "new.txt").write_bytes(b"one\ntwo\n\x1b]0;evil\x07three")
    typist = _repl(wiring, "make A 2", "/diff")
    shown = _shown(typist)
    assert "a.py [dim](this session)" in shown
    assert "b.py [dim](before this session)" in shown
    assert "new.txt [dim](this session)" in shown
    assert "new file[/green] new.txt [dim](3 lines)" in shown
    assert "# mine" in shown and "\x1b" not in shown and "evil" not in shown


def test_diff_with_nothing_changed_says_so(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "no uncommitted changes" in _shown(_repl(wiring, "/diff"))


# --- /new ---------------------------------------------------------------------------------


def test_new_ends_this_session_and_starts_another(repo, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    (home).mkdir()
    (home / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "fake"\nstream = false\n',
        encoding="utf-8")
    lines = iter(["/new", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(lines))
    assert app.main(["run", "--workspace", str(repo)]) == 13   # cancelled, by /exit

    journals = sorted((home / "sessions").glob("*/events.jsonl"))
    assert len(journals) == 2
    reasons = []
    for journal in journals:
        (ended,) = [r for r in SessionStore.read_records(journal)
                    if r.type == EventType.TERMINATION]
        reasons.append(ended.payload["reason"])
    assert sorted(reasons) == ["session ended", "the user started a new session"]
    assert "new session" in capsys.readouterr().out


# --- the policy: plan mode and read-only tasks refuse before anything asks -----------------


DIRTY_PATCH = ("*** Begin Patch\n*** Update File: a.py\n"
               "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n*** End Patch\n")


@pytest.mark.parametrize("op", [
    ApplyPatch().validate({"patch": DIRTY_PATCH}),             # a dirty file: an ASK rule
    RunCommand().validate({"command": "echo $env:PATH"}),       # unparseable: ASK
    RunCommand().validate({"command": "python -m pytest"}),
])
def test_plan_mode_refuses_what_an_ask_rule_would_have_put_to_the_user(op):
    decision, _ = PolicyEngine(mode=Mode.PLAN, dirty_files={"a.py"}).evaluate(op)
    assert (decision.verdict, decision.rule_id) == (Verdict.DENY, "mode.plan")


@pytest.mark.parametrize("mode", [Mode.DEFAULT, Mode.ACCEPT_EDITS, Mode.PLAN])
def test_a_read_only_task_refuses_every_change_in_every_mode(mode):
    policy = PolicyEngine(mode=mode, dirty_files={"a.py"})
    policy.read_only = "reviewing"
    for op in (ApplyPatch().validate({"patch": DIRTY_PATCH}),
               RunCommand().validate({"command": "python -m pytest"})):
        decision, _ = policy.evaluate(op)
        assert (decision.verdict, decision.rule_id, decision.reason) == (
            Verdict.DENY, "task.read_only", "reviewing")
    decision, _ = policy.evaluate(ReadFile().validate({"path": "a.py"}))
    assert decision.verdict is Verdict.ALLOW


def test_the_breaker_still_speaks_first():
    policy = PolicyEngine(mode=Mode.PLAN)
    policy.read_only = "reviewing"
    decision, _ = policy.evaluate(RunCommand().validate({"command": "git reset --hard"}))
    assert decision.rule_id == "breaker"


class _Exploding:
    def stream_turn(self, request):
        raise RuntimeError("provider exploded")


def test_the_read_only_switch_is_cleared_even_when_the_turn_fails(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [], approve=ApprovalChoice.ONCE)
    wiring.runtime.backend = _Exploding()
    from foundry.cli.repl import RunTask, run_task

    with pytest.raises(RuntimeError):
        run_task(wiring, RunTask("review", read_only="reviewing"))
    assert wiring.runtime.policy.read_only == ""
    app._close(wiring, TerminalStatus.CANCELLED, "session ended")
