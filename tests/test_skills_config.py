"""Skills as codex finds and switches them (D-114): ``.agents/skills`` in the
workspace and every folder above it up to the repository's top, the nearest
first; and codex's ``[skills]`` table -- ``[[skills.config]]`` turning one off
or on by its SKILL.md's path or by its name, the last entry that matches
deciding, and ``max_context_tokens``, the room the model's list may take.

What carries it: nothing above the repository's top is read, and the folders
in it only where the workspace is trusted; the table is the user's own, as
the skills folder is -- a repository's config may not set it.
"""

from __future__ import annotations

import io
import json
import os

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.core.config import load_config
from foundry.core.errors import ConfigError
from foundry.core.skills import REPOSITORY, YOURS, load_skills, repository_skill_folders

from test_resume import _wire, repo  # noqa: F401  (repo is a fixture)
from test_skills import _plain, _repl, _skill, _system


def _config(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return load_config(home=home)


def _toml(path) -> str:
    return json.dumps(str(path))


# --- the folders up to the repository's top --------------------------------------------------


@pytest.fixture()
def nested(tmp_path):
    top = tmp_path / "repo"
    (top / ".git").mkdir(parents=True)
    work = top / "services" / "api"
    work.mkdir(parents=True)
    return top, work


def test_the_folders_are_the_workspace_and_those_above_it_to_the_top(nested):
    top, work = nested
    assert repository_skill_folders(work) == [
        work / ".agents" / "skills", top / "services" / ".agents" / "skills",
        top / ".agents" / "skills"]
    assert repository_skill_folders(top) == [top / ".agents" / "skills"]


def test_outside_a_repository_it_is_the_workspace_alone(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    assert repository_skill_folders(loose) == [loose / ".agents" / "skills"]


def test_each_one_is_read(nested, tmp_path):
    top, work = nested
    _skill(top / ".agents" / "skills", "release")
    _skill(top / "services" / ".agents" / "skills", "deploy")
    _skill(work / ".agents" / "skills", "migrate")
    skills = load_skills(tmp_path / "home", work, trusted=True)
    assert sorted(skills.found) == ["deploy", "migrate", "release"]
    assert {s.source for s in skills.found.values()} == {REPOSITORY}


def test_the_nearest_wins_a_name_and_yours_win_over_all(nested, tmp_path):
    top, work = nested
    _skill(top / ".agents" / "skills", "deploy", body="Top's.")
    near = _skill(work / ".agents" / "skills", "deploy", body="Near's.")
    skills = load_skills(tmp_path / "home", work, trusted=True)
    assert skills.found["deploy"].folder == near.resolve()
    assert [s.folder for s in skills.hidden] == [(top / ".agents" / "skills" / "deploy").resolve()]
    _skill(tmp_path / "home" / "skills", "deploy", body="Mine.")
    skills = load_skills(tmp_path / "home", work, trusted=True)
    assert skills.found["deploy"].source == YOURS and len(skills.hidden) == 2


def test_nothing_above_the_top_is_read(nested, tmp_path):
    top, work = nested
    _skill(tmp_path / ".agents" / "skills", "above")
    assert load_skills(tmp_path / "home", work, trusted=True).found == {}


def test_none_where_the_workspace_is_not_trusted(nested, tmp_path):
    top, work = nested
    _skill(top / ".agents" / "skills", "release")
    assert load_skills(tmp_path / "home", work, trusted=False).found == {}


def test_a_workspace_whose_repository_has_them_above_it_is_asked_about(nested, tmp_path):
    top, work = nested
    _skill(top / ".agents" / "skills", "release")
    out = io.StringIO()
    assert not app._confirm_trust(Console(file=out), tmp_path / "home", work, interactive=False)
    assert "provides Foundry instructions" in out.getvalue()


# --- [[skills.config]] -----------------------------------------------------------------------


def test_a_name_turns_one_off(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "release")
    _skill(home / "skills", "deploy")
    config = _config(home, '[[skills.config]]\nname = "release"\nenabled = false\n')
    assert config.skill_switches == (("name", "release", False),)
    skills = load_skills(home, None, trusted=False, switches=config.skill_switches)
    assert list(skills.found) == ["deploy"]
    assert [s.name for s in skills.disabled] == ["release"]


def test_a_path_turns_one_off(tmp_path):
    home = tmp_path / "home"
    folder = _skill(home / "skills", "release")
    path = str(folder / "SKILL.md")
    if os.name == "nt":
        path = path.upper()     # Windows paths match whatever their case
    config = _config(home, f"[[skills.config]]\npath = {_toml(path)}\nenabled = false\n")
    skills = load_skills(home, None, trusted=False, switches=config.skill_switches)
    assert skills.found == {} and [s.name for s in skills.disabled] == ["release"]


def test_a_path_that_is_another_skills_turns_nothing_off(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "release")
    other = tmp_path / "elsewhere" / "SKILL.md"
    config = _config(home, f"[[skills.config]]\npath = {_toml(other)}\nenabled = false\n")
    assert list(load_skills(home, None, trusted=False,
                            switches=config.skill_switches).found) == ["release"]


def test_the_last_entry_that_matches_decides(tmp_path):
    home = tmp_path / "home"
    folder = _skill(home / "skills", "release")
    config = _config(home, '[[skills.config]]\nname = "release"\nenabled = false\n'
                     f"[[skills.config]]\npath = {_toml(folder / 'SKILL.md')}\nenabled = true\n")
    assert list(load_skills(home, None, trusted=False,
                            switches=config.skill_switches).found) == ["release"]
    config = _config(home, f"[[skills.config]]\npath = {_toml(folder / 'SKILL.md')}\n"
                     'enabled = true\n[[skills.config]]\nname = "release"\nenabled = false\n')
    assert load_skills(home, None, trusted=False, switches=config.skill_switches).found == {}


def test_turning_yours_off_lets_the_repositorys_of_that_name_through(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    mine = _skill(home / "skills", "deploy")
    _skill(work / ".agents" / "skills", "deploy")
    config = _config(home, f"[[skills.config]]\npath = {_toml(mine / 'SKILL.md')}\n"
                     "enabled = false\n")
    skills = load_skills(home, work, trusted=True, switches=config.skill_switches)
    assert skills.found["deploy"].source == REPOSITORY and skills.hidden == []


@pytest.mark.parametrize("entry, message", [
    ('[[skills.config]]\nname = "a"\npath = "C:/x/SKILL.md"\nenabled = false\n',
     "skills.config 1 names a skill by path or by name, one of the two"),
    ("[[skills.config]]\nenabled = false\n",
     "skills.config 1 names a skill by path or by name, one of the two"),
    ('[[skills.config]]\npath = "skills/a/SKILL.md"\nenabled = false\n',
     "skills.config 1.path must be the absolute path of a SKILL.md"),
    ('[[skills.config]]\nname = "a"\n', "skills.config 1.enabled must be true or false"),
    ('[[skills.config]]\nname = "a"\nenabled = "no"\n',
     "skills.config 1.enabled must be true or false"),
    ('[[skills.config]]\nname = ""\nenabled = false\n', "skills.config 1.name must be a string"),
    ('[[skills.config]]\nname = 3\nenabled = false\n', "skills.config 1.name must be a string"),
    ('[[skills.config]]\nname = "a"\nenabled = false\nwhen = "x"\n',
     "skills.config 1: no setting 'when' (it takes enabled, name, path)"),
    ('[[skills.config]]\nname = "a"\nenabled = false\n[[skills.config]]\nenabled = true\n',
     "skills.config 2 names a skill by path or by name"),
    ("[skills]\nconfig = 3\n", "skills.config must be tables, as [[skills.config]]"),
    ("[skills]\nconfig = [3]\n", "skills.config 1 must be a table"),
    ("skills = 3\n", "skills must be a table"),
    ("[skills.bundled]\nenabled = false\n", "skills: no setting 'bundled'; Foundry bundles no "
                                            "skills"),
    ("[skills]\nmax_tokens = 5\n",
     "skills: no setting 'max_tokens' (it takes config, max_context_tokens)"),
])
def test_a_bad_entry_is_refused(tmp_path, entry, message):
    with pytest.raises(ConfigError) as caught:
        _config(tmp_path / "home", entry)
    assert message in str(caught.value)


def test_a_repositorys_config_may_not_set_them(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    (work / ".foundry").mkdir(parents=True)
    (work / ".foundry" / "config.toml").write_text(
        '[[skills.config]]\nname = "release"\nenabled = true\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="may not set skills; set them in your own"):
        load_config(work, home=home)


def test_c_is_told_where_they_go():
    from foundry.core.config import parse_settings

    with pytest.raises(ConfigError, match=r"\[skills\] goes in your config.toml"):
        parse_settings(["skills.max_context_tokens=100"])


# --- max_context_tokens ----------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "10001", "true", '"5"', "1.5"])
def test_the_budget_is_a_whole_number_up_to_ten_thousand(tmp_path, value):
    with pytest.raises(ConfigError, match="skills.max_context_tokens must be a whole number of "
                                          "tokens above 0 and at most 10,000"):
        _config(tmp_path / "home", f"[skills]\nmax_context_tokens = {value}\n")


def test_the_budget_is_kept(tmp_path):
    assert _config(tmp_path / "home", "[skills]\nmax_context_tokens = 10000\n"
                   ).skills_max_context_tokens == 10_000
    assert _config(tmp_path / "h2", "").skills_max_context_tokens is None


def test_the_list_keeps_to_it(repo, tmp_path):
    home = tmp_path / "home"
    for index in range(30):
        _skill(home / "skills", f"skill-{index:02}", description="word " * 150)
    (home / "config.toml").write_text("[skills]\nmax_context_tokens = 500\n", encoding="utf-8")
    wiring = _wire(repo, home, [])
    system = _system(wiring)
    listed = system[system.index("# Skills"):system.index("# Environment")]
    assert len(listed) <= 500 * 4 + 120
    assert wiring.skills.left_out
    # Without it, the window's share: all thirty fit.
    (home / "config.toml").write_text("", encoding="utf-8")
    assert _wire(repo, home, []).skills.left_out == []


# --- /skills ---------------------------------------------------------------------------------


def test_skills_says_which_are_turned_off(repo, tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "release")
    (home / "config.toml").write_text('[[skills.config]]\nname = "release"\nenabled = false\n',
                                      encoding="utf-8")
    wiring = _wire(repo, home, [])
    assert "release" not in wiring.skills.found
    shown = _plain(_repl(wiring, "/skills"))
    assert "release in" in shown and "turned off by your [[skills.config]]" in shown
    assert "no skills" not in shown


def test_the_machines_policy_has_the_last_word(tmp_path, monkeypatch):
    programdata = tmp_path / "programdata"
    (programdata / "Foundry").mkdir(parents=True)
    (programdata / "Foundry" / "policy.toml").write_text(
        '[[skills.config]]\nname = "release"\nenabled = false\n', encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(programdata))
    home = tmp_path / "home"
    _skill(home / "skills", "release")
    config = _config(home, '[[skills.config]]\nname = "release"\nenabled = true\n')
    assert config.all_skill_switches() == (("name", "release", True),
                                           ("name", "release", False))
    assert load_skills(home, None, trusted=False,
                       switches=config.all_skill_switches()).found == {}


def test_a_session_keeps_to_the_policy_and_the_user(repo, tmp_path, monkeypatch):
    programdata = tmp_path / "programdata"
    (programdata / "Foundry").mkdir(parents=True)
    (programdata / "Foundry" / "policy.toml").write_text(
        '[[skills.config]]\nname = "release"\nenabled = false\n', encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(programdata))
    home = tmp_path / "home"
    _skill(home / "skills", "release")
    _skill(home / "skills", "deploy")
    _skill(home / "skills", "notes")
    (home / "config.toml").write_text('[[skills.config]]\nname = "release"\nenabled = true\n'
                                      '[[skills.config]]\nname = "notes"\nenabled = false\n',
                                      encoding="utf-8")
    wiring = _wire(repo, home, [])
    assert list(wiring.skills.found) == ["deploy"]
    assert "- release:" not in _system(wiring) and "- notes:" not in _system(wiring)
