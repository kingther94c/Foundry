"""scripts/delta.py: DELTA.md is the empty template on main and a branch's notes elsewhere.

Each test builds a throwaway repository and runs the script on it the way the
hooks and CI do, as a separate process. git runs with no global or system
config, so the machine's own settings (autocrlf, signing) cannot change a result.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "delta.py"
_spec = importlib.util.spec_from_file_location("delta_script", SCRIPT)
delta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(delta)

TEMPLATE = delta.TEMPLATE


class Checkout:
    def __init__(self, root, env):
        self.root = root
        self.env = env

    def git(self, *args):
        done = subprocess.run(["git", *args], cwd=self.root, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        return done.stdout.decode("utf-8", "replace").strip()

    def run(self, *args, stdin=b""):
        done = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.root), *args],
                              input=stdin, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return (done.returncode, done.stdout.decode("utf-8", "replace"),
                done.stderr.decode("utf-8", "replace"))

    def stop(self, payload=None):
        return self.run("check", "--stop-hook",
                        stdin=json.dumps(payload or {"hook_event_name": "Stop"}).encode())

    def write(self, name, text):
        (self.root / name).write_bytes(text.encode("utf-8"))

    def notes(self):
        return (self.root / "DELTA.md").read_bytes().decode("utf-8")

    def commit(self, message, *names):
        self.git("add", "--", *names)
        self.git("commit", "-q", "-m", message)

    def write_notes(self, summary="Adds b.txt.", verification="- looked at it"):
        text = self.notes()
        text = text.replace(delta.PLACEHOLDER, summary)
        text = text.replace("## Verification\n\n", "## Verification\n\n%s\n\n" % verification)
        self.write("DELTA.md", text)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
    c = Checkout(root, env)
    c.git("init", "-q", "-b", "main")
    c.write("a.txt", "a\n")
    c.write("DELTA.md", TEMPLATE)
    c.commit("base", "a.txt", "DELTA.md")
    return c


def test_main_keeps_the_empty_template(repo):
    repo.write("DELTA.md", "leftover notes\n")
    assert repo.run("refresh")[0] == 0
    assert repo.notes() == TEMPLATE
    assert repo.git("status", "--porcelain") == ""
    assert repo.run("check")[0] == 0

    repo.write("DELTA.md", TEMPLATE + "stray\n")
    repo.commit("oops", "DELTA.md")
    code, _, err = repo.run("check")
    assert code == 1 and "empty template" in err


def test_a_branch_that_changes_nothing_is_left_alone(repo):
    repo.git("checkout", "-q", "-b", "feature")
    code, out, _ = repo.run("refresh")
    assert code == 0 and "DELTA.md records" in out
    assert repo.notes() == TEMPLATE
    assert repo.run("check")[0] == 0
    assert repo.stop()[0] == 0


def test_refresh_lists_what_differs_from_main(repo):
    base = repo.git("rev-parse", "--short=7", "HEAD")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("b.txt", "b\n")
    repo.commit("add b", "b.txt")
    repo.write("a.txt", "changed\n")
    repo.write("c.txt", "untracked\n")
    assert repo.run("refresh")[0] == 0
    text = repo.notes()
    generated = text[text.index(delta.AUTO_BEGIN):]
    assert "`feature`, from main at `%s`." % base in generated
    assert "| M | `a.txt` |" in generated
    assert "| A | `b.txt` |" in generated
    assert "| A | `c.txt` |" in generated
    assert "DELTA.md` |" not in generated
    assert text.startswith(TEMPLATE[:TEMPLATE.index(delta.AUTO_BEGIN)])

    before = (repo.root / "DELTA.md").stat().st_mtime_ns
    assert repo.run("refresh")[0] == 0
    assert repo.notes() == text
    assert (repo.root / "DELTA.md").stat().st_mtime_ns == before


def test_a_crlf_checkout_is_not_rewritten(repo):
    raw = TEMPLATE.replace("\n", "\r\n").encode("utf-8")
    (repo.root / "DELTA.md").write_bytes(raw)
    assert repo.run("refresh")[0] == 0
    assert (repo.root / "DELTA.md").read_bytes() == raw
    assert repo.run("check")[0] == 0


def test_commits_without_a_summary_fail(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("b.txt", "b\n")
    repo.commit("add b", "b.txt")
    code, _, err = repo.run("check")
    assert code == 1 and "no Summary" in err

    code, _, err = repo.stop()
    assert code == 2 and "Summary" in err
    # Sent back once already this turn: never twice.
    assert repo.stop({"stop_hook_active": True})[0] == 0


def test_an_uncommitted_change_needs_a_summary_at_the_end_of_a_turn(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("a.txt", "changed\n")
    assert repo.stop()[0] == 2
    assert repo.run("check")[0] == 0  # CI: no commits beyond main yet


def test_notes_that_lag_behind_later_commits(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("b.txt", "b\n")
    repo.run("refresh")
    repo.write_notes()
    repo.commit("add b", "b.txt", "DELTA.md")
    assert repo.stop()[0] == 0
    assert repo.run("check")[0] == 0

    repo.write("d.txt", "d\n")
    repo.commit("add d", "d.txt")
    code, _, err = repo.stop()
    assert code == 2 and "d.txt" in err

    repo.write_notes(summary="Adds b.txt.", verification="- and d.txt")
    assert repo.stop()[0] == 0  # being edited
    repo.commit("notes", "DELTA.md")
    assert repo.stop()[0] == 0


def test_ci_warns_when_the_committed_file_list_is_out_of_date(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write_notes()
    repo.write("b.txt", "b\n")
    repo.commit("add b", "b.txt", "DELTA.md")  # no refresh first
    code, _, err = repo.run("check")
    assert code == 0 and "out of date" in err


def test_reset_commit_lands_the_branch(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("b.txt", "b\n")
    repo.run("refresh")
    repo.write_notes(summary="Adds b.txt, for the record.")
    repo.commit("add b", "b.txt", "DELTA.md")

    code, out, _ = repo.run("reset", "--commit")
    assert code == 0
    assert repo.git("log", "-1", "--format=%s") == delta.RESET_SUBJECT
    assert "Adds b.txt, for the record." in repo.git("log", "-1", "--format=%b")
    assert repo.git("show", "HEAD:DELTA.md") + "\n" == TEMPLATE
    assert repo.git("status", "--porcelain") == ""

    # Ready to land: nothing rewrites the template or holds the turn up.
    assert repo.run("refresh")[0] == 0
    assert repo.notes() == TEMPLATE
    assert repo.run("check")[0] == 0
    assert repo.stop()[0] == 0

    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--ff-only", "feature")
    assert repo.run("check")[0] == 0
    assert repo.git("log", "--grep", "for the record", "--format=%s") == delta.RESET_SUBJECT


def test_a_pull_request_merge_of_a_landed_branch_passes(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("b.txt", "b\n")
    repo.write_notes()
    repo.commit("add b", "b.txt", "DELTA.md")
    assert repo.run("reset", "--commit")[0] == 0
    repo.git("checkout", "-q", "main")
    repo.write("e.txt", "e\n")
    repo.commit("main moves on", "e.txt")
    # What a pull_request CI run checks out: main merged with the branch, detached.
    repo.git("checkout", "-q", "--detach", "main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    assert repo.run("check")[0] == 0


def test_reset_commit_is_refused_on_main(repo):
    code, _, err = repo.run("reset", "--commit")
    assert code == 1 and "branch" in err


def test_a_detached_head_at_main_counts_as_main(repo):
    repo.git("checkout", "-q", "--detach", "main")
    repo.write("DELTA.md", "notes\n")
    assert repo.run("check")[0] == 1
    assert repo.run("refresh")[0] == 0
    assert repo.notes() == TEMPLATE


def test_origin_main_stands_in_for_a_missing_local_main(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.git("checkout", "-q", "-b", "feature")
    repo.git("update-ref", "refs/remotes/origin/main", base)
    repo.git("branch", "-q", "-D", "main")
    repo.write("b.txt", "b\n")
    repo.commit("add b", "b.txt")
    assert repo.run("refresh")[0] == 0
    assert "| A | `b.txt` |" in repo.notes()
    assert repo.run("check")[0] == 1


def test_without_main_nothing_is_touched_and_nothing_blocks(repo):
    repo.git("branch", "-q", "-m", "main", "trunk")
    repo.write("DELTA.md", "notes\n")
    assert repo.run("refresh")[0] == 0
    assert repo.notes() == "notes\n"
    code, _, err = repo.run("check")
    assert code == 1 and "fetch-depth" in err
    assert repo.stop()[0] == 0
