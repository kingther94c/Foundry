"""Check a ResearchOS v22 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run, build vs reference

Without --build, `suite` runs against the reference: a copy of ResearchOS/ with
reference/series.py added and reference/main_patch.py applied to __main__.py. With
--build, the build's researchos/ package is tested with the repository's own tests/
plus the hidden suite. Run it with an interpreter that has pytest and anthropic.
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
NL = chr(10)
HIDDEN = "test_series_acceptance.py"


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(HERE / "reference" / "series.py", pkg / "series.py")
    spec = importlib.util.spec_from_file_location("main_patch", HERE / "reference" / "main_patch.py")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    main = pkg / "__main__.py"
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
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root)).returncode


# --- the fuzz worker ----------------------------------------------------------------------------

IDS = ["DGS10", "UNRATE", "X", "A_B", "DGS1", "DGS 10", "", "dgs10", None, 7]
HEADERS = ["observation_date", "DATE", "date", " observation_date ", "OBSERVATION_DATE"]
DAYS = ["2026-10-01", "2026-10-02", "2026-10-12", " 2026-10-05 ", "2026-10-32", "20261003", "",
        "Oct 1", "2026-02-29", "2024-02-29"]
VALUES = ["5.24", "-0.35", "4", "", ".", " 4.21 ", "1e3", "+1", ".5", "5.", "NaN", "9" * 400, "007",
          "1_0", "#N/A", " . "]
CODES = ["043602", "13874A", "1170E1", "43602", "13874a", "", None]
COUNTS = ["668230", " 12 ", "-5", "0", 10, -3, "1.5", "", True, None, 1.0, "1e3"]
REPORT_DAYS = ["2026-10-27T00:00:00.000", "2026-10-20", " 2026-10-13 ", "2026-10-27Tx",
               "10/27/2026", "", None, 20261027]
DATE_ARGS = [None, "2026-01-01", " 2026-08-01 ", "2026-13-01", "soon", 20261001, True]


def worker(n: int, seed: int) -> None:
    from datetime import date, datetime

    from researchos import series

    out = []

    def call(name, fn):
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 -- the kind of failure is the result
            out.append([name, "raise", type(exc).__name__])
            return None
        out.append([name, "ok", json.loads(json.dumps(value, default=str))])
        return value

    class Transport:
        def __init__(self, body, ctype):
            self.body, self.ctype, self.calls = body, ctype, []

        def __call__(self, url, headers):
            self.calls.append([url, headers])
            if isinstance(self.body, Exception):
                raise self.body
            return self.body, self.ctype

    for i in range(n):
        rng = random.Random(f"{seed}:{i}")
        tag = f"{i}"
        sid = rng.choice(IDS[:5]) if rng.random() < 0.8 else rng.choice(IDS)
        start, vintage = rng.choice(DATE_ARGS), rng.choice(DATE_ARGS)
        if rng.random() < 0.2:
            start = date(2026, 1, rng.randint(1, 28))
        call(tag + " fred_url", lambda: series.fred_url(sid, start=start, vintage=vintage))
        # a CSV
        column = sid if isinstance(sid, str) else "X"
        column = rng.choice([column, column, column + "_20261030", column + "_2026103", "DGS2",
                             column + "0"])
        lines = [rng.choice(HEADERS[:2]) if rng.random() < 0.85 else rng.choice(HEADERS)]
        lines[0] += "," + column
        for _ in range(rng.randint(0, 6)):
            day = rng.choice(DAYS[:4]) if rng.random() < 0.85 else rng.choice(DAYS)
            value = rng.choice(VALUES[:6]) if rng.random() < 0.85 else rng.choice(VALUES)
            lines.append(rng.choice([f"{day},{value}", f"{day},{value}", f"{day},{value},",
                                     day, ""]))
        if rng.random() < 0.05:
            lines = ["<html>error</html>"]
        text = rng.choice([NL, chr(13) + NL]).join(lines) + rng.choice(["", NL])
        call(tag + " parse_fred_csv", lambda: series.parse_fred_csv(text, sid))
        # a CFTC payload
        code = rng.choice(CODES[:3]) if rng.random() < 0.85 else rng.choice(CODES)
        rows = []
        for _ in range(rng.randint(0, 4)):
            r = {"report_date_as_yyyy_mm_dd": rng.choice(REPORT_DAYS[:3]) if rng.random() < 0.85
                 else rng.choice(REPORT_DAYS),
                 "cftc_contract_market_code": code if rng.random() < 0.9 else rng.choice(CODES),
                 "market_and_exchange_names": "M"}
            for field in ("noncomm_positions_long_all", "noncomm_positions_short_all",
                          "open_interest_all"):
                r[field] = rng.choice(COUNTS[:6]) if rng.random() < 0.9 else rng.choice(COUNTS)
            if rng.random() < 0.05:
                del r[rng.choice(list(r))]
            rows.append(r if rng.random() < 0.97 else ["x"])
        payload = rng.choice([rows, rows, rows, {"message": "m"}, None, {"x": 1}])
        call(tag + " parse_cftc", lambda: series.parse_cftc(payload, rng.choice([code, code, "043602"])))
        limit = rng.choice([600, 1, 50000, 0, 50001, True, 2.0, "5"])
        call(tag + " cftc_url", lambda: series.cftc_url(code, limit=limit))
        # fetching through a transport
        body = rng.choice([text.encode("utf-8"), json.dumps(payload).encode(), b"<html>x</html>",
                           OSError("down")])
        ctype = rng.choice(["application/csv", "text/csv; charset=latin-1", "", "application/json"])
        t = Transport(body, ctype)
        call(tag + " fetch_fred", lambda: series.fetch_fred(sid, user_agent="UA", start=start,
                                                            vintage=vintage, transport=t))
        out.append([tag + " fetch_fred calls", t.calls])
        t = Transport(body, ctype)
        call(tag + " fetch_cftc", lambda: series.fetch_cftc(code, user_agent="UA", limit=limit,
                                                            transport=t))
        out.append([tag + " fetch_cftc calls", t.calls])
        # files
        with tempfile.TemporaryDirectory() as tmp:
            pairs = []
            for _ in range(rng.randint(0, 5)):
                d = date(2026, 10, rng.randint(1, 9))
                v = rng.choice([1.5, -2, 0, 10 ** 20, 3.25])
                if rng.random() < 0.05:
                    d = rng.choice([datetime(2026, 10, 1, 9), "2026-10-01"])
                if rng.random() < 0.05:
                    v = rng.choice([True, float("nan"), "1", None])
                pairs.append((d, v))
            fid = rng.choice(["DGS10", "PAYEMS"])
            call(tag + " save_series", lambda: series.save_series(Path(tmp) / f"{fid}.json", pairs))
            p = Path(tmp) / f"{fid}.json"
            out.append([tag + " bytes", p.read_text(encoding="utf-8") if p.is_file() else None])
            if rng.random() < 0.5:
                call(tag + " save vintage", lambda: series.save_series(
                    Path(tmp) / f"{fid}@2026-10-05.json", [(date(2026, 10, 3), 7.0),
                                                           (date(2026, 10, 6), 8.0)]))
            as_of = rng.choice([None, "2026-10-05", date(2026, 10, 4), " 2026-10-05 ", "2026-10-09",
                                "bad", 5])
            call(tag + " load_series", lambda: series.load_series(tmp, rng.choice([fid, fid, "NONE",
                                                                                    "../x"]), as_of))
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
