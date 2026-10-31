"""Check a ResearchOS v21 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

Without --build, `suite` runs against the reference: a copy of ResearchOS/ with
reference/cases.py in place and the two appends added. With --build, the build's
researchos/ package is tested with the repository's own tests/ (a build cannot
weaken them) plus the hidden suite. Run it with an interpreter that has pytest and
anthropic; tools/ from the repository goes on PYTHONPATH.
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


def reference_package(dest: Path) -> Path:
    """dest/researchos: the repository's package with the v21 reference applied."""
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(HERE / "reference" / "cases.py", pkg / "cases.py")
    for module in ("masking", "loop"):
        extra = (HERE / "reference" / f"{module}_append.py").read_text(encoding="utf-8")
        path = pkg / f"{module}.py"
        path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8", newline=NL)
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
        shutil.copyfile(HERE / "test_macro_cases_acceptance.py",
                        root / "tests" / "test_macro_cases_acceptance.py")
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root)).returncode


# --- the fuzz worker: run in a subprocess with one implementation on the path -------------

IDS = ["DGS10", "DGS2", "T10Y2Y", "SP500", "x", "_", "a_b", "", "DGS-10", "x y", chr(0xE9),
       7, None, "A1" + NL, "VIXCLS"]
HORIZONS = ["1w", "1m", "3m", "10d", "1y", "12m", " 1w", "1w ", "0d", "01w", "1W", "1mo", "",
            None, 1, "2w", "1w" + NL]
KINDS = ["bp", "return", "points", "bps", "", None, "Return"]
DATES = ["2026-10-30", "2026-07-03", "2026-01-31", "2026-02-28", " 2026-10-30 ", "2026-13-01",
         "", None, "9999-12-01", "2024-02-29", "2026-11-02", "2026-06-30", "2026-10-29"]
VALUES = [4.21, 0, -1, True, "4", None, float("nan"), 10 ** 30, 6512.3, -0.35]
DOMAINS = ["earnings", "macro", "rates"]


