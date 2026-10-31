"""Check a ResearchOS v26 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is reference/targets.py added to a copy of ResearchOS/. Run it with an
interpreter that has pytest and anthropic.
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
NL = chr(10)
HIDDEN = "test_targets_acceptance.py"


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(HERE / "reference" / "targets.py", pkg / "targets.py")
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
        return subprocess.run(cmd, cwd=root, env=env_for(root), check=False).returncode


HORIZONS = ["1w", "2w", "1m", "3m", "10d", "1y"]
NAMES = ["growth", "inflation", "labor", "monetary_policy", "fiscal_supply",
         "liquidity_conditions", "positioning_sentiment", "global_fx", "commodities_energy",
         "politics_geopolitics", "my_view", "other_thing"]


def worker(n: int, seed: int) -> None:
    from dataclasses import asdict

    from researchos import cases, masking, targets
    from researchos.playbook import Lesson, Playbook

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
        ts = []
        for k in range(rng.randint(1, 4)):
            ts.append(cases.Target(f"T{k}", rng.choice(["Ten-year", "S&P", "x"]),
                                   rng.choice(["bp", "return", "points"]),
                                   rng.sample(HORIZONS, rng.randint(1, 3)), "2026-10-29",
                                   rng.choice([4.98, 7743.98, -37.5, 0.0, 12.345678, 2])))
        if rng.random() < 0.05:
            ts.append(cases.Target("T0", "dup", "bp", ["1w"], "2026-10-29", 1.0))
        docs = [cases.Document(rng.choice(["dashboard", "economy", "statement"]), f"D{j}",
                               f"text {j} " * rng.randint(0, 3), rng.choice(["", "https://x"]))
                for j in range(rng.randint(0, 3))]
        try:
            case = cases.macro_case(f"m-{i}", "2026-10-30", ts, docs)
        except ValueError:
            case = cases.Case(f"m-{i}", "", "", [], "as of 2026-10-30", "2026-10-30", docs,
                              "macro", ts)
        if rng.random() < 0.04:
            case.domain = "earnings"
        call(tag + " schema", lambda: targets.view_schema(ts))
        persp = rng.sample(NAMES, rng.randint(1, 5))
        if rng.random() < 0.05:
            persp = rng.choice([[], persp + persp[:1], "growth", [1]])
        mode = rng.choice(["holistic", "isolated"]) if rng.random() < 0.97 else "both"
        weights = rng.choice([
            {p: rng.choice([0.0, 0.1, 0.5, 1.0, 2.0]) for p in NAMES},
            {},
            {"growth": 1.0},
        ])
        book = Playbook(weights=weights, lessons=[Lesson("l", "w", "general", "c")][: rng.randint(0, 1)])
        calls = []

        def answer(system, prompt, schema, prefix, who):
            calls.append([who, system, prompt, schema, prefix])
            reply = {"forecasts": {}, "key_points": [f"p{len(calls)}", 3][: rng.randint(0, 2)],
                     "thesis": rng.choice(["t", None, 5])}
            for t in ts:
                entry = {}
                for h in t.horizons:
                    entry[f"prob_up_{h}"] = rng.choice([
                        round(rng.random(), 3), 0, 1, 1.5, -0.2, 0.5, 0.123456789])
                reply["forecasts"][t.id] = entry
            r = rng.random()
            if r < 0.02:
                return ["x"]
            if r < 0.04:
                reply["forecasts"].pop(ts[0].id, None)
            elif r < 0.06:
                first = reply["forecasts"].get(ts[0].id)
                if isinstance(first, dict) and first:
                    first[next(iter(first))] = rng.choice(["0.5", True, math.nan, None])
            return reply

        class Role:
            def __init__(self, who):
                self.who = who

            def json(self, system, prompt, schema, *, prefix=None):
                return answer(system, prompt, schema, prefix, self.who)

        def analyze():
            result = targets.analyze_targets(masking.unmasked(case), case, book, Role("a"),
                                             Role("j"), perspectives=persp, mode=mode)
            data = asdict(result)
            back = targets.target_analysis_from_dict(data)
            return [data, asdict(back) == data]

        call(tag + " analyze", analyze)
        out.append([tag + " calls", "ok", calls])
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
        if fn == "analyze" and key in ref:
            label = ref[key][1] if ref[key][0] == "raise" else "analyzed"
            outcomes[label] = outcomes.get(label, 0) + 1
        if ref.get(key, "absent") != got.get(key, "absent"):
            stats[1] += 1
            if shown < 6:
                print("MISMATCH", key, NL, json.dumps(ref.get(key))[:1500], NL, "   vs", NL,
                      json.dumps(got.get(key))[:1500])
                shown += 1
    for fn, (calls, mism) in sorted(by_fn.items()):
        print(f"{fn:10} {calls:6} calls, {mism} mismatch(es)")
    print("reference analyses:", outcomes)
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
