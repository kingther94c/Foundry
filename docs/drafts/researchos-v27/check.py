"""Check a ResearchOS v27 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is a copy of ResearchOS/ with reference/targets_append.py appended to
researchos/targets.py. While v26 is still a draft, its reference module (and its hidden
suite) is put in first. Run it with an interpreter that has pytest and anthropic.
"""

from __future__ import annotations

import argparse
import json
import math
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
HIDDEN = "test_target_scoring_acceptance.py"
V26 = DRAFTS / "researchos-v26"
V26_HIDDEN = "test_targets_acceptance.py"


def _append(module: Path, extra: Path) -> None:
    module.write_text(module.read_text(encoding="utf-8") + extra.read_text(encoding="utf-8"),
                      encoding="utf-8", newline=NL)


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    if not (pkg / "targets.py").exists():
        shutil.copyfile(V26 / "reference" / "targets.py", pkg / "targets.py")
    _append(pkg / "targets.py", HERE / "reference" / "targets_append.py")
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
        if (V26 / V26_HIDDEN).exists() and not (root / "tests" / V26_HIDDEN).exists():
            shutil.copyfile(V26 / V26_HIDDEN, root / "tests" / V26_HIDDEN)
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root), check=False).returncode


HORIZONS = ["1w", "2w", "1m", "3m", "10d", "1y"]
NAMES = ["growth", "inflation", "labor", "monetary_policy", "fiscal_supply",
         "liquidity_conditions", "positioning_sentiment", "global_fx", "my_view"]
LEVELS = [4.98, 7743.98, -37.5, 0.0, 12.345678, 2.0, 0.125, 1234567.5]


def worker(n: int, seed: int) -> None:
    from dataclasses import asdict

    from researchos import cases, targets

    out = []

    def call(name, fn):
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
            out.append([name, "raise", type(exc).__name__])
            return
        out.append([name, "ok", value])

    for i in range(n):
        rng = random.Random(f"{seed}:{i}")
        tag = str(i)
        as_of = rng.choice(["2026-10-30", "2026-01-30", "2026-02-27", "2026-12-31", "2024-02-29"])
        ts = []
        for k in range(rng.randint(1, 4)):
            horizons = rng.sample(HORIZONS, rng.randint(1, 3))
            trailing = {h: rng.choice([1.5, -0.02, 0.0, 31.0]) for h in horizons
                        if rng.random() < 0.5}
            ts.append(cases.Target(f"T{k}", rng.choice(["Ten-year", "S&P 500", "x"]),
                                   rng.choice(["bp", "return", "points"]), horizons, as_of,
                                   rng.choice(LEVELS), trailing))
        docs = [cases.Document(rng.choice(["dashboard", "economy", "statement"]), f"D{j}",
                               f"text {j} " * rng.randint(0, 3), rng.choice(["", "https://x"]))
                for j in range(rng.randint(0, 3))]
        case = cases.macro_case(f"m-{i}", as_of, ts, docs)
        keys = cases.target_keys(case)
        persp = rng.sample(NAMES, rng.randint(1, 5))

        def prob():
            return rng.choice([round(rng.random(), 3), 0.01, 0.99, 0.5, 0.123456789, 0.75])

        views = {name: targets.TargetView({k: prob() for k in keys},
                                          [f"{name} point"][: rng.randint(0, 1)])
                 for name in persp}
        analysis = targets.TargetAnalysis(
            case.case_id, rng.choice(["holistic", "isolated"]), list(persp), list(keys), views,
            {k: prob() for k in keys}, {k: prob() for k in keys}, rng.choice(["", "thesis x"]))
        r = rng.random()
        if r < 0.03:
            analysis.case_id = "other"
        elif r < 0.06:
            analysis.keys = list(reversed(keys)) if len(keys) > 1 else keys + ["Z_1w"]
        resolved = {}
        for key in keys:
            if rng.random() < 0.6:
                t_id, h = key.rsplit("_", 1)
                t = next(x for x in ts if x.id == t_id)
                end = rng.choice(LEVELS)
                change = rng.choice([round(end - t.as_of_value, 6), -18.75, 0.025, 0.0, -0.4])
                resolved[key] = cases.TargetOutcome(t_id, h, t.kind, t.as_of_date, t.as_of_value,
                                                    rng.choice(["2026-11-06", "2027-01-29"]), end,
                                                    change, change > 0)
        outcome = cases.Outcome(targets=resolved)
        call(tag + " score", lambda: targets.score_targets(analysis, outcome, case))

        chosen = None
        if rng.random() < 0.4:
            pool = keys + ["Z_1w"] if rng.random() < 0.1 else keys
            resolved_keys = [k for k in keys if k in resolved] or keys
            chosen = [rng.choice(pool if rng.random() < 0.2 else resolved_keys)
                      for _ in range(rng.randint(0, 3))]
        calls = []
        reply = rng.choice(3 * [
            {"why_market_moved": "x", "where_we_were_wrong": "y", "lessons": [
                {"lesson": rng.choice([" A ", "B", "C", "  ", 5]),
                 "applies_when": rng.choice([" w ", 3, None]),
                 "perspective": rng.choice([rng.choice(persp), " GROWTH ", "risks", None, 7])}
                for _ in range(rng.randint(0, 3))] + rng.choice([[], ["s"], [None]])},
            {"lessons": "none"}, ["lessons"], {},
        ])

        class Reflector:
            def json(self, system, prompt, schema, *, prefix=None):
                calls.append([system, prompt, schema, prefix])
                return reply

        def reflect():
            lessons = targets.reflect_targets(case, analysis, outcome, Reflector(), keys=chosen)
            return [asdict(lesson) for lesson in lessons]

        call(tag + " reflect", reflect)
        out.append([tag + " calls", "ok", calls])
    json.dump(out, sys.stdout, default=repr)


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    return a == b


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
                print(done.stderr)
                return 2
            results[name] = json.loads(done.stdout)
    ref = {e[0]: e[1:] for e in results["reference"]}
    got = {e[0]: e[1:] for e in results["build"]}
    by_fn, shown, outcomes = {}, 0, {}
    for key in sorted(set(ref) | set(got), key=lambda k: (int(k.split(" ", 1)[0]), k)):
        fn = key.split(" ", 1)[1]
        stats = by_fn.setdefault(fn, [0, 0])
        stats[0] += 1
        if fn in ("score", "reflect") and key in ref:
            value = ref[key]
            label = fn + (" " + value[1] if value[0] == "raise" else
                          (" none" if value[1] == [] else " some"))
            outcomes[label] = outcomes.get(label, 0) + 1
        if not _same(ref.get(key, "absent"), got.get(key, "absent")):
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
