"""The probe projects under verify/probes/ measure something (D-199).

A probe is a scaffold, a task, and hidden tests Foundry never sees; a change
to the agent is judged by how many of them it passes before and after. That
only means something if the hidden tests fail on the scaffold and can be
passed. This runs them both ways, in subprocesses, so it is the slowest test
of the kit and is kept apart from tests/test_verify_kit.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / "verify"


def _script(name, *args):
    return subprocess.run([sys.executable, str(KIT / "probes" / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_the_probes_are_written_whole_and_their_hidden_tests_judge_something(tmp_path):
    """Each project has what the runner reads; and the second set's hidden
    tests fail on what Foundry is given and pass on a reference fix."""
    projects = tmp_path / "projects"

    made = _script("make_projects_b.py", projects)
    assert made.returncode == 0, made.stderr[-2000:]
    names = sorted(p.name for p in projects.iterdir())
    assert len(names) == 11
    for name in names:
        for part in ("repo", "hidden", "task.txt", "allow.txt", "extra.toml"):
            assert (projects / name / part).exists(), f"{name}/{part}"
        assert list((projects / name / "hidden").glob("*.py")), name

    checked = _script("check_b.py", projects)
    assert checked.returncode == 0, checked.stdout[-3000:]
    assert checked.stdout.count("scaffold ->") == 5
    assert "OK: 5 sets of hidden tests" in checked.stdout


def test_hidden_tests_that_judge_nothing_or_cannot_be_passed_are_reported(tmp_path):
    projects = tmp_path / "projects"
    assert _script("make_projects_b.py", projects).returncode == 0
    for name, body in (("i-scope", "assert True"), ("l-repro", "assert False")):
        for hidden in (projects / name / "hidden").glob("*.py"):
            hidden.write_text(f"def test_it():\n    {body}\n", encoding="utf-8")

    checked = _script("check_b.py", projects, "i-scope", "l-repro")

    assert checked.returncode == 1
    assert "FAIL i-scope: the hidden tests pass on the scaffold" in checked.stdout
    assert "FAIL l-repro: the hidden tests fail on the reference fix" in checked.stdout
    assert "OK:" not in checked.stdout


# -- the third set: versions of ResearchOS, rebuilt from history (D-201) ------

def _has_history() -> bool:
    """Asked of git here, not of the generator, which is under test."""
    commits = ("89aa6b4", "2d776e0", "da867b6", "50c7a37", "8ba36e1", "5b84d7c")
    return all(subprocess.run(["git", "-C", str(KIT.parent), "cat-file", "-e", f"{c}^{{commit}}"],
                              capture_output=True).returncode == 0 for c in commits)


history = pytest.mark.skipif(not _has_history(), reason="a clone without the history the "
                             "third set is rebuilt from (CI's test job fetches it whole)")


@pytest.fixture(scope="module")
def third_set(tmp_path_factory):
    projects = tmp_path_factory.mktemp("third") / "projects-c"
    made = _script("make_projects_c.py", projects)
    assert made.returncode == 0, made.stderr[-2000:]
    return projects


@history
def test_the_third_set_is_each_version_as_foundry_was_given_it(third_set):
    """The tree before the version, its SPEC section appended, `tools` beside
    it, its acceptance suite hidden; a run's notes are left out."""
    assert sorted(p.name for p in third_set.iterdir()) == ["r-v24", "r-v30", "r-v32"]
    for name, heading, suite in (
            ("r-v24", "Building a snapshot (v24)", "test_build_snapshot_acceptance.py"),
            ("r-v30", "Walk forward by resolution date (v30)", "test_walk_forward_acceptance.py"),
            ("r-v32", "Resampling whole cases (v32)", "test_resample_cases_acceptance.py")):
        project = third_set / name
        spec = (project / "repo" / "SPEC.md").read_bytes()
        assert f"\n## {heading}\n".encode() in spec and b"\r" not in spec, name
        assert f'"{heading}"' in (project / "task.txt").read_text(encoding="utf-8")
        assert [p.name for p in (project / "hidden").iterdir()] == [suite]
        assert not (project / "repo" / "tests" / suite).exists(), name
        assert (project / "beside" / "tools" / "tools" / "__init__.py").is_file()
        assert not (project / "repo" / "runs").exists(), name
        assert (project / "requires.txt").read_text(encoding="utf-8").split() == ["anthropic"]
    # Where a version had no draft, its section is what the landing appended:
    # the rebuilt SPEC.md is the one it landed with.
    assert ((third_set / "r-v32" / "repo" / "SPEC.md").read_bytes()
            == (third_set / "r-v32" / "reference" / "SPEC.md").read_bytes())


@history
def test_each_versions_suite_fails_before_it_and_passes_where_it_landed(third_set):
    checked = _script("check_c.py", third_set)

    assert checked.returncode == 0, checked.stdout[-3000:]
    assert checked.stdout.count("scaffold -> ") == checked.stdout.count("reference -> ") == 3
    assert "OK: 3 versions, each judged by its own suite" in checked.stdout


@history
def test_a_suite_that_judges_nothing_or_cannot_be_passed_is_reported(third_set, tmp_path):
    import shutil

    copy = tmp_path / "projects-c"
    for name in ("r-v30", "r-v32"):
        shutil.copytree(third_set / name, copy / name)
    for name, body in (("r-v30", "assert True"), ("r-v32", "assert False")):
        for hidden in (copy / name / "hidden").glob("*.py"):
            hidden.write_text(f"def test_it():\n    {body}\n", encoding="utf-8")

    checked = _script("check_c.py", copy)

    assert checked.returncode == 1
    assert "FAIL r-v30: the hidden suite does not fail on the tree before" in checked.stdout
    assert "FAIL r-v32: the hidden suite fails on the tree the version landed in" in checked.stdout
    assert "OK:" not in checked.stdout


def test_the_third_set_says_so_when_the_clone_lacks_its_history(tmp_path, monkeypatch):
    sys.path.insert(0, str(KIT / "probes"))
    try:
        import make_projects_c
        assert make_projects_c.has_history() is _has_history()
        monkeypatch.setitem(make_projects_c.VERSIONS, "r-none",
                            ("0" * 40, "0" * 40, None, "x:y.py"))
        assert make_projects_c.has_history() is False
        assert make_projects_c.main([str(tmp_path / "out")]) == 2
        assert not (tmp_path / "out").exists()
    finally:
        sys.path.remove(str(KIT / "probes"))
        sys.modules.pop("make_projects_c", None)
