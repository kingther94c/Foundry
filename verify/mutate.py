"""Apply each mutation in turn, run the given tests, and say which survived.

    python verify/mutate.py MUTATIONS.json TEST [TEST ...]
                            [--root DIR] [--from N] [--timeout SECONDS]

MUTATIONS.json is a list of {"name", "file", "old", "new"}. For each, the
first `old` in `file` (relative to the root: this repository unless --root
names another) becomes `new`, the tests run with -x, and the file is put back.
A mutation whose tests still pass survived: nothing checks what it changed.

The tests run once before any mutation and must pass. Each file is restored
byte for byte, line endings included, and the run stops if one is not. The
exit status is 1 if a mutation survived and 2 if the run could not be trusted.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tests_pass(root, tests, timeout):
    """Whether the tests pass in ``root``; a run that times out does not."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0


def mutated(original, old, new):
    """``original`` (bytes) with its first ``old`` replaced by ``new``, both
    given with \\n and matched in the file's own line endings; None if ``old``
    is not there."""
    if b"\r\n" in original:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    old, new = old.encode("utf-8"), new.encode("utf-8")
    return original.replace(old, new, 1) if old in original else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="A mutation check of the given tests.")
    parser.add_argument("mutations", type=Path)
    parser.add_argument("tests", nargs="+")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--from", dest="start", type=int, default=1,
                        help="begin at this mutation (1 is the first)")
    parser.add_argument("--timeout", type=int, default=420)
    args = parser.parse_args(argv)
    mutations = json.loads(args.mutations.read_text(encoding="utf-8"))

    if not tests_pass(args.root, args.tests, args.timeout):
        print("the tests do not pass before any mutation: nothing to learn from them")
        return 2
    survived = []
    for number, mutation in enumerate(mutations, 1):
        if number < args.start:
            continue
        path = args.root / mutation["file"]
        original, before = path.read_bytes(), digest(path)
        changed = mutated(original, mutation["old"], mutation["new"])
        if changed is None or changed == original:
            print(f"{number:2} cannot apply: {mutation['name']} "
                  f"({mutation['old'][:60]!r} is not in {mutation['file']}, or changes nothing)")
            return 2
        path.write_bytes(changed)
        try:
            killed = not tests_pass(args.root, args.tests, args.timeout)
        finally:
            path.write_bytes(original)
        if digest(path) != before:
            print(f"{mutation['file']} was not restored; stopping")
            return 2
        print(f"{number:2} {'killed' if killed else 'SURVIVED'}: {mutation['name']}", flush=True)
        if not killed:
            survived.append(mutation["name"])
    print("survived:", survived)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