def worker(n: int, seed: int) -> None:
    from dataclasses import asdict
    from datetime import date

    from researchos import cases, loop, masking
    from researchos.cases import Case, Document, Outcome, Target, TargetOutcome

    rng = random.Random(seed)
    out = []

    def call(name, fn):
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
            out.append([name, "raise", type(exc).__name__])
            return None
        out.append([name, "ok", json.loads(json.dumps(value, default=repr))])
        return value

    def a_target():
        horizons = rng.choice([rng.sample(HORIZONS[:6], rng.randint(1, 3)),
                               [rng.choice(HORIZONS) for _ in range(rng.randint(0, 3))],
                               "1w", ("1w",), None])
        return Target(id=rng.choice(IDS[:7]) if rng.random() < 0.8 else rng.choice(IDS),
                      name=rng.choice(["n", "", None]),
                      kind=rng.choice(KINDS[:3]) if rng.random() < 0.8 else rng.choice(KINDS),
                      horizons=horizons,
                      as_of_date=rng.choice(DATES[:4]) if rng.random() < 0.7 else rng.choice(DATES),
                      as_of_value=rng.choice(VALUES[:2]) if rng.random() < 0.7 else rng.choice(VALUES),
                      trailing=rng.choice([{}, {"1w": 1.0}, "x"]))

    def targets():
        ts = [a_target() for _ in range(rng.randint(0, 4))]
        if ts and rng.random() < 0.1:
            ts.append({"id": "not a target"})
        return ts

    def documents():
        return [Document(rng.choice(["dashboard", "statement"]), f"T{i}", f"text {i} $1,200.0 2025",
                         rng.choice(["", "https://x"])) for i in range(rng.randint(0, 3))]

    def a_case():
        """A macro case when the inputs allow one, else an odd Case built directly."""
        ts = [t for t in targets() if isinstance(t, Target)]
        try:
            return cases.macro_case("m", rng.choice(DATES), ts, documents())
        except ValueError:
            return Case("odd", "", "", [], "", rng.choice(DATES), documents(),
                        rng.choice(DOMAINS), ts)

    def keys_of(case):
        try:
            return cases.target_keys(case)
        except ValueError:
            return []

    for i in range(n):
        rng = random.Random(f"{seed}:{i}")  # a disagreement stays in its own round
        tag = f"{i}"
        call(tag + " target_key", lambda: cases.target_key(rng.choice(IDS), rng.choice(HORIZONS)))
        ts = targets()
        call(tag + " check_targets", lambda: cases.check_targets(ts))
        mc = call(tag + " macro_case",
                  lambda: asdict(cases.macro_case(rng.choice(["m1", "macro-x"]), rng.choice(DATES),
                                                  ts, documents())))
        case = a_case()
        call(tag + " target_keys", lambda: cases.target_keys(case))
        keys = keys_of(case)
        resolved = rng.sample(keys, rng.randint(0, len(keys))) if keys else []
        if rng.random() < 0.15:
            resolved.append(rng.choice(["DGS10_1y", "nope", "VIX_1w"]))
        manifest = call(tag + " resolution_manifest",
                        lambda: cases.resolution_manifest(case, resolved))
        variants = [None, {}, [], {"targets": None}, {"targets": {}}, manifest]
        if isinstance(manifest, dict):
            odd = json.loads(json.dumps(manifest))
            for k in list(odd["targets"])[:2]:
                odd["targets"][k] = rng.choice([True, {"resolved": "true"}, {"resolved": 1},
                                                {"resolved": True}, {}, None])
            odd["targets"]["EXTRA_1w"] = {"resolved": True}
            variants.append(odd)
        m = rng.choice(variants)
        call(tag + " unresolved_targets", lambda: cases.unresolved_targets(case, m))
        cutoff = rng.choice(DATES + [date(2026, 6, 30), date(2026, 10, 30), 20260630])
        call(tag + " require_after_cutoff",
             lambda: loop.require_after_cutoff("cid", case, cutoff))
        group = [Case(f"c{j}", "", "", [], "", "2026-01-02", [], rng.choice(DOMAINS[:2])
                      if rng.random() < 0.9 else rng.choice(DOMAINS)) for j in range(rng.randint(0, 3))]
        call(tag + " single_domain", lambda: loop.single_domain(group))
        call(tag + " unmasked", lambda: asdict(masking.unmasked(case)))
        # files: what is written, and what comes back
        with tempfile.TemporaryDirectory() as tmp:
            outcome = None
            if keys and rng.random() < 0.5:
                outcome = Outcome(targets={k: TargetOutcome(k.rsplit("_", 1)[0], k.rsplit("_", 1)[1],
                                                            "bp", "2026-10-30", 4.2, "2026-11-06",
                                                            4.3, 10.0, True) for k in keys})
            elif rng.random() < 0.5:
                outcome = Outcome(rng.choice([0.03, -0.01, None]), notes="n")
            if rng.random() < 0.3:
                case.domain = "earnings"
            call(tag + " save_case", lambda: cases.save_case(tmp, case, outcome))
            for name in ("case.json", "outcome.json"):
                p = Path(tmp) / case.case_id / name
                out.append([tag + " bytes " + name, p.read_text(encoding="utf-8") if p.is_file() else None])
            call(tag + " load_case", lambda: asdict(cases.load_case(tmp, case.case_id)) == asdict(case))
            if outcome is not None:
                call(tag + " load_outcome",
                     lambda: asdict(cases.load_outcome(tmp, case.case_id)) == asdict(outcome))
            if isinstance(manifest, dict):
                call(tag + " save_resolution", lambda: cases.save_resolution(tmp, case.case_id, manifest))
                call(tag + " load_resolution", lambda: cases.load_resolution(tmp, case.case_id))
            call(tag + " load_resolution missing", lambda: cases.load_resolution(tmp, "none"))
    json.dump(out, sys.stdout)


def fuzz(build, n: int, seed: int) -> int:
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, make in (("reference", lambda d: reference_package(d)),
                           ("build", lambda d: build_package(Path(build), d))):
            root = Path(tmp) / name
            root.mkdir()
            make(root)
            cmd = [sys.executable, str(Path(__file__).resolve()), "worker", str(n), str(seed)]
            done = subprocess.run(cmd, cwd=root, env=env_for(root), capture_output=True, text=True,
                                  encoding="utf-8")
            if done.returncode:
                print(done.stderr)
                return 2
            results[name] = json.loads(done.stdout)
    ref = {entry[0]: entry[1:] for entry in results["reference"]}
    got = {entry[0]: entry[1:] for entry in results["build"]}
    by_fn: dict[str, list[int]] = {}
    shown = 0
    for key in sorted(set(ref) | set(got), key=lambda k: (int(k.split(" ", 1)[0]), k)):
        fn = key.split(" ", 1)[1]
        stats = by_fn.setdefault(fn, [0, 0])
        stats[0] += 1
        if ref.get(key, "absent") != got.get(key, "absent"):
            stats[1] += 1
            if shown < 12:
                print("MISMATCH", key, json.dumps(ref.get(key, "absent"))[:300], NL,
                      "      vs", json.dumps(got.get(key, "absent"))[:300])
                shown += 1
    total = sum(s[0] for s in by_fn.values())
    bad = sum(s[1] for s in by_fn.values())
    for fn, (calls, mism) in sorted(by_fn.items()):
        print(f"{fn:26} {calls:6} calls, {mism} mismatch(es)")
    print(f"total: {total} calls, {bad} mismatch(es)")
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
