"""Fetch the series of this run and build its snapshots, through the researchos commands.

    python prepare.py fetch     store every series in series/ (skips files already there)
    python prepare.py build     build the snapshot of each Friday in cases/
    python prepare.py check     check every snapshot has its six documents and its targets
    python prepare.py hashes    write inputs.sha256: the SHA-256 of each case.json, and
                                inputs-parts.sha256: of each document and of the targets
    python prepare.py compare FILE   which parts of each snapshot differ from FILE's
    python prepare.py resolve ROOT   record what has resolved in an arm's copy of cases/
                                     (only after its forecasts are sealed)

series/ and cases/ are not committed: some of the series (the S&P 500, VIX, the ICE BofA
spread, the NASDAQ gold index) may not be redistributed. inputs.sha256 is, so that the
cases a forecast was made on can be checked against the ones built before it, and so is
inputs-parts.sha256, so that two builds can be compared part by part without the cases.
Every source needs no account and no key.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAUNCHER = HERE.parents[1] / "run-researchos.py"
CONFIG = HERE / "macro-config.json"
SERIES = HERE / "series"
CASES = HERE / "cases"
START = "2025-01-01"
FIRST, LAST = date(2026, 7, 3), date(2026, 10, 30)
COT_REPORTS = 120
KINDS = ("dashboard", "economy", "positioning", "calendar", "fomc_statement", "fomc_minutes")


def fridays() -> list[str]:
    days, day = [], FIRST
    while day <= LAST:
        days.append(day.isoformat())
        day += timedelta(days=7)
    return days


def run(*args: str) -> None:
    done = subprocess.run([sys.executable, str(LAUNCHER), *args], capture_output=True,
                          text=True, encoding="utf-8", check=False)
    if done.returncode:
        raise SystemExit(f"{' '.join(args[:3])} failed ({done.returncode}): {done.stderr.strip()}")
    if done.stderr.strip():
        print(done.stderr.strip(), file=sys.stderr)  # warnings: a page or a target left out


def fetch_fred(series_id: str, vintage: str | None = None, folder: Path = SERIES) -> None:
    name = f"{series_id}@{vintage}.json" if vintage else f"{series_id}.json"
    if (folder / name).exists():
        return
    args = ["fetch-series", "--source", "fred", "--id", series_id, "--start", START,
            "--file", str(folder / name)]
    if vintage:
        args += ["--vintage", vintage]
    run(*args)
    time.sleep(0.5)  # a polite pace for a free service


def fetch() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    SERIES.mkdir(exist_ok=True)
    for target in config["targets"]:
        fetch_fred(target["id"])
    for entry in config["context"]:
        fetch_fred(entry["id"])
        for day in fridays():
            fetch_fred(entry["id"], day)  # the series as it was published by that Friday
    for entry in config["positioning"]:
        code = entry["net"].split("_")[1]
        if (SERIES / f"{entry['net']}.json").exists():
            continue
        run("fetch-series", "--source", "cftc", "--id", code, "--limit", str(COT_REPORTS),
            "--file", str(SERIES / f"{entry['net']}.json"),
            "--oi-file", str(SERIES / f"{entry['open_interest']}.json"))
        time.sleep(0.5)


def build() -> None:
    CASES.mkdir(exist_ok=True)
    for day in fridays():
        if (CASES / f"macro-{day}" / "case.json").exists():
            continue
        run("snapshot", "--root", str(CASES), "--config", str(CONFIG), "--as-of", day,
            "--series-dir", str(SERIES))


def check() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    wanted = [t["id"] for t in config["targets"] if not t.get("optional")]
    optional = [t["id"] for t in config["targets"] if t.get("optional")]
    bad = []
    for day in fridays():
        case = json.loads((CASES / f"macro-{day}" / "case.json").read_text(encoding="utf-8"))
        kinds = [d["kind"] for d in case["documents"]]
        have = [t["id"] for t in case["targets"]]
        missing = [k for k in KINDS if k not in kinds] + [t for t in wanted if t not in have]
        left_out = [t for t in optional if t not in have]
        chars = sum(len(d["text"]) for d in case["documents"])
        print(f"macro-{day}: {len(have)} targets, {len(kinds)} documents, {chars} characters"
              + (f"; optional target left out: {', '.join(left_out)}" if left_out else "")
              + (f"; MISSING: {', '.join(missing)}" if missing else ""))
        if missing:
            bad.append(day)
    if bad:
        raise SystemExit(f"{len(bad)} snapshots lack a document or a target: {', '.join(bad)}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hashes() -> None:
    lines, parts = [], []
    for day in fridays():
        path = CASES / f"macro-{day}" / "case.json"
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  cases/macro-{day}/case.json")
        case = json.loads(path.read_text(encoding="utf-8"))
        for doc in case["documents"]:
            parts.append(f"{_sha(json.dumps([doc['title'], doc['text']]))}  macro-{day}/{doc['kind']}")
        parts.append(f"{_sha(json.dumps(case['targets'], sort_keys=True))}  macro-{day}/targets")
    (HERE / "inputs.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (HERE / "inputs-parts.sha256").write_text("\n".join(parts) + "\n", encoding="utf-8",
                                              newline="\n")


def compare(other: str) -> None:
    """Which parts of each snapshot differ from those hashed in another parts file."""
    def read(path: Path) -> dict[str, str]:
        found = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, part = line.split()
                found[part] = digest
        return found

    ours, theirs = read(HERE / "inputs-parts.sha256"), read(HERE / other)
    same = 0
    for day in fridays():
        names = sorted({p for p in [*ours, *theirs] if p.startswith(f"macro-{day}/")})
        differ = [p.split("/")[1] for p in names if ours.get(p) != theirs.get(p)]
        same += not differ
        print(f"macro-{day}: " + (f"differs in {', '.join(differ)}" if differ else "the same"))
    print(f"{same} of {len(fridays())} snapshots the same as in {other}")


def resolve(root: str) -> None:
    ids = ",".join(f"macro-{day}" for day in fridays())
    folder = (HERE / root).resolve()
    run("resolve", "--root", str(folder), "--ids", ids, "--series-dir", str(SERIES),
        "--out", str(HERE / f"resolve_{folder.name}.json"))


if __name__ == "__main__":
    steps = {"fetch": fetch, "build": build, "check": check, "hashes": hashes}
    if len(sys.argv) == 3 and sys.argv[1] == "resolve":
        resolve(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "compare":
        compare(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] in steps:
        steps[sys.argv[1]]()
    else:
        raise SystemExit(__doc__)
