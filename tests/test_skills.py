"""Agent skills (D-113), as codex's: a folder holding SKILL.md -- a name and a
description in its front matter, then instructions -- listed to the model at
the start and loaded with load_skill when a task matches one; ``$name`` in a
message asks for one.

What carries it: a repository's skills are read only where the user trusts
it, and only from folders inside it; the user's own win a name both have;
load_skill reads nothing but a skill's own files; the model's list keeps to
its share of the window; a skill's text is shown cleaned and goes to the
model scrubbed; a session without skills is as it was.
"""

from __future__ import annotations

import io
import json
import os
import subprocess

import pytest

from foundry.cli import app
from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.policy.engine import Mode, PolicyEngine, Verdict
from foundry.core.redaction import default_redactor
from foundry.core.skills import (
    LIST_HEADER,
    LOAD_SKILL,
    REPOSITORY,
    YOURS,
    Skills,
    SkillTool,
    front_matter,
    list_budget,
    load_skills,
    skill_list,
)
from foundry.core.tools.base import ToolContext

from test_resume import _wire, call, repo, turn  # noqa: F401  (repo is a fixture)
from test_slash_commands import _repl, _sent, _shown

SKILL = """---
name: {name}
description: {description}
---

# {name}

{body}
"""


def _skill(root, folder, name=None, description="Use it when testing.", body="Do the thing.",
           files=None, text=None):
    path = root / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        text if text is not None else SKILL.format(name=name or folder,
                                                   description=description, body=body),
        encoding="utf-8", newline="\n")
    for relative, content in (files or {}).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8", newline="\n")
    return path


def _ctx(**extra):
    return ToolContext(workspace=None, artifacts=None, read_tracker=None, **extra)


def _load(tool, args, **ctx):
    return tool.execute(tool.validate(args), _ctx(**ctx)).content


def _system(wiring):
    return wiring.runtime.context.system_message().text_content


def _plain(typist) -> str:
    """What the console shows, without the markup."""
    from rich.text import Text

    return "\n".join(Text.from_markup(line).plain for line in _shown(typist).splitlines())


# --- the front matter ----------------------------------------------------------------------


@pytest.mark.parametrize("text, fields", [
    ("---\nname: pdf\ndescription: Fill PDF forms.\n---\nbody", {"name": "pdf",
                                                              "description": "Fill PDF forms."}),
    ('---\nname: "pdf"\ndescription: \'Quoted: with a colon\'\n---\n',
     {"name": "pdf", "description": "Quoted: with a colon"}),
    ("---\nname: pdf\ndescription: >\n  Folded over\n  two lines.\nlicense: MIT\n---\n",
     {"name": "pdf", "description": "Folded over two lines.", "license": "MIT"}),
    ("---\nname: pdf\ndescription: |-\n  Kept\n\n  apart\n---\n",
     {"name": "pdf", "description": "Kept apart"}),
    ("---\nname: pdf\nmetadata:\n  name: nested\n  version: 2\n# a comment\n---\n",
     {"name": "pdf", "metadata": ""}),
    ("\ufeff---\nname: pdf\n---\n", {"name": "pdf"}),
    ("name: pdf\n", {}),
    ("---\nname: pdf\n", {}),
])
def test_the_front_matter_is_read(text, fields):
    assert front_matter(text)[0] == fields


def test_the_body_follows_it():
    assert front_matter("---\nname: x\n---\n\n# Title\nText.")[1] == "\n# Title\nText."


# --- where skills come from ------------------------------------------------------------------


def test_the_users_skills_are_found(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "pdf-forms", description="Fill in  PDF\tforms.")
    _skill(home / "skills", "release")
    (home / "skills" / "notes").mkdir()                     # no SKILL.md: not a skill
    _skill(home / "skills", ".hidden")
    skills = load_skills(home, None, trusted=False)
    assert sorted(skills.found) == ["pdf-forms", "release"]
    pdf = skills.found["pdf-forms"]
    assert pdf.description == "Fill in PDF forms." and pdf.source == YOURS
    assert pdf.folder == (home / "skills" / "pdf-forms").resolve()
    assert skills.skipped == [] and skills.hidden == []


