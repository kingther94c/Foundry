"""Keep DELTA.md: how the current branch differs from main.

    python scripts/delta.py refresh              rewrite the generated file list
    python scripts/delta.py check                exit 1 if DELTA.md is wrong here (CI)
    python scripts/delta.py check --stop-hook    exit 2, reason on stderr, if the notes lag
    python scripts/delta.py reset [--commit]     restore the empty template

DELTA.md has a hand-written part (Summary, Changes, Decisions and open points,
Verification) and a generated part between the auto markers: the files that
differ from main. On main the file is always the empty template. A branch fills
it in, and `reset --commit` empties it again as the branch's last commit before
it lands, keeping the notes in that commit's message so `git log --grep` still
finds them. AGENT.md, "Branch notes", is the workflow.

Stdlib only and no newer than Python 3.8: Claude Code hooks run this with
whichever `python` comes first on PATH, which is not always Foundry's 3.12.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

NAME = "DELTA.md"
MAIN = "main"
RESET_SUBJECT = "Reset DELTA.md for landing"
PLACEHOLDER = "_Not written yet._"
AUTO_BEGIN = "<!-- auto:begin: written by scripts/delta.py refresh; do not edit by hand -->"
AUTO_END = "<!-- auto:end -->"

TEMPLATE = (
    "# Delta from main\n"
    "\n"
    "This file records how this branch differs from main: what changed, why, and\n"
    "how it was checked. On main it is this empty template, and\n"
    "`python scripts/delta.py reset --commit` restores it as the last commit\n"
    "before a branch lands. See AGENT.md, \"Branch notes\".\n"
    "\n"
    "## Summary\n"
    "\n"
    + PLACEHOLDER + "\n"
    "\n"
    "## Changes\n"
    "\n"
    "## Decisions and open points\n"
    "\n"
    "## Verification\n"
    "\n"
    + AUTO_BEGIN + "\n"
    + AUTO_END + "\n"
)

REMINDER = (
    "DELTA.md records how this branch differs from main. Write its Summary as "
    "soon as the branch changes anything, and keep Changes and Verification "
    "current as you commit (AGENT.md, \"Branch notes\")."
)


class GitError(Exception):
    pass


class Repo:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / NAME

    def git(self, *args):
        """stdout of a git command, decoded; GitError if it fails."""
        done = subprocess.run(["git"] + list(args), cwd=str(self.root),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if done.returncode != 0:
            message = done.stderr.decode("utf-8", "replace").strip()
            raise GitError("git %s: %s" % (" ".join(args), message))
        return done.stdout.decode("utf-8", "replace")

    def try_git(self, *args):
        try:
            return self.git(*args)
        except GitError:
            return None

    def read(self):
        """DELTA.md in the working tree with LF line endings, or None."""
        try:
            with open(str(self.path), encoding="utf-8", newline="") as f:
                return f.read().replace("\r\n", "\n")
        except FileNotFoundError:
            return None

    def write(self, text):
        """Write only on a real change: a checkout with CRLF endings is left alone."""
        if self.read() == text:
            return False
        with open(str(self.path), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return True

    def show(self, rev):
        """DELTA.md as of a commit, or None where it did not exist."""
        text = self.try_git("show", "%s:%s" % (rev, NAME))
        return None if text is None else text.replace("\r\n", "\n")


class Where:
    """Which side of main the checkout is on."""

    def __init__(self, kind, branch=None, base=None):
        self.kind = kind  # "main", "branch" or "unknown"
        self.branch = branch  # None on a detached HEAD
        self.base = base  # the merge base with main, on a branch


def locate(repo):
    branch = (repo.try_git("symbolic-ref", "--quiet", "--short", "HEAD") or "").strip() or None
    if branch == MAIN:
        return Where(MAIN, branch)
    main = None
    # A CI checkout of a branch has origin/main and no local main.
    for ref in ("refs/heads/" + MAIN, "refs/remotes/origin/" + MAIN):
        sha = repo.try_git("rev-parse", "--verify", "--quiet", ref + "^{commit}")
        if sha:
            main = sha.strip()
            break
    head = (repo.try_git("rev-parse", "--verify", "--quiet", "HEAD") or "").strip()
    if main is None or not head:
        return Where("unknown", branch)
    if branch is None and head == main:
        return Where(MAIN)
    base = repo.try_git("merge-base", main, head)
    if not base:
        return Where("unknown", branch)
    return Where("branch", branch, base.strip())


def commits_beyond(repo, base):
    """Non-merge commits on HEAD that main does not have, newest first."""
    return repo.git("rev-list", "--no-merges", base + "..HEAD").split()


def changed_files(repo, base):
    """(status, path) for every file the working tree has that differs from base."""
    fields = repo.git("diff", "--name-status", "--no-renames", "-z", base).split("\0")
    found = {}
    for i in range(0, len(fields) - 1, 2):
        found[fields[i + 1]] = fields[i]
    for path in repo.git("ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path:
            found[path] = "A"
    found.pop(NAME, None)
    return [(found[path], path) for path in sorted(found)]


def hand_written(text):
    """Everything above the generated part."""
    if text is None:
        return None
    at = text.find(AUTO_BEGIN)
    return text if at < 0 else text[:at]


def summary(text):
    lines = hand_written(text).split("\n")
    out = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip() == "## Summary"
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def unwritten(text):
    return text is None or summary(text) in ("", PLACEHOLDER)


def generated(where, commits, files):
    """The text between the auto markers; empty while the branch changes nothing."""
    if not commits and not files:
        return ""
    name = "`%s`" % where.branch if where.branch else "A detached HEAD"
    lines = ["%s, from main at `%s`." % (name, where.base[:7]), ""]
    if not files:
        lines.append("No file differs from main.")
    else:
        lines.append("Files that differ from main (the working tree; this file left out):")
        lines.append("")
        lines.append("| Status | Path |")
        lines.append("|---|---|")
        for status, path in files:
            lines.append("| %s | `%s` |" % (status, path))
    return "\n".join(lines) + "\n"


def with_generated(text, block):
    """text with the generated part replaced (or appended, if the markers are gone)."""
    begin = text.find(AUTO_BEGIN)
    if begin < 0:
        return text.rstrip("\n") + "\n\n" + AUTO_BEGIN + "\n" + block + AUTO_END + "\n"
    end = text.find(AUTO_END, begin)
    tail = text[end + len(AUTO_END):] if end >= 0 else ""
    return text[:begin] + AUTO_BEGIN + "\n" + block + AUTO_END + (tail if tail.strip() else "\n")


def landing_ready(repo, base, text):
    """The branch's last commit is `reset --commit` and the file is still the template."""
    if text != TEMPLATE:
        return False
    last = repo.git("log", "-1", "--no-merges", "--topo-order", "--format=%s",
                    base + "..HEAD").strip()
    return last == RESET_SUBJECT


