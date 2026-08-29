"""A repository as ordinary git on Windows creates it.

Every other fixture builds its repo through ``run_git``, which inherits
Foundry's own hardening. That is why a regression that broke ``core.autocrlf``
handling passed the whole suite while making Foundry unusable on a real
checkout: a clean repo read as entirely dirty, the patch was refused, and the
run still reported success. These tests use plain git on purpose.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from foundry.core.tools.git import capture_baseline, collect_evidence, run_git

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="core.autocrlf is the Windows default")


def plain_git(args: list[str], cwd) -> subprocess.CompletedProcess:
    """Deliberately not run_git: this is how the user's repo was made."""
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


@pytest.fixture()
def crlf_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e.com"],
                 ["config", "user.name", "T"], ["config", "core.autocrlf", "true"]):
        plain_git(args, repo)
    (repo / "app.py").write_bytes(b"def add(a, b):\r\n    return a - b\r\n")
    plain_git(["add", "-A"], repo)
    plain_git(["commit", "-qm", "init"], repo)
    return repo


def test_a_clean_crlf_repo_is_not_reported_dirty(crlf_repo):
    assert plain_git(["status", "--porcelain"], crlf_repo).stdout.strip() == "", \
        "the fixture itself should be clean"
    baseline = capture_baseline(crlf_repo)
    assert not baseline.dirty_paths
    assert not baseline.untracked_paths


def test_diff_of_a_one_line_change_is_one_line(crlf_repo):
    (crlf_repo / "app.py").write_bytes(b"def add(a, b):\r\n    return a + b\r\n")
    code, out, _ = run_git(["diff"], crlf_repo)
    assert code == 0
    changed = [line for line in out.splitlines()
               if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert changed == ["-    return a - b", "+    return a + b"]


def test_change_attribution_credits_the_session(crlf_repo):
    """With autocrlf broken, everything landed under 'already modified' and the
    session appeared to have changed nothing."""
    baseline = capture_baseline(crlf_repo)
    (crlf_repo / "app.py").write_bytes(b"def add(a, b):\r\n    return a + b\r\n")
    evidence = collect_evidence(crlf_repo, baseline)
    assert "app.py" in evidence.session_changed
    assert not evidence.preexisting_changed
    assert not evidence.head_moved


def test_hardening_still_disables_an_external_diff(crlf_repo, tmp_path):
    """Keeping the system config must not have reopened the config-driven
    program launch."""
    marker = tmp_path / "PWNED.txt"
    payload = tmp_path / "pwn.bat"
    payload.write_text(f'@echo off\r\necho x > "{marker}"\r\n', encoding="utf-8")
    plain_git(["config", "diff.external", str(payload)], crlf_repo)
    (crlf_repo / "app.py").write_bytes(b"def add(a, b):\r\n    return a + b\r\n")

    run_git(["diff"], crlf_repo)
    assert not marker.exists()