def test_a_repositorys_are_read_only_where_it_is_trusted(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    _skill(work / ".agents" / "skills", "deploy")
    assert load_skills(home, work, trusted=False).found == {}
    found = load_skills(home, work, trusted=True).found
    assert list(found) == ["deploy"] and found["deploy"].source == REPOSITORY


def test_the_users_own_win_a_name_both_have(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    _skill(home / "skills", "deploy", body="Mine.")
    _skill(work / ".agents" / "skills", "deploy", body="The repository's.")
    skills = load_skills(home, work, trusted=True)
    assert skills.found["deploy"].source == YOURS
    assert [(s.name, s.source) for s in skills.hidden] == [("deploy", REPOSITORY)]


def test_the_name_is_the_front_matters(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "folder-name", name="other-name")
    assert list(load_skills(home, None, trusted=False).found) == ["other-name"]


@pytest.mark.parametrize("text, why", [
    ("---\ndescription: d\n---\n", "its SKILL.md has no name"),
    ("---\nname: x\n---\n", "its SKILL.md has no description"),
    ("no front matter at all", "its SKILL.md has no name"),
    ("---\nname: Has Spaces\ndescription: d\n---\n",
     "its name 'Has Spaces' is not lowercase letters, digits and hyphens, at most 64"),
    ("---\nname: " + "a" * 65 + "\ndescription: d\n---\n", "is not lowercase letters"),
    ("---\nname: -lead\ndescription: d\n---\n", "is not lowercase letters"),
])
def test_a_folder_that_is_not_a_skill_says_why(tmp_path, text, why):
    home = tmp_path / "home"
    folder = _skill(home / "skills", "bad", text=text)
    skills = load_skills(home, None, trusted=False)
    assert skills.found == {}
    assert len(skills.skipped) == 1 and skills.skipped[0][0] == folder
    assert why in skills.skipped[0][1]


def test_a_description_is_at_most_1024(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "long", description="x" * 2000)
    assert len(load_skills(home, None, trusted=False).found["long"].description) == 1024


def _link(link, target):
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                       capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_a_repository_skill_that_leads_outside_it_is_not_used(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    outside = _skill(tmp_path / "elsewhere", "secrets")
    (work / ".agents" / "skills").mkdir(parents=True)
    _link(work / ".agents" / "skills" / "secrets", outside)
    skills = load_skills(home, work, trusted=True)
    assert skills.found == {}
    assert skills.skipped[0][1] == "it leads outside the repository"


def test_one_of_yours_may_be_a_link(tmp_path):
    home = tmp_path / "home"
    shared = _skill(tmp_path / "shared", "team")
    (home / "skills").mkdir(parents=True)
    _link(home / "skills" / "team", shared)
    assert load_skills(home, None, trusted=False).found["team"].folder == shared.resolve()


# --- the model's list ------------------------------------------------------------------------


def _skills(tmp_path, count, description="Use it when testing."):
    home = tmp_path / "home"
    for index in range(count):
        _skill(home / "skills", f"skill-{index:02}", description=description)
    return load_skills(home, None, trusted=False)


def test_the_list_names_each_skill_and_its_description(tmp_path):
    skills = _skills(tmp_path, 2)
    text = skill_list(skills, 8000)
    assert text == "\n".join([LIST_HEADER, "- skill-00: Use it when testing.",
                              "- skill-01: Use it when testing."])
    assert skills.left_out == []


def test_no_skills_no_list(tmp_path):
    assert skill_list(Skills(), 8000) == ""


def test_descriptions_are_shortened_first(tmp_path):
    skills = _skills(tmp_path, 10, description="word " * 100)
    text = skill_list(skills, 3000)
    assert skills.left_out == []
    lines = text.splitlines()[len(LIST_HEADER.splitlines()):]
    assert len(lines) == 10 and all(len(line) <= len("- skill-00: ") + 160 for line in lines)
    assert all(line.endswith("...") for line in lines)


def test_then_skills_are_left_out_and_said_so(tmp_path):
    skills = _skills(tmp_path, 40, description="word " * 100)
    text = skill_list(skills, 3000)
    assert len(text) <= 3000 + 120
    assert skills.left_out and skills.left_out[-1] == "skill-39"
    listed = [line for line in text.splitlines() if line.startswith("- skill-")]
    assert len(listed) + len(skills.left_out) == 40
    assert text.endswith(f"- ({len(skills.left_out)} more, not listed for room: load_skill "
                         "loads any skill by its name)")


@pytest.mark.parametrize("window, budget", [(128_000, 10_240), (10_000, 2_000),
                                            (1_000_000, 40_000), (200_000, 16_000)])
def test_the_list_has_codexs_share_of_the_window(window, budget):
    assert list_budget(window) == budget


# --- load_skill ------------------------------------------------------------------------------


@pytest.fixture()
def tool(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "pdf", body="Run scripts/fill.py on the form.",
           files={"scripts/fill.py": "print('filled')\n", "reference.md": "# Fields\n",
                  ".git/config": "hidden", "logo.png": b"\x89PNG\x00\x00binary"})
    return SkillTool(load_skills(home, None, trusted=False))


def test_it_gives_the_instructions_and_the_other_files(tool, tmp_path):
    text = _load(tool, {"name": "pdf"})
    folder = (tmp_path / "home" / "skills" / "pdf").resolve()
    assert text.startswith(f"Skill pdf (yours, in {folder})\n\n# pdf\n\nRun scripts/fill.py")
    assert text.endswith("Its other files, for load_skill with file:\n  logo.png\n"
                         "  reference.md\n  scripts/fill.py")
    assert "description:" not in text and ".git" not in text


@pytest.mark.parametrize("file", ["scripts/fill.py", "scripts\\fill.py", "./scripts/fill.py"])
def test_it_reads_one_of_them(tool, file):
    assert _load(tool, {"name": "pdf", "file": file}) == "print('filled')\n"


@pytest.mark.parametrize("file", ["../pdf/reference.md", "/etc/passwd", "C:/Windows/win.ini",
                                  "..", "", "  ", 3, "scripts/../../x"])
def test_it_reads_nothing_outside_the_skills_folder(tool, file):
    with pytest.raises(InvalidToolCall, match="file is a path inside the skill's folder"):
        tool.validate({"name": "pdf", "file": file})


def test_a_file_it_does_not_have_is_said(tool):
    with pytest.raises(ToolError, match="the skill pdf has no file nope.md"):
        _load(tool, {"name": "pdf", "file": "nope.md"})


def test_a_file_that_is_not_text_is_said(tool):
    with pytest.raises(ToolError, match="logo.png is not text"):
        _load(tool, {"name": "pdf", "file": "logo.png"})


def test_a_link_in_the_folder_to_outside_it_is_refused(tmp_path):
    home = tmp_path / "home"
    folder = _skill(home / "skills", "pdf")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not the skill's", encoding="utf-8")
    _link(folder / "linked", outside)
    tool = SkillTool(load_skills(home, None, trusted=False))
    with pytest.raises(ToolError, match="linked/secret.txt is not in the skill's folder"):
        _load(tool, {"name": "pdf", "file": "linked/secret.txt"})


@pytest.mark.parametrize("args, message", [
    ({"name": "nope"}, "no skill is named 'nope'; the skills are pdf"),
    ({}, "no skill is named 'None'"),
    ({"name": "pdf", "path": "x"}, "load_skill takes no 'path'"),
])
def test_a_bad_call_is_refused(tool, args, message):
    with pytest.raises(InvalidToolCall, match=message):
        tool.validate(args)


def test_its_operation_is_read_only_and_named(tool):
    op = tool.validate({"name": "pdf", "file": "scripts\\fill.py"})
    assert (op.kind.value, op.display, op.target) == ("read_only", "skill pdf: scripts/fill.py",
                                                      "pdf scripts/fill.py")
    assert op.args == {"name": "pdf", "file": "scripts/fill.py"}
    assert tool.validate({"name": "pdf"}).target == "pdf SKILL.md"
    assert op.digest != tool.validate({"name": "pdf"}).digest


@pytest.mark.parametrize("mode", [Mode.DEFAULT, Mode.PLAN, Mode.DONT_ASK])
def test_the_policy_lets_it_run_unasked(tool, mode):
    policy = PolicyEngine(mode=mode, interactive=False)
    decision, _ = policy.evaluate(tool.validate({"name": "pdf"}))
    assert decision.verdict is Verdict.ALLOW


def test_a_long_file_is_cut_and_kept(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "big", body="line\n" * 5000)
    tool = SkillTool(load_skills(home, None, trusted=False))
    output = tool.execute(tool.validate({"name": "big"}), _ctx(max_output_bytes=2000))
    assert output.truncated and len(output.content) < 2500


# --- in a session ----------------------------------------------------------------------------


def test_a_session_without_skills_is_as_it_was(repo, tmp_path):
    wiring = _wire(repo, tmp_path / "home", [])
    assert LOAD_SKILL not in wiring.runtime.registry.names()
    assert "# Skills" not in _system(wiring)


def test_a_session_lists_them_to_the_model_and_offers_load_skill(repo, tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "release", description="Cut a release: tag, notes, push.")
    wiring = _wire(repo, home, [turn(call("s1", LOAD_SKILL, {"name": "release"})),
                                turn(text="done")])
    system = _system(wiring)
    assert "# Skills\n" in system and "- release: Cut a release: tag, notes, push." in system
    # After the instructions, before the environment.
    assert system.index("# Skills") < system.index("# Environment")
    assert LOAD_SKILL in wiring.runtime.registry.names()
    wiring.runtime.run_turn("ship it")
    request = wiring.runtime.backend.seen[-1]
    assert LOAD_SKILL in [tool.name for tool in request.tools]
    result = request.messages[-1].blocks[0]
    assert "Skill release (yours" in result.content and not result.is_error


def test_a_repositorys_skill_is_asked_about_first(tmp_path):
    work = tmp_path / "work"
    _skill(work / ".agents" / "skills", "deploy")
    out = io.StringIO()
    from rich.console import Console

    assert not app._confirm_trust(Console(file=out), tmp_path / "home", work, interactive=False)
    assert "provides Foundry instructions" in out.getvalue()


def test_a_trusted_repositorys_skill_is_offered(repo, tmp_path):
    home = tmp_path / "home"
    _skill(repo / ".agents" / "skills", "deploy", description="Deploy the service.")
    untrusted = _wire(repo, home, [])
    assert "deploy" not in untrusted.skills.found
    app._remember_trust(home, repo)
    trusted = _wire(repo, home, [])
    assert trusted.skills.found["deploy"].source == REPOSITORY
    assert "- deploy: Deploy the service." in _system(trusted)


def test_what_is_not_as_it_should_be_is_said_at_the_start(repo, tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "bad", text="---\nname: x\n---\n")
    _skill(home / "skills", "deploy")
    _skill(repo / ".agents" / "skills", "deploy")
    app._remember_trust(home, repo)
    wiring = _wire(repo, home, [])
    said = wiring.renderer.console.file.getvalue()
    assert "skill folder" in said and "is not used: its SKILL.md has no description" in said
    assert "skill deploy in" in said and "is hidden by another of that name" in said


def test_one_left_out_of_the_list_is_said_and_still_loads(repo, tmp_path):
    home = tmp_path / "home"
    for index in range(60):
        _skill(home / "skills", f"skill-{index:02}", description="word " * 150)
    wiring = _wire(repo, home, [], overrides={"max_context_tokens": 25_000})
    said = " ".join(wiring.renderer.console.file.getvalue().split())
    assert "skill(s) left out of the model's list for room" in said
    left = wiring.skills.left_out[-1]
    assert f"- {left}:" not in _system(wiring)
    tool = wiring.runtime.registry.get(LOAD_SKILL)
    assert tool.execute(tool.validate({"name": left}), wiring.runtime.tool_ctx).content


# --- $name -----------------------------------------------------------------------------------


def test_naming_a_skill_sends_its_instructions_with_the_message(repo, tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "release", body="Tag it, then write the notes.")
    wiring = _wire(repo, home, [turn(text="ok")])
    typist = _repl(wiring, "cut 1.2 with $release please")
    sent = _sent(wiring)[0]
    assert sent.startswith("cut 1.2 with $release please\n\nThe user asked for the skill "
                           "release with $release; load_skill gives:\nSkill release")
    assert "Tag it, then write the notes." in sent
    assert "using the skill release" in _shown(typist)


@pytest.mark.parametrize("line", ["costs $5 or $PATH or $nope", "a$release", "$release-x",
                                  "$$release", "$release_x"])
def test_another_dollar_word_is_left_as_written(repo, tmp_path, line):
    home = tmp_path / "home"
    _skill(home / "skills", "release")
    wiring = _wire(repo, home, [turn(text="ok")])
    _repl(wiring, line)
    assert _sent(wiring) == [line]


def test_each_skill_goes_once_and_at_most_three(repo, tmp_path):
    home = tmp_path / "home"
    for name in ("a1", "b2", "c3", "d4"):
        _skill(home / "skills", name, body=f"Body of {name}.")
    wiring = _wire(repo, home, [turn(text="ok")])
    typist = _repl(wiring, "$a1 $a1 $b2 $c3 $d4")
    sent = _sent(wiring)[0]
    assert sent.count("Body of a1.") == 1 and "Body of c3." in sent
    assert "Body of d4." not in sent
    assert "at most 3 skills go with one message; d4 do not" in _shown(typist)


def test_a_rule_that_refuses_it_refuses_this(repo, tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "release")
    (home / "config.toml").write_text(
        '[[permissions]]\ntool = "load_skill"\npattern = "release *"\ndecision = "deny"\n'
        'reason = "not here"\n', encoding="utf-8")
    wiring = _wire(repo, home, [turn(text="ok")])
    typist = _repl(wiring, "use $release")
    assert _sent(wiring) == ["use $release"]
    assert "$release: not attached, the policy refuses loading it" in _shown(typist)


def test_what_is_attached_is_scrubbed(repo, tmp_path):
    held = "held-credential-2233"
    default_redactor().register(held)
    home = tmp_path / "home"
    _skill(home / "skills", "release", body=f"Use the token {held}.")
    wiring = _wire(repo, home, [turn(text="ok")])
    _repl(wiring, "$release")
    assert held not in _sent(wiring)[0] and "[redacted]" in _sent(wiring)[0]


# --- /skills ---------------------------------------------------------------------------------


def test_skills_lists_them(repo, tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "release", description="Cut a release.\x1b[31m red")
    _skill(home / "skills", "bad", text="---\nname: x\n---\n")
    _skill(repo / ".agents" / "skills", "release")
    _skill(repo / ".agents" / "skills", "deploy", description="Deploy it.")
    app._remember_trust(home, repo)
    wiring = _wire(repo, home, [])
    shown = _plain(_repl(wiring, "/skills"))
    assert "$deploy (the repository's) Deploy it." in shown
    assert "$release (yours) Cut a release." in shown and "\x1b" not in shown
    assert "release in" in shown and "hidden by another of that name" in shown
    assert "not used, its SKILL.md has no description" in shown


def test_skills_says_where_they_go_when_there_are_none(repo, tmp_path):
    home = tmp_path / "home"
    shown = " ".join(_shown(_repl(_wire(repo, home, []), "/skills")).split())
    assert ("no skills: a skill is a folder holding a SKILL.md, in "
            f"{home / 'skills'}") in shown
    assert ".agents/skills" not in shown
    app._remember_trust(home, repo)
    shown = " ".join(_shown(_repl(_wire(repo, home, []), "/skills")).split())
    assert "or this repository's .agents/skills" in shown


def test_skills_marks_one_not_in_the_models_list(repo, tmp_path):
    home = tmp_path / "home"
    for index in range(60):
        _skill(home / "skills", f"skill-{index:02}", description="word " * 150)
    wiring = _wire(repo, home, [], overrides={"max_context_tokens": 25_000})
    shown = _plain(_repl(wiring, "/skills"))
    assert f"${wiring.skills.left_out[-1]} (yours) (not in the model's list, for room)" in shown


def test_a_repositorys_skill_says_whose_it_is(tmp_path):
    work = tmp_path / "work"
    _skill(work / ".agents" / "skills", "deploy")
    tool = SkillTool(load_skills(tmp_path / "home", work, trusted=True))
    assert _load(tool, {"name": "deploy"}).startswith("Skill deploy (the repository's, in ")


def test_a_name_has_no_spaces(tmp_path):
    home = tmp_path / "home"
    _skill(home / "skills", "spaced", text="---\nname: has spaces\ndescription: d\n---\n")
    skills = load_skills(home, None, trusted=False)
    assert skills.found == {} and "is not lowercase letters" in skills.skipped[0][1]


def test_a_long_description_is_whole_where_there_is_room(tmp_path):
    skills = _skills(tmp_path, 1, description="word " * 60)
    assert skill_list(skills, 8000).endswith("- skill-00: " + " ".join(["word"] * 60))
