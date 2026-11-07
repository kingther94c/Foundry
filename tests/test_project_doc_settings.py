"""Which files are instructions, and how much of them is read (D-086), as
codex's settings: ``AGENTS.override.md`` over the shared files in each
directory, ``project_doc_fallback_filenames`` tried after the usual names, and
``project_doc_max_bytes`` as the budget the repository's files share (0 reads
none of them).

What carries it: the names and the budget are the user's -- a repository may
read less of its own notes, never name more of its files as instructions --
and a file read only by a fallback name is asked about before it is read,
like any other.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from foundry.cli import app
from foundry.core.config import load_config, parse_settings
from foundry.core.errors import ConfigError
from foundry.core.prompts import (PROJECT_DOC_MAX_BYTES, doc_names, load_instructions,
                                  repository_docs)

from test_instructions import _repo, _write
from test_mcp import _config
from test_resume import OVERRIDES, _wire, repo  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown


# --- which file ---------------------------------------------------------------------------


def test_a_local_override_wins_in_each_directory(tmp_path):
    top = _repo(tmp_path, "pkg")
    for name in ("FOUNDRY.md", "AGENTS.md", "AGENTS.override.md"):
        _write(top / name, f"top {name}\n")
    _write(top / "pkg" / "AGENTS.md", "pkg shared\n")
    _write(top / "pkg" / "AGENTS.override.md", "pkg local\n")
    assert [label for label, _ in repository_docs(top / "pkg")] == [
        "AGENTS.override.md", "pkg/AGENTS.override.md"]
    text = load_instructions(top / "pkg", None, trusted=True).text
    assert "top AGENTS.override.md" in text and "pkg local" in text
    assert "top FOUNDRY.md" not in text and "pkg shared" not in text


def test_the_users_own_override_wins_too(tmp_path):
    home = tmp_path / "home"
    _write(home / "FOUNDRY.md", "shared notes\n")
    _write(home / "AGENTS.override.md", "this machine's notes\n")
    found = load_instructions(_repo(tmp_path), home, trusted=False)
    assert "this machine's notes" in found.text and "shared notes" not in found.text
    assert found.sources == (str(home / "AGENTS.override.md"),)


def test_a_fallback_name_is_read_only_where_the_usual_ones_are_missing(tmp_path):
    top = _repo(tmp_path, "pkg")
    _write(top / "CLAUDE.md", "top claude\n")
    _write(top / "pkg" / "AGENTS.md", "pkg agents\n")
    _write(top / "pkg" / "CLAUDE.md", "pkg claude\n")
    assert repository_docs(top / "pkg") == [("pkg/AGENTS.md", top / "pkg" / "AGENTS.md")]
    assert [label for label, _ in repository_docs(top / "pkg", ("CLAUDE.md",))] == [
        "CLAUDE.md", "pkg/AGENTS.md"]
    text = load_instructions(top / "pkg", None, trusted=True, fallbacks=("CLAUDE.md",)).text
    assert "top claude" in text and "pkg agents" in text and "pkg claude" not in text


def test_fallbacks_are_tried_in_the_order_given(tmp_path):
    top = _repo(tmp_path)
    _write(top / "CLAUDE.md", "claude\n")
    _write(top / ".cursorrules", "cursor\n")
    assert repository_docs(top, (".cursorrules", "CLAUDE.md"))[0][0] == ".cursorrules"
    assert repository_docs(top, ("CLAUDE.md", ".cursorrules"))[0][0] == "CLAUDE.md"
    assert doc_names(("AGENTS.md", "CLAUDE.md")) == (
        "AGENTS.override.md", "FOUNDRY.md", "AGENTS.md", "CLAUDE.md")


def test_the_users_own_file_takes_no_fallback(tmp_path):
    home = tmp_path / "home"
    _write(home / "CLAUDE.md", "not this\n")
    found = load_instructions(_repo(tmp_path), home, trusted=True, fallbacks=("CLAUDE.md",))
    assert found.text == "" and found.sources == ()


# --- how much -----------------------------------------------------------------------------


def test_one_file_is_cut_at_the_budget(tmp_path):
    top = _repo(tmp_path)
    _write(top / "AGENTS.md", "a" * 500)
    text = load_instructions(top, None, trusted=True, max_chars=100).text
    assert text.endswith("a" * 100 + "\n[project document truncated]")
    assert "a" * 101 not in text


def test_files_share_the_budget_from_the_top_down(tmp_path):
    top = _repo(tmp_path, "pkg")
    _write(top / "AGENTS.md", "t" * 80)
    _write(top / "pkg" / "AGENTS.md", "p" * 80)
    _write(top / "pkg" / "sub" / "AGENTS.md", "s" * 80)
    found = load_instructions(top / "pkg" / "sub", None, trusted=True, max_chars=100)
    assert "t" * 80 in found.text and "p" * 20 + "\n[project document truncated]" in found.text
    assert "s" * 10 not in found.text
    assert ("[left out: the project instructions above already fill their 100 characters]"
            in found.text)
    assert found.sources == ("AGENTS.md", "pkg/AGENTS.md")


def test_none_reads_none_of_the_repositorys_but_still_the_users(tmp_path):
    home = tmp_path / "home"
    _write(home / "FOUNDRY.md", "mine\n")
    top = _repo(tmp_path)
    _write(top / "AGENTS.md", "theirs\n")
    found = load_instructions(top, home, trusted=True, max_chars=0)
    assert "mine" in found.text and "theirs" not in found.text
    assert found.sources == (str(home / "FOUNDRY.md"),)


# --- the settings -------------------------------------------------------------------------


def test_the_users_config_sets_them(tmp_path):
    config = load_config(home=_config(tmp_path / "home", (
        '[runtime]\nproject_doc_max_bytes = 65536\n'
        'project_doc_fallback_filenames = ["CLAUDE.md", ".cursorrules", "CLAUDE.md"]\n')))
    assert config.project_doc_max_bytes == 65536
    assert config.project_doc_fallback_filenames == ("CLAUDE.md", ".cursorrules")
    default = load_config(home=_config(tmp_path / "other", ""))
    assert default.project_doc_max_bytes == PROJECT_DOC_MAX_BYTES
    assert default.project_doc_fallback_filenames == ()


def test_the_command_line_sets_them(tmp_path):
    settings = parse_settings(["project_doc_max_bytes=0",
                               'project_doc_fallback_filenames=["CLAUDE.md"]'])
    config = load_config(home=_config(tmp_path / "home", ""), settings=settings)
    assert config.project_doc_max_bytes == 0
    assert config.project_doc_fallback_filenames == ("CLAUDE.md",)


@pytest.mark.parametrize("line, message", [
    ("project_doc_max_bytes = -1", "project_doc_max_bytes must be a whole number"),
    ("project_doc_max_bytes = true", "project_doc_max_bytes must be a whole number"),
    ('project_doc_max_bytes = "32k"', "project_doc_max_bytes must be a whole number"),
    ('project_doc_fallback_filenames = "CLAUDE.md"', "must be a list of file names"),
    ('project_doc_fallback_filenames = ["docs/CLAUDE.md"]', "must be a list of file names"),
    ('project_doc_fallback_filenames = ["..\\\\x.md"]', "must be a list of file names"),
    ('project_doc_fallback_filenames = ["CLAUDE.md:x"]', "must be a list of file names"),
    ('project_doc_fallback_filenames = [".."]', "must be a list of file names"),
    ('project_doc_fallback_filenames = [""]', "must be a list of file names"),
    ('project_doc_fallback_filenames = [1]', "must be a list of file names"),
    ("project_doc_fallback_filenames = [" + ", ".join(f'"f{i}.md"' for i in range(17)) + "]",
     "must be a list of file names"),
])
def test_a_bad_setting_is_refused(tmp_path, line, message):
    with pytest.raises(ConfigError, match=message):
        load_config(home=_config(tmp_path / "home", "[runtime]\n" + line + "\n"))


def _project(tmp_path, text):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text("[runtime]\n" + text,
                                                        encoding="utf-8")
    return workspace


def test_a_repository_may_read_less_of_its_notes_but_not_more(tmp_path):
    home = _config(tmp_path / "home", "")
    lower = load_config(_project(tmp_path / "a", "project_doc_max_bytes = 1000\n"), home=home)
    assert lower.project_doc_max_bytes == 1000
    with pytest.raises(ConfigError, match="may only lower project_doc_max_bytes"):
        load_config(_project(tmp_path / "b", "project_doc_max_bytes = 900000\n"), home=home)


def test_a_repository_may_not_name_its_files_as_instructions(tmp_path):
    with pytest.raises(ConfigError, match="may not set project_doc_fallback_filenames"):
        load_config(_project(tmp_path, 'project_doc_fallback_filenames = ["README.md"]\n'),
                    home=_config(tmp_path / "home", ""))


# --- trust and a session ------------------------------------------------------------------


def _asked(tmp_path, workspace, **settings):
    out = io.StringIO()
    app._confirm_trust(Console(file=out, width=200), tmp_path / "home", workspace,
                       interactive=False, **settings)
    return "provides Foundry instructions; not loading them" in out.getvalue()


def test_a_file_read_by_a_fallback_name_is_asked_about(tmp_path):
    top = _repo(tmp_path)
    _write(top / "CLAUDE.md", "notes\n")
    assert not _asked(tmp_path, top)
    assert _asked(tmp_path, top, fallbacks=("CLAUDE.md",))


def test_nothing_is_asked_about_files_the_user_reads_none_of(tmp_path):
    top = _repo(tmp_path)
    _write(top / "AGENTS.md", "notes\n")
    assert _asked(tmp_path, top)
    assert not _asked(tmp_path, top, read_docs=False)


def test_a_session_reads_by_the_users_settings(repo, tmp_path):
    home = _config(tmp_path / "home", ('[runtime]\nproject_doc_max_bytes = 40\n'
                                       'project_doc_fallback_filenames = ["CLAUDE.md"]\n'))
    _write(repo / "CLAUDE.md", "Run the tests with pytest. " + "x" * 100)
    app._remember_trust(home, repo)
    wiring = _wire(repo, home, [])
    system = wiring.runtime.context.system_message().text_content
    assert "Run the tests with pytest. " + "x" * 13 + "\n[project document truncated]" in system
    assert wiring.instructions == ("CLAUDE.md",)
    wiring.session.close()


def test_a_session_asks_about_a_file_only_a_fallback_name_reads(repo, tmp_path):
    _write(repo / "CLAUDE.md", "notes\n")
    said = "provides Foundry instructions; not loading them"
    for text, asked in (("", False),
                        ('[runtime]\nproject_doc_fallback_filenames = ["CLAUDE.md"]\n', True)):
        out = io.StringIO()
        home = _config(tmp_path / f"home{int(asked)}", text)
        wiring = app.build(repo, home=home, overrides=OVERRIDES,
                           console=Console(file=out, width=200))
        assert (said in out.getvalue()) is asked
        wiring.session.close()


def test_a_trusted_repositorys_own_settings_still_apply(repo, tmp_path):
    """The user's settings are read before the trust prompt now; the
    repository's must still be read after it, where it is trusted."""
    _write(repo / ".foundry" / "config.toml", "[runtime]\nmax_tool_rounds = 5\n")
    untrusted = _wire(repo, tmp_path / "home", [])
    assert untrusted.config.max_tool_rounds == 40
    untrusted.session.close()
    home = tmp_path / "home2"
    app._remember_trust(home, repo)
    trusted = _wire(repo, home, [])
    assert trusted.config.max_tool_rounds == 5
    trusted.session.close()


def test_a_fallback_named_file_counts_as_existing_for_init(repo, tmp_path):
    home = _config(tmp_path / "home", '[runtime]\nproject_doc_fallback_filenames = ["CLAUDE.md"]\n')
    _write(repo / "CLAUDE.md", "# notes\n")
    wiring = _wire(repo, home, [])
    typist = _repl(wiring, "/init")
    assert wiring.runtime.backend.seen == []
    assert "CLAUDE.md already exists" in _shown(typist)
