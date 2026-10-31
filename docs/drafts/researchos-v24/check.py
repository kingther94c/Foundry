"""Check a ResearchOS v24 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is a copy of ResearchOS/ with reference/snapshot_append.py appended to
researchos/snapshot.py and reference/main_patch.py applied to __main__.py. While v22 or v23
is still a draft, its reference (and its hidden suite) is added first. Run it with an
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
HIDDEN = "test_build_snapshot_acceptance.py"
EARLIER = [("researchos-v22", "series.py", "test_series_acceptance.py"),
           ("researchos-v23", "snapshot.py", "test_snapshot_tables_acceptance.py")]


def _patch(main: Path, patch_file: Path) -> None:
    spec = importlib.util.spec_from_file_location(patch_file.stem + "_mod", patch_file)
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    main.write_text(patch.apply(main.read_text(encoding="utf-8")), encoding="utf-8", newline=NL)


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    for draft, module, _ in EARLIER:
        if not (pkg / module).exists():
            shutil.copyfile(DRAFTS / draft / "reference" / module, pkg / module)
            if (DRAFTS / draft / "reference" / "main_patch.py").exists():
                _patch(pkg / "__main__.py", DRAFTS / draft / "reference" / "main_patch.py")
    snap = pkg / "snapshot.py"
    snap.write_text(snap.read_text(encoding="utf-8")
                    + (HERE / "reference" / "snapshot_append.py").read_text(encoding="utf-8"),
                    encoding="utf-8", newline=NL)
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
        for draft, _, hidden in EARLIER:
            if (DRAFTS / draft / hidden).exists() and not (root / "tests" / hidden).exists():
                shutil.copyfile(DRAFTS / draft / hidden, root / "tests" / hidden)
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root)).returncode


IDS = ["A", "B", "C", "D"]

MUTATIONS = [
    lambda c: c.update(case_prefix=random.choice(["a b", "", 5, None])),
    lambda c: c.pop("case_prefix"),
    lambda c: c.update(horizons=random.choice([[], ["1w", "1w"], ["1W"], "1w", [7], ["0d"]])),
    lambda c: c.pop("horizons"),
    lambda c: c.update(targets=random.choice([[], "A", [1]])),
    lambda c: c["targets"][0].update(id=random.choice(["A-1", "", 3, "a b"])),
    lambda c: c["targets"].append(dict(c["targets"][0])),
    lambda c: c["targets"][0].update(kind=random.choice(["pct", None, "BP"])),
    lambda c: c["targets"][0].update(optional=random.choice(["yes", 1, None])),
    lambda c: c["targets"][0].update(name=None),
    lambda c: c["targets"][0].pop("unit"),
    lambda c: c.update(context=random.choice([{}, "x", [3]])),
    lambda c: c.update(context=[{"id": random.choice(["x y", "", 4]), "name": "n", "unit": "u"}]),
    lambda c: c.update(context=[{"id": "A", "name": "n"}]),
    lambda c: c.update(positioning=[{"id": "A", "name": "P", "net": "A_net",
                                     "release_lag_days": random.choice([-1, True, "3", 2.0, None])}]),
    lambda c: c.update(positioning=[{"id": "A", "name": "P", "net": "A_net", "release_lag_days": 3,
                                     "open_interest": random.choice(["", "a b", 5])}]),
    lambda c: c.update(positioning=[{"id": "A", "name": "P", "net": random.choice(["a b", None]),
                                     "release_lag_days": 3}]),
    lambda c: c.update(documents=[{"kind": "s", "title": "t", "dated": "2026-01-02",
                                   "url": "https://x.example/u", "text": "both"}]),
    lambda c: c.update(documents=[{"kind": "s", "title": "t", "dated": "2026-01-02"}]),
    lambda c: c.update(documents=[{"kind": "s", "title": "t",
                                   "dated": random.choice(["soon", "2026-02-30", None, 20260102]),
                                   "text": "x"}]),
    lambda c: c.update(documents=[{"kind": random.choice(["", None]), "title": "t",
                                   "dated": "2026-01-02", "text": "x"}]),
    lambda c: c.update(documents=[{"kind": "s", "dated": "2026-01-02", "text": "x"}]),
    lambda c: c.update(documents=[{"kind": "s", "title": "t", "dated": "2026-01-02", "url": None}]),
    lambda c: c.update(calendar=[{"date": random.choice(["Nov 4", None, "2026-13-01"]), "event": "e"}]),
    lambda c: c.update(calendar=[{"date": "2026-11-04"}]),
    lambda c: c.update(notes="ignored", extra={"a": 1}),
]


def worker(n: int, seed: int) -> None:
    import logging
    from datetime import date, timedelta
    from types import SimpleNamespace

    from researchos import snapshot

    logging.disable(logging.CRITICAL)
    out = []

    def write(path, data):
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            rng = random.Random(f"{seed}:{i}")
            random.seed(f"{seed}:{i}:m")
            base = Path(tmp) / str(i)
            sdir = base / "series"
            sdir.mkdir(parents=True)
            as_of = date(2026, rng.randint(1, 12), rng.randint(1, 28))

            def obs():
                pairs = []
                for _ in range(rng.randint(0, 25)):
                    d = as_of + timedelta(days=rng.randint(-450, 40))
                    v = rng.choice([round(rng.uniform(-3, 300), 2), 0.0, rng.randint(-2, 5),
                                    float(rng.randint(-90000, 90000))])
                    pairs.append([d.isoformat(), v])
                return pairs

            for name in IDS + [k + "_net" for k in IDS] + [k + "_oi" for k in IDS]:
                if rng.random() < (0.93 if name in IDS else 0.75):
                    write(sdir / f"{name}.json", obs())
                if rng.random() < 0.15:
                    write(sdir / f"{name}@{as_of.isoformat()}.json", obs())
                if rng.random() < 0.05:
                    write(sdir / f"{name}@{(as_of - timedelta(days=7)).isoformat()}.json", obs())
                if rng.random() < 0.02:
                    write(sdir / f"{name}.json", "not json")

            def target(k):
                t = {"id": k, "name": f"T{k}", "unit": "u",
                     "kind": rng.choice(["bp", "return", "points"])}
                if rng.random() < 0.5:
                    t["optional"] = rng.random() < 0.5
                return t

            def pos(k):
                e = {"id": k, "name": f"P{k}", "net": k + "_net",
                     "release_lag_days": rng.choice([0, 3, 4, 10])}
                if rng.random() < 0.7:
                    e["open_interest"] = rng.choice([k + "_oi", None])
                return e

            cfg = {"case_prefix": rng.choice(["macro", "m_1", "x-y"]),
                   "horizons": rng.sample(["1w", "2w", "1m", "3m", "10d", "1y"], rng.randint(1, 3)),
                   "targets": [target(k) for k in rng.sample(IDS, rng.randint(1, 4))]}
            if rng.random() < 0.8:
                cfg["context"] = [{"id": k, "name": f"S{k}", "unit": "u"}
                                  for k in rng.sample(IDS, rng.randint(0, 3))]
            if rng.random() < 0.8:
                cfg["positioning"] = [pos(k) for k in rng.sample(IDS, rng.randint(0, 3))]
            urls = []
            if rng.random() < 0.85:
                docs = []
                for j in range(rng.randint(0, 7)):
                    dated = (as_of + timedelta(days=rng.randint(-60, 20))).isoformat()
                    if rng.random() < 0.1:
                        dated = f" {dated} "
                    e = {"kind": rng.choice(["st", "jobs", "cpi"]), "title": f"Doc {j}",
                         "dated": dated}
                    if rng.random() < 0.6:
                        e["url"] = f"https://x.example/{j}"
                        urls.append(e["url"])
                    else:
                        e["text"] = f"text {j}"
                    docs.append(e)
                cfg["documents"] = docs
            if rng.random() < 0.8:
                cfg["calendar"] = [{"date": (as_of + timedelta(days=rng.randint(-5, 400))).isoformat(),
                                    "event": f"E{j}"} for j in range(rng.randint(0, 6))]
            raw = cfg
            if rng.random() < 0.3:
                try:
                    rng.choice(MUTATIONS)(cfg)
                except (IndexError, KeyError):
                    pass
            if rng.random() < 0.02:
                raw = [cfg]
            cfg_path = base / "macro-config.json"
            write(cfg_path, raw)
            try:
                loaded = snapshot.load_macro_config(cfg_path)
            except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
                out.append([f"{i} config", "raise", type(exc).__name__])
                continue
            out.append([f"{i} config", "ok", loaded])

            fail = {u: rng.choice([OSError, ValueError]) for u in urls if rng.random() < 0.25}
            calls = []

            def fetch(url):
                calls.append(url)
                if url in fail:
                    raise fail[url]("unavailable")
                return SimpleNamespace(url=url, title="ignored", text=f"page at {url}")

            root = base / "cases"
            case_dir = root / f"{loaded['case_prefix']}-{as_of.isoformat()}"
            seal = rng.random()
            if seal < 0.06:
                case_dir.mkdir(parents=True)
                write(case_dir / rng.choice(["prediction.json", "outcome.json"]), "{}")
            elif seal < 0.1:
                case_dir.mkdir(parents=True)
                write(case_dir / "case.json", "{}")
            as_arg = rng.choice([as_of, as_of.isoformat(), as_of.isoformat(),
                                 rng.choice(["2026-02-30", "soon", None])
                                 if rng.random() < 0.1 else as_of])
            try:
                summary = snapshot.build_snapshot(root, loaded, as_arg, series_dir=sdir,
                                                  fetch_page=fetch)
                result = ["ok", summary]
            except Exception as exc:  # noqa: BLE001
                result = ["raise", type(exc).__name__]
            files = {}
            if root.exists():
                for p in sorted(root.rglob("*")):
                    if p.is_file():
                        files[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
            out.append([f"{i} build", result, calls, files])
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
            if shown < 6:
                print("MISMATCH", key, NL, json.dumps(ref.get(key))[:1500], NL, "   vs", NL,
                      json.dumps(got.get(key))[:1500])
                shown += 1
    outcomes = {}
    for key, value in ref.items():
        if key.endswith(" build"):
            label = value[0][0] if value[0][0] == "raise" else "built"
            outcomes[label] = outcomes.get(label, 0) + 1
    for fn, (calls, mism) in sorted(by_fn.items()):
        print(f"{fn:10} {calls:6} calls, {mism} mismatch(es)")
    print("reference builds:", outcomes)
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
