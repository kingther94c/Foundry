"""The third set of probe projects, versions of ResearchOS that Foundry once
built: python verify/probes/make_projects_c.py OUT

Each is ResearchOS as it was at the commit before a version landed, with the
SPEC.md section that version was built from appended to its SPEC.md, as the
original runs had it (docs/foundry-acceptance.md). The acceptance suite that
judged the version is the hidden test. ResearchOS imports `tools`, which its
pyproject.toml finds at ../tools: that commit's tools/ is written to beside/,
and run_auto.py puts it next to the workspace, where Foundry's commands can
import it and its file tools cannot reach. The tree the version landed in is
reference/, for check_c.py.

The history is read with git, so this needs a clone that has it (a shallow
one does not). The figures are what the version took when Foundry built it.

r-v24  building a snapshot             75 hidden tests, 10 rounds, US$2.30
r-v30  walk forward by resolution date  53 hidden tests, 12 rounds, US$3.17
r-v32  resampling whole cases          49 hidden tests, 15 rounds, US$0.27 (real API)
"""
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# name: (the commit before, the commit it landed in, its SPEC section, its suite)
# A section or suite named "COMMIT:path" is read from git; a section of None
# is what the landing commit appended to SPEC.md (v32 had no draft).
VERSIONS = {
    "r-v24": ("89aa6b4", "2d776e0",
              "89aa6b4:docs/drafts/researchos-v24/SPEC-section.md",
              "89aa6b4:docs/drafts/researchos-v24/test_build_snapshot_acceptance.py"),
    # The draft of v30's suite had one wrong assertion, put right when it landed.
    "r-v30": ("da867b6", "50c7a37",
              "da867b6:docs/drafts/researchos-v30/SPEC-section.md",
              "50c7a37:ResearchOS/tests/test_walk_forward_acceptance.py"),
    "r-v32": ("8ba36e1", "5b84d7c", None,
              "5b84d7c:ResearchOS/tests/test_resample_cases_acceptance.py"),
}
# A run's notes, which can describe what is still to be built.
LEFT_OUT = ("runs",)


def git(*args: str) -> bytes:
    # The bytes as committed: a clone that turns LF into CRLF on checkout
    # would otherwise hand each machine a different scaffold.
    return subprocess.run(["git", "-C", str(ROOT), "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True).stdout


def has_history() -> bool:
    """Whether every commit the projects are read from is in this clone."""
    commits = {c for v in VERSIONS.values() for c in v[:2]}
    return all(subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{c}^{{commit}}"],
                              capture_output=True).returncode == 0 for c in commits)


def extract(commit: str, folder: str, dest: Path) -> None:
    """``folder`` as it was at ``commit``, written to ``dest``, byte for byte."""
    with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", commit, folder))) as tar:
        tar.extractall(dest.parent / ".extract", filter="data")
    shutil.move(str(dest.parent / ".extract" / folder), str(dest))
    shutil.rmtree(dest.parent / ".extract")
    for name in LEFT_OUT:
        shutil.rmtree(dest / name, ignore_errors=True)


def section(before: str, landed: str, source: str | None) -> str:
    if source:
        return git("show", source).decode("utf-8")
    old = git("show", f"{before}:ResearchOS/SPEC.md")
    new = git("show", f"{landed}:ResearchOS/SPEC.md")
    assert new.startswith(old), "the landing did more to SPEC.md than append to it"
    return new[len(old):].decode("utf-8")


def heading(text: str) -> str:
    return next(line[3:].strip() for line in text.splitlines() if line.startswith("## "))


def project(out: Path, name: str) -> None:
    before, landed, spec_source, suite_source = VERSIONS[name]
    root = out / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    extract(before, "ResearchOS", root / "repo")
    extract(before, "tools", root / "beside" / "tools")
    extract(landed, "ResearchOS", root / "reference")

    added = section(before, landed, spec_source)
    spec = root / "repo" / "SPEC.md"
    text = spec.read_bytes().decode("utf-8")
    spec.write_bytes((text.rstrip("\n") + "\n\n" + added.strip("\n") + "\n").encode("utf-8"))

    hidden = root / "hidden"
    hidden.mkdir()
    (hidden / suite_source.rsplit("/", 1)[1]).write_bytes(git("show", suite_source))

    (root / "task.txt").write_text(
        f'SPEC.md ends with a new section, "{heading(added)}". Implement it, '
        "with tests, as FOUNDRY.md says. `tools` is installed beside this "
        "project and is not yours to change. Run `python -m pytest -q` before "
        "you finish.\n", encoding="utf-8")
    (root / "allow.txt").write_text("python -m pytest*\n", encoding="utf-8")
    (root / "extra.toml").write_text("", encoding="utf-8")
    # tests/test_llm.py imports the Anthropic SDK; without it the suite does
    # not even collect.
    (root / "requires.txt").write_text("anthropic\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python verify/probes/make_projects_c.py OUT", file=sys.stderr)
        return 2
    if not has_history():
        print("this clone does not have the commits the projects are read from; "
              "a shallow clone needs `git fetch --unshallow`", file=sys.stderr)
        return 2
    out = Path(argv[0])
    for name in VERSIONS:
        project(out, name)
        print("wrote", out / name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
