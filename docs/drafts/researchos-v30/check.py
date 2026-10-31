"""Check a ResearchOS v30 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is a copy of ResearchOS/ with reference/playbook_append.py appended to
playbook.py and reference/loop_append.py to loop.py. While v29 is still a draft, its
reference (and its hidden suite) is put in first. Run it with an interpreter that has pytest
and anthropic.
"""

from __future__ import annotations

import argparse
import builtins
import importlib.util
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROS = REPO / "ResearchOS"
DRAFTS = HERE.parent
NL = chr(10)
HIDDEN = "test_walk_forward_acceptance.py"
V29 = DRAFTS / "researchos-v29"
V29_HIDDEN = "test_blend_macro_acceptance.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _append(path: Path, extra: Path) -> None:
    text = path.read_text(encoding="utf-8").rstrip(NL) + NL + extra.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8", newline=NL)


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    if V29.exists() and "resolution_date" not in (pkg / "blend.py").read_text(encoding="utf-8"):
        shutil.copyfile(V29 / "reference" / "blend.py", pkg / "blend.py")
        main = pkg / "__main__.py"
        patch = _load(V29 / "reference" / "main_patch.py", "v29_main_patch")
        main.write_text(patch.apply(main.read_text(encoding="utf-8")), encoding="utf-8",
                        newline=NL)
    _append(pkg / "playbook.py", HERE / "reference" / "playbook_append.py")
    _append(pkg / "loop.py", HERE / "reference" / "loop_append.py")
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


# v28's own tests checked that train refuses a macro case, which this version replaces;
# while they are as v28 left them, they are kept for evaluate only.
V28_TRAIN_CHECKS = [
    ('@pytest.mark.parametrize("run", [train, evaluate])' + NL
     + "def test_train_and_evaluate_refuse_a_macro_case(",
     '@pytest.mark.parametrize("run", [evaluate])' + NL
     + "def test_train_and_evaluate_refuse_a_macro_case("),
    ('    for cmd in ("train", "evaluate"):' + NL, '    for cmd in ("evaluate",):' + NL),
]


def _narrow_v28_tests(tests: Path) -> None:
    path = tests / "test_forecast_score.py"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    for old, new in V28_TRAIN_CHECKS:
        if text.count(old) == 1:
            text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline=NL)


def suite(build) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if build:
            build_package(Path(build), root)
        else:
            reference_package(root)
        shutil.copytree(ROS / "tests", root / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        _narrow_v28_tests(root / "tests")
        if (V29 / V29_HIDDEN).exists() and not (root / "tests" / V29_HIDDEN).exists():
            shutil.copyfile(V29 / V29_HIDDEN, root / "tests" / V29_HIDDEN)
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root), check=False).returncode


# --- the differential run -------------------------------------------------------------------------

DAYS = ["2026-07-03", "2026-07-06", "2026-07-10", "2026-07-13", "2026-07-17", "2026-07-27",
        "2026-08-03", "2026-08-10", "2026-10-02"]
TARGETS = [("DGS10", "10-year Treasury yield", "bp", 4.0),
           ("SP500", "S&P 500", "return", 6800.0),
           ("VIXCLS", "VIX", "points", 16.0)]
HORIZONS = ["1w", "2w", "1m", "3m"]


