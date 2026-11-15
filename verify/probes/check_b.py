"""Check the second set's hidden tests: each fails on the scaffold and passes on
a reference fix. python verify/probes/check_b.py [PROJECTS [NAME ...]]

PROJECTS is where make_projects_b.py wrote them; by default, projects/ here.
NAME limits the check to those projects.
Hidden tests that pass on the scaffold judge nothing, and ones that fail on the
reference fix cannot be passed: either is a FAIL line and an exit status of 1.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "projects"

REF = {
    "g-checklist": {"textstats/__main__.py": '''
import argparse, json, re, sys
from collections import Counter

WORD = re.compile(r"[\\w'-]+")

def main(argv=None):
    p = argparse.ArgumentParser(prog="textstats")
    p.add_argument("file")
    p.add_argument("--lines", action="store_true")
    p.add_argument("--words", action="store_true")
    p.add_argument("--chars", action="store_true")
    p.add_argument("--top", type=int)
    p.add_argument("--ignore-case", action="store_true")
    p.add_argument("--min-length", type=int, default=0)
    p.add_argument("--exclude")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        with open(a.file, encoding="utf-8", newline="") as f:
            text = f.read()
    except OSError:
        print(f"textstats: cannot read {a.file}", file=sys.stderr)
        return 2
    if not (a.lines or a.words or a.chars):
        a.lines = a.words = a.chars = True
    words = [w for w in re.findall(r"[^\\W_]+(?:['-][^\\W_]+)*|['-]", text) if w not in ("'", "-")]
    words = re.findall(r"[A-Za-z0-9'-]+", text)
    if a.ignore_case:
        words = [w.lower() for w in words]
    words = [w for w in words if len(w) >= a.min_length]
    if a.exclude:
        ex = open(a.exclude, encoding="utf-8").read().split()
        ex = {e.lower() for e in ex} if a.ignore_case else set(ex)
        words = [w for w in words if w not in ex]
    out = {}
    if a.lines: out["lines"] = len(text.splitlines())
    if a.words: out["words"] = len(words)
    if a.chars: out["chars"] = len(text)
    top = sorted(Counter(words).items(), key=lambda kv: (-kv[1], kv[0]))[:a.top] if a.top else None
    if a.json:
        if top is not None: out["top"] = [list(t) for t in top]
        print(json.dumps(out))
    else:
        for k, v in out.items(): print(f"{k}: {v}")
        for w, c in top or []: print(f"{w} {c}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''},
    "i-scope": {"strutil/text.py": None},  # filled below
    "l-repro": {"invoice/totals.py": '''
from decimal import Decimal, ROUND_HALF_UP

def line_total(price, quantity):
    return float((Decimal(str(price)) * Decimal(str(quantity))).quantize(Decimal("0.01"), ROUND_HALF_UP))

def invoice_total(lines):
    return float(sum(Decimal(str(line_total(l["price"], l["quantity"]))) for l in lines))
'''},
    "m-ambiguous": {"sales/__main__.py": '''
import csv, sys
from sales.data import ROWS

def main(argv=None):
    argv = argv or []
    if "--csv" in argv:
        w = csv.DictWriter(sys.stdout, ["region", "quarter", "units", "revenue"], lineterminator="\\n")
        w.writeheader(); w.writerows(ROWS); return 0
    print(f"{'region':<8}{'quarter':<9}{'units':>6}{'revenue':>10}")
    for r in ROWS:
        print(f"{r['region']:<8}{r['quarter']:<9}{r['units']:>6}{r['revenue']:>10.2f}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''},
    "n-env-trap": {"src/calc/stats.py": '''
def mean(values):
    values = list(values)
    if not values:
        raise ValueError("mean of nothing")
    return sum(values) / len(values)

def median(values):
    v = sorted(values)
    if not v:
        raise ValueError("median of nothing")
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2
'''},
}


def run_hidden(name, fix):
    with tempfile.TemporaryDirectory() as t:
        ws = Path(t) / "ws"
        shutil.copytree(P / name / "repo", ws)
        g = lambda *a: subprocess.run(["git", *a], cwd=ws, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      env={**os.environ, "GIT_AUTHOR_NAME": "s", "GIT_AUTHOR_EMAIL": "s@x",
                                           "GIT_COMMITTER_NAME": "s", "GIT_COMMITTER_EMAIL": "s@x"})
        g("init", "-q"); g("config", "core.autocrlf", "false"); g("add", "-A"); g("commit", "-qm", "s")
        if fix:
            for rel, text in REF[name].items():
                if name == "i-scope":
                    src = (ws / rel).read_text(encoding="utf-8")
                    text = src.replace('s = re.sub(r"[^a-z0-9]", "-", s)', 's = re.sub(r"[^a-z0-9]+", "-", s)')
                (ws / rel).write_text(text.lstrip("\n"), encoding="utf-8", newline="\n")
        tests = []
        for h in (P / name / "hidden").glob("*.py"):
            shutil.copy(h, ws / "tests" / h.name)
            tests.append(f"tests/{h.name}")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                           cwd=ws, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env={**os.environ, "PYTHONPATH": ""})
        return r.returncode, r.stdout.strip().splitlines()[-1], r.stdout


names = sys.argv[2:] or list(REF)
wrong = []
for name in names:
    before = run_hidden(name, False)
    after = run_hidden(name, True)
    print(f"{name}: scaffold -> {before[1]} | reference -> {after[1]}")
    if before[0] == 0:
        wrong.append(f"{name}: the hidden tests pass on the scaffold")
    if after[0] != 0:
        print(after[2][-2500:])
        wrong.append(f"{name}: the hidden tests fail on the reference fix")
for line in wrong:
    print("FAIL", line)
if not wrong:
    print(f"OK: {len(names)} sets of hidden tests fail on the scaffold and pass on a "
          "reference fix")
sys.exit(1 if wrong else 0)