def refresh(repo, quiet=False):
    where = locate(repo)
    if where.kind == MAIN:
        repo.write(TEMPLATE)
        return 0
    if where.kind != "branch":
        return 0
    text = repo.read()
    if text is not None and landing_ready(repo, where.base, text):
        return 0
    block = generated(where, commits_beyond(repo, where.base), changed_files(repo, where.base))
    repo.write(with_generated(TEMPLATE if text is None else text, block))
    if not quiet:
        print(REMINDER)
    return 0


def stale(repo, base, text):
    """Files changed by commits after the notes were last edited, if the notes are idle."""
    at_head = repo.show("HEAD")
    if at_head is None or hand_written(at_head) != hand_written(text):
        return []  # never committed, or being edited right now
    last = None
    for sha in repo.git("log", "--no-merges", "--topo-order", "--format=%H", base + "..HEAD",
                           "--", NAME).split():
        if hand_written(repo.show(sha)) != hand_written(repo.show(sha + "^")):
            last = sha
            break
    if last is None:
        return []
    names = repo.git("diff", "--name-only", "-z", last, "HEAD").split("\0")
    return [n for n in names if n and n != NAME]


def check(repo):
    where = locate(repo)
    text = repo.read()
    if where.kind == "unknown":
        print("delta: cannot find main or the merge base with it (a CI checkout needs "
              "fetch-depth: 0)", file=sys.stderr)
        return 1
    if where.kind == MAIN:
        if text != TEMPLATE:
            print("delta: on main %s must be the empty template; run "
                  "`python scripts/delta.py refresh` and commit it" % NAME, file=sys.stderr)
            return 1
        return 0
    commits = commits_beyond(repo, where.base)
    if not commits:
        return 0
    if text is not None and landing_ready(repo, where.base, text):
        return 0
    if unwritten(text):
        print("delta: this branch has %d commit(s) beyond main and %s has no Summary "
              "(AGENT.md, \"Branch notes\")" % (len(commits), NAME), file=sys.stderr)
        return 1
    current = with_generated(text, generated(where, commits, changed_files(repo, where.base)))
    if current != text:
        print("delta: warning: the file list in %s is out of date; "
              "`python scripts/delta.py refresh` rewrites it" % NAME, file=sys.stderr)
    return 0