def _crc(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


def _prob(text: str) -> float:
    return round(0.05 + (_crc(text) % 900) / 1000, 3)


class _Fake:
    """Answers by the schema; every number depends on the whole request."""

    def __init__(self, record, role, model=None):
        self.record, self.role = record, role
        if model is not None:
            self.model = model

    def json(self, system, prompt, schema, *, prefix=None):
        self.record.append([self.role, system, prefix, prompt])
        text = f"{system}|{prefix}|{prompt}"
        props = schema["properties"]
        if "redactions" in props:
            return {"redactions": []}
        if "guesses" in props:
            return {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.2}],
                    "clues": []}
        if "lessons" in props:
            names = ["growth", "inflation", "liquidity_conditions", "general", "bogus", "risks"]
            lessons = [{"lesson": f"lesson {_crc(text + str(k)) % 1000}",
                        "applies_when": "sometimes",
                        "perspective": names[_crc(text + "p" + str(k)) % len(names)]}
                       for k in range(_crc(text) % 3)]
            return {"why_market_moved": "", "where_we_were_wrong": "", "lessons": lessons}
        if "stance" in props:
            return {"stance": "bullish", "prob_up": _prob(text), "conviction": "medium",
                    "key_points": ["p"]}
        if "forecasts" in props:
            forecasts = {tid: {field: _prob(text + tid + field) for field in sub["properties"]}
                         for tid, sub in props["forecasts"]["properties"].items()}
            if "thesis" in props:
                return {"forecasts": forecasts, "thesis": "t"}
            return {"key_points": ["k"], "forecasts": forecasts}
        return {"prob_up": _prob(text), "thesis": "t"}


def _days_before(as_of, n):
    from datetime import date
    return date.fromordinal(date(*map(int, as_of.split("-"))).toordinal() - n).isoformat()


def _scenario(rng, root: Path):
    """Write a random set of snapshots (and sometimes a trap) under root; return the call."""
    from researchos import cases
    from researchos.cases import Case, Document, Outcome, Target, TargetOutcome
    from researchos.playbook import Lesson, Playbook, save_playbook
    from researchos.targets import MACRO_PERSPECTIVES

    ids = []
    for j in range(rng.randint(1, 4)):
        as_of = rng.choice(DAYS)
        case_id = f"s{j}"
        chosen = rng.sample(TARGETS, rng.randint(1, 3))
        targets = []
        for tid, name, kind, level in chosen:
            horizons = rng.sample(HORIZONS, rng.randint(1, 3))
            targets.append(Target(tid, name, kind, horizons, _days_before(as_of, 1), level,
                                  {h: rng.choice([-1.0, 2.5]) for h in horizons}))
        case = cases.macro_case(case_id, as_of, targets,
                                [Document("dashboard", f"Markets as of {as_of}", f"t {j}")])
        keys = cases.target_keys(case)
        cases.save_case(root, case)
        r = rng.random()
        if r < 0.015:
            pass  # no manifest
        elif r < 0.03:
            cases.save_resolution(root, case_id, cases.resolution_manifest(case, keys[1:]))
        else:
            cases.save_resolution(root, case_id, cases.resolution_manifest(case, keys))
        found = {}
        for key in keys:
            if rng.random() < 0.004:
                continue  # the outcome disagrees with the manifest
            tid, horizon = key.rsplit("_", 1)
            up = rng.random() < 0.5
            kind = next(t.kind for t in targets if t.id == tid)
            found[key] = TargetOutcome(tid, horizon, kind, "2026-07-02", 1.0, "2026-09-01",
                                       2.0 if up else 0.5, 1.0 if up else -0.5, up)
        cases.save_outcome(root, case_id, Outcome(targets=found))
        if rng.random() < 0.015:
            (root / case_id / "prediction.json").write_text("{}", encoding="utf-8")
        if rng.random() < 0.01:
            path = root / case_id / "case.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["event_date"] = rng.choice(["2026-7-3", "soon", 5])
            path.write_text(json.dumps(data), encoding="utf-8")
        ids.append(case_id)
    if rng.random() < 0.03:
        earnings = Case("acme", "Acme Widgets Inc.", "ACMW", ["Acme"], "Q4", "2026-02-01",
                        [Document("release", "Acme reports", "Revenue rose.", "")])
        cases.save_case(root, earnings, Outcome(0.03, 0.04, 0.01, "Up.", []))
        ids = ["acme"] if rng.random() < 0.5 else ids + ["acme"]
    if rng.random() < 0.015:
        ids.append(rng.choice(ids))
    if rng.random() < 0.015:
        ids.insert(rng.randrange(len(ids) + 1), "nope")
    rng.shuffle(ids)

    pb = root / "pb.json"
    r = rng.random()
    if r < 0.25:
        raw = [rng.random() + 0.1 for _ in MACRO_PERSPECTIVES]
        weights = {n: w / sum(raw) for n, w in zip(MACRO_PERSPECTIVES, raw)}
        history = [{"case_id": "old/DGS10_1w", "split": "train", "event_date": "2026-05-01",
                    "resolution_date": rng.choice(["2026-05-08", "2026-06-20", "2026-06-20",
                                                   "2026-07-04", None])}]
        if rng.random() < 0.05:
            history.append({"case_id": "bad", "event_date": "2026-05-01",
                            "resolution_date": "later"})
        save_playbook(pb, Playbook(weights, [Lesson("old lesson", "always", "growth", "old")],
                                   history))
    elif r < 0.28:
        save_playbook(pb, Playbook.initial())
    cutoff = rng.choice(["2026-06-30"] * 24 + [None, "2026-07-05", "2026-07-10"])
    kwargs = {"knowledge_cutoff": cutoff}
    if rng.random() < 0.5:
        kwargs["perspective_mode"] = rng.choice(["holistic", "isolated", "isolated"])
    if rng.random() < 0.02:
        kwargs["perspective_mode"] = "both"
    return ids, pb, kwargs


