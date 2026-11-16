"""Check the third set: each version's hidden suite fails on the tree before it
and passes on the tree it landed in. python verify/probes/check_c.py [PROJECTS [NAME ...]]

PROJECTS is where make_projects_c.py wrote them; by default, projects-c/ here.
NAME limits the check to those projects. Only the hidden suite runs, on a copy
of each tree with the project's beside/ next to it, as run_auto.py lays them
out. A suite that passes on the tree before judges nothing, and one that fails
on the tree the version landed in cannot be passed: either is a FAIL line and
an exit status of 1.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run_hidden(project: Path, tree: str) -> tuple[int, str]:
    """The hidden suite on a copy of ``project/tree``; its exit status and last line."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(project / tree, ws)
        beside = project / "beside"
        for item in sorted(beside.iterdir()) if beside.is_dir() else []:
            shutil.copytree(item, Path(tmp) / item.name)
        names = []
        for test in sorted((project / "hidden").glob("*.py")):
            shutil.copy(test, ws / "tests" / test.name)
            names.append(f"tests/{test.name}")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               *names], cwd=ws, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=900)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    return done.returncode, (lines[-1] if lines else done.stderr.strip()[-200:])


def main(argv: list[str]) -> int:
    projects = Path(argv[0]) if argv else HERE / "projects-c"
    names = argv[1:] or sorted(p.name for p in projects.iterdir() if (p / "reference").is_dir())
    problems = []
    for name in names:
        project = projects / name
        code, last = run_hidden(project, "repo")
        print(f"{name}: scaffold -> {last}")
        if code != 1:
            problems.append(f"FAIL {name}: the hidden suite does not fail on the tree before "
                            f"the version (exit {code}): it judges nothing")
        code, last = run_hidden(project, "reference")
        print(f"{name}: reference -> {last}")
        if code != 0:
            problems.append(f"FAIL {name}: the hidden suite fails on the tree the version "
                            f"landed in (exit {code}): it cannot be passed")
    for line in problems:
        print(line)
    if not problems:
        print(f"OK: {len(names)} versions, each judged by its own suite")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
