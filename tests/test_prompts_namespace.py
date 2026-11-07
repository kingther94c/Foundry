"""``/prompts:NAME`` (D-112), as codex calls a custom prompt: the same prompt
``/NAME`` sends, and the one way to call a prompt whose name a built-in
command has -- ``/prompts:review`` is the user's, ``/review`` Foundry's.

What carries it: a line shaped so is a command, never a task sent to the
model as it was typed; the namespace finds only custom prompts, never a
built-in; and a name it does not find is said, sending nothing.
"""

from __future__ import annotations

import pytest

from foundry.cli.repl import COMMANDS
from foundry.core.commands import parse_command

from test_resume import _wire, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _sent, _shown


def _home(tmp_path, **prompts):
    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    for name, text in prompts.items():
        (home / "prompts" / f"{name.replace('_', '-')}.md").write_text(text, encoding="utf-8")
    return home


# --- what is a command -------------------------------------------------------------------


@pytest.mark.parametrize("line, parsed", [
    ("/prompts:fix-test test_add", ("prompts:fix-test", "test_add")),
    ("  /Prompts:Explain  a.py ", ("prompts:explain", "a.py")),
    ("/prompts:review", ("prompts:review", "")),
    ("/prompts:", ("prompts:", "")),
    ("/PROMPTS:", ("prompts:", "")),
    ("/note:later more", ("note:later", "more")),
    ("/prompts:fix\nand more", ("prompts:fix", "and more")),
    ("/prompts::fix", None),
    ("/prompts:1st", None),
    ("/prompts: fix", None),
    ("/note: remember this", None),
    ("/a:b:c", None),
    ("/prompts:fix/x", None),
])
def test_a_namespaced_name_is_a_command(line, parsed):
    assert parse_command(line) == parsed


def test_a_plain_command_is_as_it_was():
    assert parse_command("/help") == ("help", "")
    assert parse_command("/usr/bin/python fails") is None


# --- what it sends -----------------------------------------------------------------------


def test_it_sends_the_custom_prompt(repo, tmp_path):
    home = _home(tmp_path, explain="Explain $1 briefly.")
    wiring = _wire(repo, home, [turn(text="ok"), turn(text="ok")])
    typist = _repl(wiring, "/prompts:explain a.py", "/explain b.py")
    assert _sent(wiring) == ["Explain a.py briefly.", "Explain b.py briefly."]
    assert "/prompts:explain: sending your prompt" in _shown(typist)


def test_it_reaches_a_prompt_a_builtin_hides(repo, tmp_path):
    home = _home(tmp_path, status="Report the build status of $ARGUMENTS.")
    wiring = _wire(repo, home, [turn(text="green")])
    typist = _repl(wiring, "/prompts:status main", "/status")
    # /status itself is still Foundry's: it sends nothing.
    assert _sent(wiring) == ["Report the build status of main."]
    assert "/prompts:status: sending your prompt" in _shown(typist)


def test_it_finds_no_builtin(repo, tmp_path):
    home = _home(tmp_path, explain="Explain.")
    wiring = _wire(repo, home, [])
    typist = _repl(wiring, "/prompts:help", "/prompts:review")
    assert _sent(wiring) == []
    shown = _shown(typist)
    assert "no custom prompt is named help; /help lists yours" in shown
    assert "no custom prompt is named review; /help lists yours" in shown


def test_a_repositorys_prompt_is_reached_where_it_is_trusted(repo, tmp_path):
    from foundry.cli import app

    home = tmp_path / "home"
    (repo / ".foundry" / "prompts").mkdir(parents=True)
    (repo / ".foundry" / "prompts" / "check.md").write_text("Run the checks.", encoding="utf-8")
    app._remember_trust(home, repo)
    wiring = _wire(repo, home, [turn(text="ok")])
    typist = _repl(wiring, "/prompts:check")
    assert _sent(wiring) == ["Run the checks."]
    assert "sending this repository's prompt" in _shown(typist)


def test_its_arguments_fill_the_placeholders(repo, tmp_path):
    home = _home(tmp_path, ticket="Close $TICKET as $1.")
    wiring = _wire(repo, home, [turn(text="ok")])
    typist = _repl(wiring, "/prompts:ticket TICKET=FND-7 done", "/prompts:ticket done")
    assert _sent(wiring) == ["Close FND-7 as done."]
    assert "TICKET" in _shown(typist).split("sending your prompt")[-1]


# --- what it says --------------------------------------------------------------------------


def test_a_name_it_does_not_find_is_said_and_nothing_sent(repo, tmp_path):
    home = _home(tmp_path, explain="Explain.")
    wiring = _wire(repo, home, [])
    typist = _repl(wiring, "/prompts:explian a.py")
    assert _sent(wiring) == []
    assert "no custom prompt is named explian; /help lists yours" in _shown(typist)


def test_without_a_name_it_asks_for_one(repo, tmp_path):
    home = _home(tmp_path, explain="Explain.")
    wiring = _wire(repo, home, [])
    typist = _repl(wiring, "/prompts:")
    assert _sent(wiring) == []
    assert "say which: /prompts:NAME; /help lists yours" in _shown(typist)


def test_with_no_prompts_it_says_where_they_go(repo, tmp_path):
    home = tmp_path / "home"
    wiring = _wire(repo, home, [])
    typist = _repl(wiring, "/prompts:explain", "/prompts:")
    assert _sent(wiring) == []
    said = f"you have no custom prompts -- Markdown files in {home / 'prompts'}"
    assert _shown(typist).count(said) == 2


def test_another_namespace_is_an_unknown_command_not_a_task(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    typist = _repl(wiring, "/note:later remember the parser")
    assert _sent(wiring) == []
    assert "unknown command /note:later; /help lists the commands" in _shown(typist)


def test_prompts_alone_is_told_codexs_way(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    typist = _repl(wiring, "/prompts")
    assert _sent(wiring) == []
    assert ("/prompts is codex's; a custom prompt is /NAME here, or /prompts:NAME as "
            "codex's; /help lists yours") in _shown(typist)


def test_a_prompt_named_prompts_is_still_the_users(repo, tmp_path):
    home = _home(tmp_path, prompts="List what is pending.")
    wiring = _wire(repo, home, [turn(text="ok")])
    _repl(wiring, "/prompts")
    assert _sent(wiring) == ["List what is pending."]


# --- /help ---------------------------------------------------------------------------------


def test_help_shows_a_hidden_prompt_by_its_namespace(repo, tmp_path):
    home = _home(tmp_path, review="---\ndescription: Our own review\n---\nReview it our way.",
                 explain="---\ndescription: Explain a file\nargument-hint: FILE\n---\nExplain $1.")
    wiring = _wire(repo, home, [])
    shown = _shown(_repl(wiring, "/help"))
    assert "custom prompts (each also /prompts:NAME, as codex's):" in shown
    assert "/prompts:review" in shown and "Our own review" in shown
    assert "/explain FILE" in shown and "/prompts:explain" not in shown
    assert "review" in COMMANDS


def test_help_without_prompts_says_nothing_of_them(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert "custom prompts" not in _shown(_repl(wiring, "/help"))
