"""Check a ResearchOS v29 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is a copy of ResearchOS/ with reference/blend.py in place of blend.py and
reference/main_patch.py applied to __main__.py. While v28 is still a draft, its reference
(and its hidden suite) is put in first. Run it with an interpreter that has pytest and
anthropic.
"""

from __future__ import annotations

import argparse
import importlib.util
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
DRAFTS = HERE.parent
NL = chr(10)
HIDDEN = "test_blend_macro_acceptance.py"
V28 = DRAFTS / "researchos-v28"
V28_HIDDEN = "test_forecast_score_acceptance.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    if "def score_sealed" not in (pkg / "loop.py").read_text(encoding="utf-8"):
        _load(V28 / "reference" / "patch.py", "v28_patch").apply(pkg)
    shutil.copyfile(HERE / "reference" / "blend.py", pkg / "blend.py")
    main = pkg / "__main__.py"
    patch = _load(HERE / "reference" / "main_patch.py", "v29_main_patch")
    main.write_text(patch.apply(main.read_text(encoding="utf-8")), encoding="utf-8", newline=NL)
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
        if (V28 / V28_HIDDEN).exists() and not (root / "tests" / V28_HIDDEN).exists():
            shutil.copyfile(V28 / V28_HIDDEN, root / "tests" / V28_HIDDEN)
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root), check=False).returncode


DAYS = ["2026-07-03", "2026-07-10", "2026-07-17", "2026-07-24", "2026-08-07", "2026-10-02"]
LATER = ["2026-07-03", "2026-07-10", "2026-07-17", "2026-07-24", "2026-07-31", "2026-08-03",
         "2026-08-10", "2026-10-02", "2027-01-04"]


def worker(n: int, seed: int) -> None:
    from researchos import blend, targets
    from researchos.perspectives import PERSPECTIVES

    out = []

    def call(name, fn):
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
            out.append([name, "raise", type(exc).__name__])
            return
        out.append([name, "ok", value])

    sets = [None, list(PERSPECTIVES), list(targets.MACRO_PERSPECTIVES), ["a", "b"],
            ["c", "a", "b"], ["a"]]
    for i in range(n):
        rng = random.Random(f"{seed}:{i}")
        tag = str(i)
        names = rng.choice(sets)
        held = list(PERSPECTIVES) if names is None else names
        if rng.random() < 0.04:
            names = rng.choice([[], ["a", "a"], "ab", [1], ("a", "b")])
        records = []
        for k in range(rng.randint(0, 12)):
            event = rng.choice(DAYS)
            up = rng.random() < 0.5
            probs = {p: rng.choice([0.0, 0.1, 0.25, 0.5, 0.6, 0.75, 0.9, 1.0, round(rng.random(), 3)])
                     for p in held}
            if rng.random() < 0.01 and probs:
                probs.pop(next(iter(probs)))
            if rng.random() < 0.1:
                probs["extra"] = 0.5
            y = 1.0 if up else 0.0
            rec = {"case_id": f"c{k}" if rng.random() < 0.995 else "c0", "event_date": event}
            r = rng.random()
            if r < 0.6:
                later = [d for d in LATER if d >= event]
                rec["resolution_date"] = rng.choice(later)
            elif r < 0.61:
                rec["resolution_date"] = rng.choice([None, "2026-07-01", "soon", 5])
            rec["actual_up"] = up if rng.random() < 0.997 else "yes"
            if rng.random() < 0.85:
                rec["prob_by_perspective"] = probs
            rec["brier_by_perspective"] = {p: (v - y) ** 2 for p, v in probs.items()}
            if rng.random() < 0.05:
                rec.pop("brier_by_perspective")
            records.append(rec)
        rule = rng.choice(list(blend.RULES)) if rng.random() < 0.98 else "best"
        eta = rng.choice([0.5, 0.5, 0.1, 2.0, 1e-9]) if rng.random() < 0.98 else 0
        kwargs = {} if names is None and rng.random() < 0.5 else {"perspectives": names}
        call(tag + " replay", lambda: blend.replay(records, rule, eta=eta, **kwargs))
        call(tag + " agreement", lambda: blend.agreement(records, **kwargs))
    json.dump(out, sys.stdout, default=repr)


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
                                  text=True, encoding="utf-8", check=False)
            if done.returncode:
                print(done.stderr[-4000:])
                return 2
            results[name] = json.loads(done.stdout)
    ref = {e[0]: e[1:] for e in results["reference"]}
    got = {e[0]: e[1:] for e in results["build"]}
    by_fn, shown, outcomes = {}, 0, {}
    for key in sorted(set(ref) | set(got), key=lambda k: (int(k.split(" ", 1)[0]), k)):
        fn = key.split(" ", 1)[1]
        stats = by_fn.setdefault(fn, [0, 0])
        stats[0] += 1
        if key in ref:
            label = fn + " " + (ref[key][1] if ref[key][0] == "raise" else "ok")
            outcomes[label] = outcomes.get(label, 0) + 1
        if ref.get(key, "absent") != got.get(key, "absent"):
            stats[1] += 1
            if shown < 6:
                print("MISMATCH", key, NL, json.dumps(ref.get(key))[:1500], NL, "   vs", NL,
                      json.dumps(got.get(key))[:1500])
                shown += 1
    for fn, (count, mism) in sorted(by_fn.items()):
        print(f"{fn:10} {count:6} calls, {mism} mismatch(es)")
    print("reference results:", dict(sorted(outcomes.items())))
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
    p.add_argument("--n", type=int, default=4000)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.command == "suite":
        return suite(a.build)
    if not a.build:
        p.error("fuzz needs --build")
    return fuzz(a.build, a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
