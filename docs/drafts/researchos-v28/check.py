"""Check a ResearchOS v28 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

The reference is a copy of ResearchOS/ with reference/patch.py applied. While v26 and v27
are still drafts, their references (and their hidden suites) are put in first. Run it with
an interpreter that has pytest and anthropic.
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
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROS = REPO / "ResearchOS"
DRAFTS = HERE.parent
NL = chr(10)
HIDDEN = "test_forecast_score_acceptance.py"
EARLIER = [(DRAFTS / "researchos-v26", "test_targets_acceptance.py"),
           (DRAFTS / "researchos-v27", "test_target_scoring_acceptance.py")]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    if not (pkg / "targets.py").exists():
        shutil.copyfile(DRAFTS / "researchos-v26" / "reference" / "targets.py", pkg / "targets.py")
    if "def score_targets" not in (pkg / "targets.py").read_text(encoding="utf-8"):
        extra = DRAFTS / "researchos-v27" / "reference" / "targets_append.py"
        (pkg / "targets.py").write_text((pkg / "targets.py").read_text(encoding="utf-8")
                                        + extra.read_text(encoding="utf-8"),
                                        encoding="utf-8", newline=NL)
    _load(HERE / "reference" / "patch.py", "v28_patch").apply(pkg)
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
        for draft, name in EARLIER:
            if (draft / name).exists() and not (root / "tests" / name).exists():
                shutil.copyfile(draft / name, root / "tests" / name)
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root), check=False).returncode


AS_OF = ["2026-06-30"] + 4 * ["2026-07-03", "2026-10-23", "2026-10-30"]
EVENTS = ["2026-02-01", "2026-05-01", "2026-08-01"]
HORIZONS = ["1w", "1m", "3m"]
KINDS = ["bp", "return", "points"]
GUESS_MISS = {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.2}],
              "clues": []}
GUESS_HIT = {"guesses": [{"company": "Acme Widgets", "ticker": "ACMW", "confidence": 1.0}],
             "clues": ["x"]}


def _prob(tag: str):
    h = zlib.crc32(tag.encode("utf-8"))
    if h % 41 == 0:
        return 1.3
    if h % 43 == 0:
        return 0
    return (h % 97 + 1) / 100


def worker(n: int, seed: int) -> None:
    from datetime import date

    from researchos import cases, loop, targets
    from researchos.cases import Case, Document, Outcome, Target, TargetOutcome
    from researchos.playbook import Lesson, Playbook, save_playbook

    out = []

    def files(root):
        return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(Path(root).rglob("*")) if p.is_file()}

    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            rng = random.Random(f"{seed}:{i}")
            root = Path(tmp) / str(i)
            root.mkdir()
            domain = "macro" if rng.random() < 0.8 else "earnings"
            made = {}
            for c in range(rng.randint(1, 3)):
                if domain == "macro" or rng.random() < 0.05:
                    as_of = rng.choice(AS_OF)
                    case_id = f"m{c}-{as_of}"
                    ts = [Target(f"T{k}", f"Target {k}", rng.choice(KINDS),
                                 rng.sample(HORIZONS, rng.randint(1, 2)), as_of,
                                 rng.choice([4.0, 6800.0, -0.5, 18.25]))
                          for k in range(rng.randint(1, 3))]
                    docs = [Document("dashboard", f"Markets {as_of}", f"rows {c}")]
                    case = cases.macro_case(case_id, as_of, ts, docs)
                    if rng.random() < 0.03:
                        case.event_date = "2026-13-01"
                    cases.save_case(root, case)
                else:
                    event = rng.choice(EVENTS)
                    case_id = f"e{c}-{event}"
                    case = Case(case_id, "Acme Widgets Inc.", "ACMW", ["Acme"], "Q4", event,
                                [Document("release", "Acme Widgets results",
                                          f"Acme Widgets revenue rose {c + 3}%. Zephyr grew.")])
                    ar = rng.choice([0.02, -0.01, 0.0, None])
                    cases.save_case(root, case, None if ar is None
                                    else Outcome(ar, 0.0, 0.0, "", []))
                if rng.random() < 0.06:
                    (root / case_id / "prediction.json").write_text("{}", encoding="utf-8")
                made[case_id] = case
            ids = list(made)
            request = list(ids)
            if rng.random() < 0.04:
                request.append(request[0])
            if rng.random() < 0.04:
                request.append("missing")
            if rng.random() < 0.03:
                request = []
            rng.shuffle(request)

            pb = root / "pb.json"
            names = targets.perspective_set(domain)
            r = rng.random()
            if r < 0.4:
                book = None
            elif r < 0.88:
                book = Playbook(weights={p: rng.choice([0.05, 0.1, 0.3, 1.0]) for p in names},
                                lessons=[Lesson("A lesson.", "w", rng.choice(names + ["general"]),
                                                "c")][: rng.randint(0, 1)])
            elif r < 0.93:
                other = targets.perspective_set("earnings" if domain == "macro" else "macro")
                book = Playbook.initial(other)
            else:
                book = Playbook.initial(names)
                book.history.append({"case_id": "h", "event_date": rng.choice(
                    ["2026-07-10", "2026-12-01", "2026-01-01", "soon"])})
            if book is not None:
                save_playbook(pb, book)
            cutoff = rng.choice([None, "2026-10-25", "bad"] + 6 * ["2026-06-30"]
                                + 3 * [date(2026, 6, 30)])
            mode = rng.choice(["holistic", "isolated"]) if rng.random() < 0.97 else "both"
            guess = GUESS_HIT if rng.random() < 0.2 else GUESS_MISS
            calls = []

            class Fake:
                def __init__(self, role, **attrs):
                    self.role = role
                    self.__dict__.update(attrs)

                def json(self, system, prompt, schema, *, prefix=None):
                    calls.append([self.role, system, prompt, prefix, list(schema["properties"])])
                    props = schema["properties"]
                    if "redactions" in props:
                        return {"redactions": [{"term": "Zephyr", "replacement": "the line",
                                                "reason": "brand"}]}
                    if "guesses" in props:
                        return guess
                    first = prompt.splitlines()[0] if prompt else ""
                    name = first.split(": ", 1)[1] if first.startswith("Perspective: ") else "j"
                    if "forecasts" in props:
                        if _prob(f"{i}:{name}:fail") == 0:
                            return ["broken"]
                        forecasts = {}
                        for t_id, sub in props["forecasts"]["properties"].items():
                            forecasts[t_id] = {f: _prob(f"{i}:{name}:{t_id}:{f}")
                                               for f in sub["properties"]}
                        return {"key_points": [name], "forecasts": forecasts, "thesis": "t"}
                    if "stance" in props:
                        return {"stance": "bullish", "prob_up": _prob(f"{i}:{name}"),
                                "conviction": "low", "key_points": [name]}
                    if "thesis" in props:
                        return {"prob_up": _prob(f"{i}:judge"), "thesis": "t"}
                    return {"lessons": []}

            analyst_attrs = rng.choice([{}, {"model": "claude-x"}, {"model": 3}])
            roles = loop.Roles(masker=Fake("masker"), guesser=Fake("guesser"),
                               analyst=Fake("analyst", **analyst_attrs), judge=Fake("judge"),
                               reflector=Fake("reflector"))
            try:
                result = ["ok", loop.forecast(root, request, pb, roles, seed=rng.randint(0, 3),
                                              knowledge_cutoff=cutoff, perspective_mode=mode)]
            except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
                result = ["raise", type(exc).__name__]
            out.append([f"{i} forecast", result, files(root), calls])

            for case_id, case in made.items():
                if case.domain == "macro" and rng.random() < 0.7:
                    kept = {}
                    for key in cases.target_keys(case):
                        if rng.random() < 0.5:
                            t_id, h = key.rsplit("_", 1)
                            change = rng.choice([-3.0, 0.0, 2.5, 0.01])
                            kept[key] = TargetOutcome(t_id, h, "points", case.event_date, 1.0,
                                                      "2026-12-01", 1.0 + change, change,
                                                      change > 0)
                    cases.save_outcome(root, case_id, Outcome(targets=kept))
            if len(ids) > 1 and rng.random() < 0.05:
                src = root / ids[0] / "prediction.json"
                if src.is_file():
                    shutil.copyfile(src, root / ids[1] / "prediction.json")
            wanted = [k for k in ids if rng.random() < 0.8] or ids[:1]
            if rng.random() < 0.04:
                wanted.append(wanted[0])
            rng.shuffle(wanted)
            try:
                scored = ["ok", loop.score_sealed(root, wanted)]
            except Exception as exc:  # noqa: BLE001
                scored = ["raise", type(exc).__name__]
            out.append([f"{i} score", scored, files(root)])
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
            value = ref[key][0]
            label = fn + " " + (value[1] if value[0] == "raise" else "ok")
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
    p.add_argument("--n", type=int, default=600)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.command == "suite":
        return suite(a.build)
    if not a.build:
        p.error("fuzz needs --build")
    return fuzz(a.build, a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
