"""Print what each scenario of a live batch did.

    python verify/show.py NAME [NAME ...] [--tail N]

Reads the lines live_batch.py appended under verify/out/ and, for each
scenario named, its runs: the tools called, what was refused, the notices,
and the end of what Foundry printed.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(args):
    tail = 1500
    if "--tail" in args:
        i = args.index("--tail")
        tail = int(args[i + 1])
        del args[i:i + 2]
    results = {}
    for file in sorted((HERE / "out").glob("live_results*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            results[json.loads(line)["name"]] = json.loads(line)
    for name in args:
        base = Path(results[name]["base"])
        summary = json.loads((base / "summary.json").read_text(encoding="utf-8"))
        spec = json.loads((HERE / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))
        print(f"===== {name} (about ${(results[name].get('usage') or {}).get('estimated_usd')})")
        for i, r in enumerate(summary["runs"], 1):
            run = spec["runs"][i - 1]
            task = (run.get("args") or [""])[-1] if run.get("args") else ""
            print(f"--- run {i} {r['args']} task={task[:160]!r} "
                  f"stdin={str(run.get('stdin'))[:200]!r}")
            print("tools", r["tools"], "rejected", r["rejected"])
            if r["notices"]:
                print("notices", r["notices"])
            err = (base / f"run{i}.err").read_text(encoding="utf-8")
            out = (base / f"run{i}.out").read_text(encoding="utf-8")
            text = err if len(err) > len(out) or out.lstrip().startswith("{") else out
            print(text[-tail:])
        print("check", summary.get("check"), "git", summary["git_status"],
              "problems", summary.get("problems"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