def worker(n: int, seed: int) -> None:
    from researchos import loop

    out = []
    for i in range(n):
        rng = random.Random(f"{seed}:{i}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids, pb, kwargs = _scenario(rng, root)
            record = []
            model = rng.choice([None, "m-1", 7])
            roles = loop.Roles(masker=_Fake(record, "masker"), guesser=_Fake(record, "guesser"),
                               analyst=_Fake(record, "analyst", model),
                               judge=_Fake(record, "judge"),
                               reflector=_Fake(record, "reflector"))
            events = []
            real_open = io.open

            def spy(file, mode="r", *args, **kw):
                if isinstance(file, (str, os.PathLike)):
                    path = Path(file)
                    if path.name in ("outcome.json", "prediction.json"):
                        kind = "w" if any(c in mode for c in "wax+") else "r"
                        event = [path.parent.name, path.name, kind]
                        if event not in events:
                            events.append(event)
                return real_open(file, mode, *args, **kw)

            io.open = builtins.open = spy
            try:
                result = ["ok", loop.train(root, ids, pb, roles, **kwargs)]
            except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
                result = ["raise", type(exc).__name__]
            finally:
                io.open = builtins.open = real_open
            files = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                     for p in sorted(root.rglob("*")) if p.is_file()}
            out.append([str(i), result, record, events, files])
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
    parts = ["result", "calls", "opened", "files"]
    mism, shown, outcomes = {p: 0 for p in parts}, 0, {}
    for ref, got in zip(results["reference"], results["build"]):
        label = "ok" if ref[1][0] == "ok" else ref[1][1]
        outcomes[label] = outcomes.get(label, 0) + 1
        for k, part in enumerate(parts, start=1):
            if ref[k] != got[k]:
                mism[part] += 1
                if shown < 6:
                    print("MISMATCH", ref[0], part, NL, json.dumps(ref[k])[:1500], NL, "   vs",
                          NL, json.dumps(got[k])[:1500])
                    shown += 1
    bad = sum(1 for ref, got in zip(results["reference"], results["build"]) if ref != got)
    print("mismatches by part:", mism)
    print("reference results:", dict(sorted(outcomes.items())))
    print(f"total: {len(results['reference'])} runs, {bad} mismatch(es)")
    return 1 if bad else 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(int(sys.argv[2]), int(sys.argv[3]))
        return 0
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["suite", "fuzz"])
    p.add_argument("--build")
    p.add_argument("--n", type=int, default=400)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.command == "suite":
        return suite(a.build)
    if not a.build:
        p.error("fuzz needs --build")
    return fuzz(a.build, a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
