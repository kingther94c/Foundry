"""Build and verify the offline wheelhouse, its dependencies pinned by hash.

The install target is a machine with no package index:

    pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock
    pip install --no-index --find-links wheelhouse --no-deps foundry

A single sdist-only transitive dependency breaks that, and it would only surface
on the locked-down machine. ``--only-binary :all:`` turns that into a loud
failure here instead.

``requirements.lock`` pins every dependency to a version and the sha256 of its
wheel (D-002, OQ-20, D-164). With it, the download is checked against those
hashes and so is the install, and a wheel that changed -- on the index, or in
the wheelhouse on its way to the machine -- fails both. Without it, the
wheelhouse is built from requirements.in, unpinned; ``--lock`` then writes the
lock from the wheels that were downloaded, to be read and committed.

    python scripts/build_wheelhouse.py            # download + build + verify
    python scripts/build_wheelhouse.py --lock     # write requirements.lock from wheelhouse/
    python scripts/build_wheelhouse.py --verify   # verify an existing wheelhouse
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEELHOUSE = ROOT / "wheelhouse"
LOCK = ROOT / "requirements.lock"
PY_TAG = "cp312"
PLATFORM = "win_amd64"

LOCK_HEADER = """\
# Every dependency of foundry, pinned to a version and the sha256 of its wheel
# (D-002, D-164). Written by `python scripts/build_wheelhouse.py --lock` from
# the wheels it downloaded: read it before committing it. Installed with
#   pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock
"""

_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def run(argv: list[str]) -> int:
    print("$", " ".join(argv))
    return subprocess.run(argv, cwd=ROOT).returncode


def normalize(name: str) -> str:
    """A project's name as pip compares them (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_key(wheel: Path) -> tuple[str, str]:
    """A wheel's project and version, from its file name (PEP 427)."""
    parts = wheel.name[:-len(".whl")].split("-")
    if len(parts) < 5:
        raise ValueError(f"{wheel.name} is not a wheel's name")
    return normalize(parts[0]), parts[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dependency_wheels(wheelhouse: Path) -> list[Path]:
    """The wheels foundry depends on: every wheel but its own, built here."""
    return [w for w in sorted(wheelhouse.glob("*.whl")) if wheel_key(w)[0] != "foundry"]


def read_lock(lock: Path) -> dict[tuple[str, str], set[str]]:
    """Each pinned project and version, with the hashes it may have. Raises
    ValueError for a requirement that is not ``name==version`` with a hash:
    pip's hash-checking mode would refuse it on the target machine."""
    text = lock.read_text(encoding="utf-8")
    pins: dict[tuple[str, str], set[str]] = {}
    for line in text.replace("\\\n", " ").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pinned = _PIN.match(line)
        hashes = set(_HASH.findall(line))
        if pinned is None or not hashes:
            raise ValueError(f"requirements.lock: {line!r} is not name==version "
                             "with --hash=sha256:...")
        pins.setdefault((normalize(pinned.group(1)), pinned.group(2)), set()).update(hashes)
    return pins


def write_lock(wheelhouse: Path, lock: Path) -> int:
    """The lock, from the dependency wheels in the wheelhouse; how many
    projects it pins."""
    pins: dict[tuple[str, str], set[str]] = {}
    for wheel in dependency_wheels(wheelhouse):
        pins.setdefault(wheel_key(wheel), set()).add(sha256(wheel))
    if not pins:
        raise ValueError(f"no dependency wheels in {wheelhouse}; build it first")
    names = [name for name, _ in pins]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        raise ValueError(f"more than one version of {', '.join(twice)} in {wheelhouse}: "
                         "clear it and build it again")
    lines = [LOCK_HEADER]
    for (name, version), hashes in sorted(pins.items()):
        lines.append(f"{name}=={version} \\\n"
                     + " \\\n".join(f"    --hash=sha256:{h}" for h in sorted(hashes)) + "\n")
    lock.write_text("".join(lines), encoding="utf-8", newline="\n")
    return len(pins)


def lock_problems(wheelhouse: Path, lock: Path) -> list[str]:
    """Where the wheelhouse and the lock disagree."""
    try:
        pins = read_lock(lock)
        wheels = dependency_wheels(wheelhouse)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    found: set[tuple[str, str]] = set()
    for wheel in wheels:
        key = wheel_key(wheel)
        found.add(key)
        if key not in pins:
            problems.append(f"{wheel.name} is not pinned in requirements.lock")
        elif sha256(wheel) not in pins[key]:
            problems.append(f"{wheel.name} is not the wheel requirements.lock pins: "
                            "its sha256 differs")
    for name, version in sorted(set(pins) - found):
        problems.append(f"requirements.lock pins {name}=={version}; no wheel of it is in "
                        "the wheelhouse")
    return problems


def download_argv(pinned: bool, platform: bool) -> list[str]:
    argv = [sys.executable, "-m", "pip", "download",
            "-r", "requirements.lock" if pinned else "requirements.in",
            "-d", str(WHEELHOUSE), "--only-binary", ":all:"]
    if pinned:
        # Every wheel checked against the lock; a dependency it does not pin
        # is refused, so a lock that falls behind requirements.in says so.
        argv.append("--require-hashes")
    if platform:
        argv += ["--platform", PLATFORM, "--python-version", "3.12", "--implementation", "cp"]
    return argv


def download() -> int:
    WHEELHOUSE.mkdir(exist_ok=True)
    pinned = LOCK.is_file()
    if not pinned:
        print("requirements.lock does not exist: the wheelhouse is not pinned by hash "
              "(OQ-20); --lock writes it from what is downloaded")
    code = run(download_argv(pinned, platform=True))
    if code != 0:
        # Pure-python wheels need no platform pinning; retry without it so a
        # py3-none-any dependency does not look like a failure.
        code = run(download_argv(pinned, platform=False))
    return code


def build_wheel() -> int:
    return run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
                "-w", str(WHEELHOUSE)])


def verify(wheelhouse: Path | None = None, lock: Path | None = None) -> int:
    wheelhouse = wheelhouse or WHEELHOUSE
    lock = lock or LOCK
    if not wheelhouse.is_dir():
        print("wheelhouse/ does not exist; run without --verify first")
        return 1

    wheels = sorted(wheelhouse.glob("*.whl"))
    others = [p for p in wheelhouse.iterdir() if p.suffix not in (".whl",)]

    print(f"{len(wheels)} wheels in {wheelhouse}")
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

    if lock.is_file():
        problems += lock_problems(wheelhouse, lock)
    else:
        print("\nnot pinned by hash: requirements.lock does not exist (OQ-20)")

    if problems:
        print("\nproblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nwheelhouse is complete and installable offline"
          + (", every dependency pinned by hash" if lock.is_file() else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true",
                      help="check an existing wheelhouse without downloading")
    mode.add_argument("--lock", action="store_true",
                      help="write requirements.lock from the wheels in wheelhouse/")
    args = parser.parse_args(argv)

    if args.verify:
        return verify()
    if args.lock:
        try:
            count = write_lock(WHEELHOUSE, LOCK)
        except (ValueError, OSError) as exc:
            print(exc)
            return 1
        print(f"requirements.lock pins {count} projects; read it, then commit it")
        return verify()

    for step in (download, build_wheel):
        code = step()
        if code != 0:
            return code
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
