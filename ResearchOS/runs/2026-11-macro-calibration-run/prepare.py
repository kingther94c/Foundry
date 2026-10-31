"""Fetch the series of this run and build its snapshots, through the researchos commands.

    python prepare.py fetch     store every series in series/ (skips files already there)
    python prepare.py build     build the snapshot of each Friday in cases/
    python prepare.py hashes    write inputs.sha256: the SHA-256 of each case.json
    python prepare.py resolve ROOT   record what has resolved in an arm's copy of cases/
                                     (only after its forecasts are sealed)

series/ and cases/ are not committed: some of the series (the S&P 500, VIX, the ICE BofA
spread, the NASDAQ gold index) may not be redistributed. inputs.sha256 is, so that the
cases a forecast was made on can be checked against the ones built when the plan was
fixed. Every source needs no account and no key.
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


def fetch_fred(series_id: str, vintage: str | None = None) -> None:
    name = f"{series_id}@{vintage}.json" if vintage else f"{series_id}.json"
    if (SERIES / name).exists():
        return
    args = ["fetch-series", "--source", "fred", "--id", series_id, "--start", START,
            "--file", str(SERIES / name)]
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


def hashes() -> None:
    lines = []
    for day in fridays():
        path = CASES / f"macro-{day}" / "case.json"
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  cases/macro-{day}/case.json")
    (HERE / "inputs.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def resolve(root: str) -> None:
    ids = ",".join(f"macro-{day}" for day in fridays())
    folder = (HERE / root).resolve()
    run("resolve", "--root", str(folder), "--ids", ids, "--series-dir", str(SERIES),
        "--out", str(HERE / f"resolve_{folder.name}.json"))


if __name__ == "__main__":
    steps = {"fetch": fetch, "build": build, "hashes": hashes}
    if len(sys.argv) == 3 and sys.argv[1] == "resolve":
        resolve(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] in steps:
        steps[sys.argv[1]]()
    else:
        raise SystemExit(__doc__)
