"""Check a ResearchOS v25 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is a copy of ResearchOS/ with reference/snapshot_append.py appended to
researchos/snapshot.py and reference/main_patch.py applied to __main__.py. While v24 is
still a draft, its reference (and its hidden suite) is added first. Run it with an
interpreter that has pytest and anthropic.
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
HIDDEN = "test_resolve_snapshot_acceptance.py"
V24 = DRAFTS / "researchos-v24"
V24_HIDDEN = "test_build_snapshot_acceptance.py"


def _patch(main: Path, patch_file: Path) -> None:
    spec = importlib.util.spec_from_file_location(patch_file.parent.parent.name + "_patch",
                                                  patch_file)
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    main.write_text(patch.apply(main.read_text(encoding="utf-8")), encoding="utf-8", newline=NL)


def _append(module: Path, extra: Path) -> None:
    module.write_text(module.read_text(encoding="utf-8") + extra.read_text(encoding="utf-8"),
                      encoding="utf-8", newline=NL)


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    if "def build_snapshot" not in (pkg / "snapshot.py").read_text(encoding="utf-8"):
        _append(pkg / "snapshot.py", V24 / "reference" / "snapshot_append.py")
        _patch(pkg / "__main__.py", V24 / "reference" / "main_patch.py")
    _append(pkg / "snapshot.py", HERE / "reference" / "snapshot_append.py")
    _patch(pkg / "__main__.py", HERE / "reference" / "main_patch.py")
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
    env.pop("RESEARCHOS_USER_AGENT", None)
    return env


def suite(build) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if build:
            build_package(Path(build), root)
        else:
            reference_package(root)
        shutil.copytree(ROS / "tests", root / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        if (V24 / V24_HIDDEN).exists() and not (root / "tests" / V24_HIDDEN).exists():
            shutil.copyfile(V24 / V24_HIDDEN, root / "tests" / V24_HIDDEN)
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root), check=False).returncode


IDS = ["A", "B", "C"]
HORIZONS = ["1w", "2w", "1m", "3m", "10d"]


def worker(n: int, seed: int) -> None:
    from datetime import date, timedelta

    from researchos import cases, snapshot

    out = []

    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            rng = random.Random(f"{seed}:{i}")
            base = Path(tmp) / str(i)
            root, sdir = base / "cases", base / "series"
            ids = []
            for c in range(rng.randint(1, 3)):
                as_of = date(2026, rng.randint(1, 12), rng.randint(1, 28))
                case_id = f"m{c}-{as_of.isoformat()}"
                targets = []
                for k in rng.sample(IDS, rng.randint(1, 3)):
                    start_day = as_of - timedelta(days=rng.choice([0, 0, 1, 2, 3]))
                    value = rng.choice([round(rng.uniform(-2, 200), 3), 0.0, 4.0, -1.5,
                                        float(rng.randint(1, 9))])
                    targets.append(cases.Target(k, f"T{k}", rng.choice(["bp", "return", "points"]),
                                                rng.sample(HORIZONS, rng.randint(1, 3)),
                                                start_day.isoformat(), value))
                case = cases.macro_case(case_id, as_of.isoformat(), targets, [])
                if rng.random() < 0.05:
                    case.domain = "earnings"
                if rng.random() < 0.03:
                    case.event_date = "2026-02-30"
                cases.save_case(root, case)
                if rng.random() < 0.8:
                    try:
                        cases.save_resolution(root, case_id, cases.resolution_manifest(case))
                    except ValueError:
                        pass
                if rng.random() < 0.3 and case.event_date != "2026-02-30":
                    keys = cases.target_keys(case)
                    kept = {}
                    for key in rng.sample(keys, rng.randint(0, len(keys))):
                        t_id, h = key.rsplit("_", 1)
                        kept[key] = cases.TargetOutcome(t_id, h, "points", "2026-01-01", 1.0,
                                                        "2026-02-01", 2.0, 1.0, True)
                    if rng.random() < 0.1:
                        kept["Z_1w"] = cases.TargetOutcome("Z", "1w", "points", "2026-01-01", 1.0,
                                                           "2026-02-01", 2.0, 1.0, True)
                    cases.save_outcome(root, case_id, cases.Outcome(notes=f"n{c}", targets=kept))
                if rng.random() < 0.3:
                    write(root / case_id / "prediction.json", "{}")
                ids.append((case_id, as_of))

            def obs(as_of):
                pairs = []
                for _ in range(rng.randint(0, 20)):
                    d = as_of + timedelta(days=rng.randint(-15, 120))
                    pairs.append([d.isoformat(), rng.choice([round(rng.uniform(-5, 300), 3),
                                                              0.0, 4.0, float(rng.randint(-3, 9))])])
                return pairs

            first_as_of = ids[0][1]
            for k in IDS:
                if rng.random() < 0.85:
                    write(sdir / f"{k}.json", obs(first_as_of))
                if rng.random() < 0.02:
                    write(sdir / f"{k}.json", "not json")
            request = [cid for cid, _ in ids]
            if rng.random() < 0.05:
                request.append(request[0])
            if rng.random() < 0.05:
                request.insert(rng.randint(0, len(request)), "missing-case")
            rng.shuffle(request)
            for round_no in range(2):
                try:
                    result = ["ok", snapshot.resolve_cases(root, request, series_dir=sdir)]
                except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
                    result = ["raise", type(exc).__name__]
                files = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                         for p in sorted(root.rglob("*")) if p.is_file()}
                out.append([f"{i} resolve{round_no}", result, files])
                for k in IDS:
                    if (sdir / f"{k}.json").exists() and rng.random() < 0.7:
                        try:
                            pairs = json.loads((sdir / f"{k}.json").read_text(encoding="utf-8"))
                        except ValueError:
                            continue
                        write(sdir / f"{k}.json", pairs + obs(first_as_of + timedelta(days=60)))
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
        value = ref.get(key)
        if value is not None:
            label = value[0][1] if value[0][0] == "raise" else "resolved"
            outcomes[label] = outcomes.get(label, 0) + 1
        if ref.get(key, "absent") != got.get(key, "absent"):
            stats[1] += 1
            if shown < 6:
                print("MISMATCH", key, NL, json.dumps(ref.get(key))[:1500], NL, "   vs", NL,
                      json.dumps(got.get(key))[:1500])
                shown += 1
    for fn, (calls, mism) in sorted(by_fn.items()):
        print(f"{fn:10} {calls:6} calls, {mism} mismatch(es)")
    print("reference calls:", outcomes)
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
    p.add_argument("--n", type=int, default=1500)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.command == "suite":
        return suite(a.build)
    if not a.build:
        p.error("fuzz needs --build")
    return fuzz(a.build, a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
