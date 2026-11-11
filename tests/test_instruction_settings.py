"""codex's instruction settings (D-142): developer_instructions, told to the
model before the project's instructions, and compact_prompt (or
experimental_compact_prompt_file), what compaction asks for. The built-in
instructions are not replaced: they carry the rules the runtime checks.
"""

from __future__ import annotations

import pytest

from foundry.cli.agents import SUBAGENT_READ_ONLY
from foundry.core.commands import builtin_prompt
from foundry.core.config import load_config
from foundry.core.errors import ConfigError
from foundry.core.prompts import read_only_line

from test_resume import _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl

SAID = "Answer in French. Keep each answer under five lines."


def _home(tmp_path, runtime: str, *, own_notes: str = ""):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[runtime]\n" + runtime, encoding="utf-8")
    if own_notes:
        (home / "FOUNDRY.md").write_text(own_notes, encoding="utf-8")
    return home


def _system(wiring) -> str:
    return wiring.runtime.backend.seen[0].messages[0].blocks[0].text


def _toml(text: str) -> str:
    return '"""' + text + '"""'


# --- developer_instructions ------------------------------------------------------


def test_they_are_told_after_foundry_s_own_and_before_the_user_s_notes(repo, tmp_path):
    home = _home(tmp_path, f"developer_instructions = {_toml(SAID)}\n",
                 own_notes="Prefer small patches.")
    wiring = _wire(repo, home, [turn(text="Bonjour.")])
    _repl(wiring, "hello")
    system = _system(wiring)
    base = wiring.runtime.context.system_prompt
    assert system.startswith(base)
    said_at, notes_at = system.index(SAID), system.index("Prefer small patches.")
    assert len(base) < said_at < notes_at
    assert "# Developer instructions" in system[len(base):said_at]
    # /status names them with the notes it read.
    assert wiring.instructions[0] == "developer_instructions"


def test_none_is_nothing(repo, tmp_path):
    home = _home(tmp_path, 'developer_instructions = "  \\n "\n')
    wiring = _wire(repo, home, [turn(text="ok")])
    _repl(wiring, "hello")
    assert "# Developer instructions" not in _system(wiring)
    assert "developer_instructions" not in wiring.instructions


def test_a_sub_agent_is_told_them_too(repo, tmp_path):
    home = _home(tmp_path, f"developer_instructions = {_toml(SAID)}\n")
    wiring = _wire(repo, home, [
        turn(call("s1", "spawn_agent", {"task": "Find A."})),
        turn(call("f9", "finish", {"status": "completed", "summary": "A is 1",
                                   "claims": []})),
        turn(text="A is 1.")])
    _repl(wiring, "use a sub-agent")
    seen = wiring.runtime.backend.seen
    assert SAID in seen[1].messages[0].blocks[0].text
    # The session's system message, but for the line that tells a read-only
    # sub-agent so (D-173).
    read_only = read_only_line(SUBAGENT_READ_ONLY) + "\n"
    child = seen[1].messages[0].text_content
    assert read_only in child
    assert child.replace(read_only, "") == seen[0].messages[0].text_content


@pytest.mark.parametrize("value", ["1", "true", '["a"]'])
def test_only_text(tmp_path, value):
    home = _home(tmp_path, f"developer_instructions = {value}\n")
    with pytest.raises(ConfigError, match="developer_instructions must be text"):
        load_config(home=home)


def test_where_codex_keeps_them_is_told_where_foundry_does(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(f"developer_instructions = {_toml(SAID)}\n",
                                      encoding="utf-8")
    with pytest.raises(ConfigError, match=r"put it under \[runtime\]"):
        load_config(home=home)


@pytest.mark.parametrize("key", ["developer_instructions", "compact_prompt",
                                 "experimental_compact_prompt_file"])
def test_not_a_repository_s_to_set(repo, tmp_path, key):
    home = tmp_path / "home"
    home.mkdir()
    (repo / ".foundry").mkdir()
    (repo / ".foundry" / "config.toml").write_text(f'[runtime]\n{key} = "x"\n',
                                                   encoding="utf-8")
    with pytest.raises(ConfigError, match=f"may not set {key}"):
        load_config(repo, home=home)


@pytest.mark.parametrize("key, said", [
    ("model_instructions_file", "developer_instructions"),
    ("instructions", "developer_instructions"),
    ("experimental_instructions_file", "developer_instructions"),
])
def test_codex_s_replacements_for_the_built_in_ones_are_refused(tmp_path, key, said):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(f'{key} = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=said) as raised:
        load_config(home=home)
    assert "finish" in str(raised.value)


# --- compact_prompt ---------------------------------------------------------------


def _compacted(repo, home) -> str:
    wiring = _wire(repo, home, [turn(text="A is 1."), turn(text="Summary: A is 1.")])
    _repl(wiring, "what is A?", "/compact")
    return wiring.runtime.backend.seen[1].messages[-1].blocks[0].text


def test_compaction_asks_for_what_the_user_says(repo, tmp_path):
    home = _home(tmp_path, 'compact_prompt = "  Sum it up in three bullets.  "\n')
    assert _compacted(repo, home) == "Sum it up in three bullets."


def test_compaction_asks_as_it_did_without_it(repo, tmp_path):
    home = _home(tmp_path, 'compact_prompt = " "\n')
    assert _compacted(repo, home) == builtin_prompt("compact").strip()


def test_from_a_file(repo, tmp_path):
    prompt = tmp_path / "compact.md"
    prompt.write_text("\nKeep every file name.\n", encoding="utf-8")
    home = _home(tmp_path, f"experimental_compact_prompt_file = '{prompt}'\n")
    assert _compacted(repo, home) == "Keep every file name."


def test_the_text_comes_before_the_file(repo, tmp_path):
    prompt = tmp_path / "compact.md"
    prompt.write_text("Keep every file name.", encoding="utf-8")
    home = _home(tmp_path, f"experimental_compact_prompt_file = '{prompt}'\n"
                           'compact_prompt = "Three bullets."\n')
    assert _compacted(repo, home) == "Three bullets."


@pytest.mark.parametrize("name, content, said", [
    ("empty.md", " \n", "is empty"),
    ("missing.md", None, "could not read"),
    ("bad.md", b"\xff\xfe\xfa", "could not read"),
])
def test_a_file_that_cannot_be_used_is_said(tmp_path, name, content, said):
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content, encoding="utf-8")
    home = _home(tmp_path, f"experimental_compact_prompt_file = '{path}'\n")
    with pytest.raises(ConfigError, match=said):
        load_config(home=home)


def test_a_file_is_named_by_its_whole_path(tmp_path):
    home = _home(tmp_path, "experimental_compact_prompt_file = 'compact.md'\n")
    with pytest.raises(ConfigError, match="absolute path"):
        load_config(home=home)


def test_a_sub_agent_compacts_as_the_session_does(repo, tmp_path):
    from foundry.cli.agents import Spawner

    home = _home(tmp_path, 'compact_prompt = "Three bullets."\n')
    wiring = _wire(repo, home, [])
    spawner = Spawner(wiring=wiring)
    store = spawner._journal(wiring, 1, "t", True)
    try:
        assert spawner._runtime(wiring, store, 1).compact_prompt == "Three bullets."
    finally:
        store.close()
