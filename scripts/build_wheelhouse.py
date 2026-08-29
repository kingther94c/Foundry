"""Build and verify the offline wheelhouse.

The install target is a machine with no package index:

    pip install --no-index --find-links wheelhouse foundry

A single sdist-only transitive dependency breaks that, and it would only surface
on the locked-down machine. ``--only-binary :all:`` turns that into a loud
failure here instead.

    python scripts/build_wheelhouse.py            # download + build + verify
    python scripts/build_wheelhouse.py --verify   # verify an existing wheelhouse
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEELHOUSE = ROOT / "wheelhouse"
PY_TAG = "cp312"
PLATFORM = "win_amd64"


def run(argv: list[str]) -> int:
    print("$", " ".join(argv))
    return subprocess.run(argv, cwd=ROOT).returncode


def download() -> int:
    WHEELHOUSE.mkdir(exist_ok=True)
    code = run([
        sys.executable, "-m", "pip", "download",
        "-r", "requirements.in",
        "-d", str(WHEELHOUSE),
        "--only-binary", ":all:",
        "--platform", PLATFORM,
        "--python-version", "3.12",
        "--implementation", "cp",
    ])
    if code != 0:
        # Pure-python wheels need no platform pinning; retry without it so a
        # py3-none-any dependency does not look like a failure.
        code = run([
            sys.executable, "-m", "pip", "download",
            "-r", "requirements.in", "-d", str(WHEELHOUSE), "--only-binary", ":all:",
        ])
    return code


def build_wheel() -> int:
    return run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
                "-w", str(WHEELHOUSE)])


def verify() -> int:
    if not WHEELHOUSE.is_dir():
        print("wheelhouse/ does not exist; run without --verify first")
        return 1

    wheels = sorted(WHEELHOUSE.glob("*.whl"))
    others = [p for p in WHEELHOUSE.iterdir() if p.suffix not in (".whl",)]

    print(f"{len(wheels)} wheels in {WHEELHOUSE}")
    problems: list[str] = []

    for wheel in wheels:
        name = wheel.name
        print(f"  {name}")
        if not (name.endswith("-py3-none-any.whl")
                or name.endswith("-py2.py3-none-any.whl")
                or (PY_TAG in name and PLATFORM in name)):
            problems.append(f"{name} is neither pure-python nor {PY_TAG}-{PLATFORM}")

    for other in others:
        problems.append(f"{other.name} is not a wheel; an sdist breaks --no-index installs")

    if not any(w.name.startswith("foundry-") for w in wheels):
        problems.append("the foundry wheel itself is missing")

    if problems:
        print("\nproblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nwheelhouse is complete and installable offline")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="check an existing wheelhouse without downloading")
    args = parser.parse_args()

    if args.verify:
        return verify()

    for step in (download, build_wheel):
        code = step()
        if code != 0:
            return code
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
