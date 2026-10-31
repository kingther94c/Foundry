"""Check a ResearchOS v23 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is reference/snapshot.py added to a copy of ResearchOS/. Run it with an
interpreter that has pytest and anthropic.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROS = REPO / "ResearchOS"
NL = chr(10)
HIDDEN = "test_snapshot_tables_acceptance.py"


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(HERE / "reference" / "snapshot.py", pkg / "snapshot.py")
    return pkg


def build_package(ws: Path, dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(Path(ws) / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    return pkg


def env_for(root: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(root), str(REPO / "tools")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def suite(build) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if build:
            build_package(Path(build), root)
        else:
            reference_package(root)
        shutil.copytree(ROS / "tests", root / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root)).returncode


def worker(n: int, seed: int) -> None:
    from datetime import date, timedelta

    from researchos import snapshot

    out = []

    def call(name, fn):
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
            out.append([name, "raise", type(exc).__name__])
            return
        out.append([name, "ok", value])

    kinds = ["bp", "return", "points"]
    for i in range(n):
        rng = random.Random(f"{seed}:{i}")
        tag = f"{i}"
        as_of = date(2026, rng.randint(1, 12), rng.randint(1, 28))

        def series():
            obs = []
            for _ in range(rng.randint(0, 30)):
                d = as_of + timedelta(days=rng.randint(-500, 20))
                v = rng.choice([round(rng.uniform(-5, 200), 2), 0.0, rng.randint(-3, 3),
                                round(rng.uniform(1e5, 2e6))])
                obs.append((d, v))
            return obs

        ids = ["A", "B", "C"]
        data = {k: series() for k in ids if rng.random() < 0.85}
        h = rng.choice(["1w", "2w", "1m", "3m", "1y", "10d", "1W", "0d"])
        kind = rng.choice(kinds + ["pct"]) if rng.random() < 0.1 else rng.choice(kinds)
        call(tag + " trailing", lambda: snapshot.trailing_change(data.get("A", []), as_of, h, kind))
        change = rng.choice([None, True, 0, -0.0049, 0.005, rng.uniform(-300, 300),
                             rng.uniform(-1, 1)])
        call(tag + " format_change", lambda: snapshot.format_change(change, kind))
        call(tag + " format_level", lambda: snapshot.format_level(change))
        targets = [{"id": k, "name": f"T{k}", "unit": "u", "kind": rng.choice(kinds)}
                   for k in rng.sample(ids, rng.randint(0, 3))]
        call(tag + " target_table", lambda: snapshot.target_table(as_of, targets, data))
        entries = [{"id": k, "name": f"S{k}", "unit": "u"} for k in rng.sample(ids, rng.randint(0, 3))]
        published = rng.sample(ids, rng.randint(0, 3))
        call(tag + " context_table", lambda: snapshot.context_table(as_of, entries, data, published))
        pos = [{"id": k, "name": f"P{k}", "net": k, "open_interest": rng.choice(ids + [None, "Z"]),
                "release_lag_days": rng.choice([0, 3, 4, 7]) if rng.random() < 0.95
                else rng.choice([-1, True, 2.0])} for k in rng.sample(ids, rng.randint(0, 3))]
        call(tag + " positioning_table", lambda: snapshot.positioning_table(as_of, pos, data))
        events = [{"date": (as_of + timedelta(days=rng.randint(-10, 120))).isoformat()
                   if rng.random() < 0.95 else rng.choice(["x", None, ""]),
                   "event": f"E{j}"} for j in range(rng.randint(0, 6))]
        through = as_of + timedelta(days=rng.choice([0, 7, 31, 92]))
        call(tag + " calendar_list", lambda: snapshot.calendar_list(as_of, events, through))
    json.dump(out, sys.stdout)


def fuzz(build, n: int, seed: int) -> int:
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, make in (("reference", reference_package),
                           ("build", lambda d: build_package(Path(build), d))):
            root = Path(tmp) / name
            root.mkdir()
            make(root)
            done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "worker", str(n),
                                   str(seed)], cwd=root, env=env_for(root), capture_output=True,
                                  text=True, encoding="utf-8")
            if done.returncode:
                print(done.stderr)
                return 2
            results[name] = json.loads(done.stdout)
    ref = {e[0]: e[1:] for e in results["reference"]}
    got = {e[0]: e[1:] for e in results["build"]}
    by_fn, shown = {}, 0
    for key in sorted(set(ref) | set(got), key=lambda k: (int(k.split(" ", 1)[0]), k)):
        fn = key.split(" ", 1)[1]
        stats = by_fn.setdefault(fn, [0, 0])
        stats[0] += 1
        if ref.get(key, "absent") != got.get(key, "absent"):
            stats[1] += 1
            if shown < 8:
                print("MISMATCH", key, NL, json.dumps(ref.get(key))[:900], NL, "   vs", NL,
                      json.dumps(got.get(key))[:900])
                shown += 1
    for fn, (calls, mism) in sorted(by_fn.items()):
        print(f"{fn:20} {calls:6} calls, {mism} mismatch(es)")
    bad = sum(s[1] for s in by_fn.values())
    print(f"total: {sum(s[0] for s in by_fn.values())} calls, {bad} mismatch(es)")
    return 1 if bad else 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(int(sys.argv[2]), int(sys.argv[3]))
        return 0
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["suite", "fuzz"])
    p.add_argument("--build")
    p.add_argument("--n", type=int, default=3000)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.command == "suite":
        return suite(a.build)
    if not a.build:
        p.error("fuzz needs --build")
    return fuzz(a.build, a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