def stop_hook(repo, payload):
    """A Claude Code Stop hook: exit 2 sends the agent back with stderr as the reason."""
    try:
        data = json.loads(payload) if payload.strip() else {}
    except ValueError:
        data = {}
    if isinstance(data, dict) and data.get("stop_hook_active"):
        return 0  # already sent back once this turn
    try:
        refresh(repo, quiet=True)
        where = locate(repo)
        if where.kind != "branch":
            return 0
        text = repo.read()
        commits = commits_beyond(repo, where.base)
        if not commits and not changed_files(repo, where.base):
            return 0
        if text is not None and landing_ready(repo, where.base, text):
            return 0
        if unwritten(text):
            print("%s has no Summary, but this branch changes files. Write the Summary, "
                  "Changes and Verification (AGENT.md, \"Branch notes\"), then finish."
                  % NAME, file=sys.stderr)
            return 2
        behind = stale(repo, where.base, text)
        if behind:
            shown = ", ".join(behind[:5]) + (", ..." if len(behind) > 5 else "")
            print("%s was last updated before commits that changed %d file(s) (%s). Bring "
                  "its Changes and Verification up to date, then finish."
                  % (NAME, len(behind), shown), file=sys.stderr)
            return 2
        return 0
    except (GitError, OSError) as exc:
        # Never hold a session up over this script's own trouble.
        print("delta: %s" % exc, file=sys.stderr)
        return 0


def reset(repo, commit):
    where = locate(repo)
    if commit and where.kind != "branch":
        print("delta: reset --commit is for a branch about to land", file=sys.stderr)
        return 1
    old = repo.read()
    repo.write(TEMPLATE)
    if not commit:
        return 0
    if repo.show("HEAD") == TEMPLATE:
        print("delta: %s is already the template at HEAD; nothing to commit" % NAME)
        return 0
    notes = hand_written(old or "")
    at = notes.find("## Summary")
    body = (notes[at:] if at >= 0 else notes).strip() or "(no notes)"
    repo.git("add", "--", NAME)
    repo.git("commit", "-m", RESET_SUBJECT, "-m", body, "--", NAME)
    print("delta: committed %r" % RESET_SUBJECT)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="delta.py", description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]),
                        help="the repository (default: the one holding this script)")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    sub.add_parser("refresh", help="rewrite the generated part")
    checking = sub.add_parser("check", help="exit 1 if DELTA.md is wrong for this checkout")
    checking.add_argument("--stop-hook", action="store_true",
                          help="run as a Claude Code Stop hook (reads its JSON on stdin)")
    resetting = sub.add_parser("reset", help="restore the empty template")
    resetting.add_argument("--commit", action="store_true",
                           help="commit it, with the notes as the commit message")
    args = parser.parse_args(argv)
    repo = Repo(args.repo)
    try:
        if args.command == "refresh":
            return refresh(repo)
        if args.command == "reset":
            return reset(repo, args.commit)
        if args.stop_hook:
            return stop_hook(repo, "" if sys.stdin is None or sys.stdin.isatty()
                             else sys.stdin.read())
        return check(repo)
    except GitError as exc:
        print("delta: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
