"""Run the v6 acceptance suite, and compare a build with the reference.

    python docs/drafts/tools-v6/check.py suite                 the suite against the reference
    python docs/drafts/tools-v6/check.py suite --tools DIR     the suite against a build
    python docs/drafts/tools-v6/check.py fuzz --tools DIR      a build against the reference

DIR is the folder that holds the `tools` package: the build workspace, or this
repository's tools/ once v6 is in it. Each run works in a fresh temporary
directory with its own pytest configuration, so nothing in this repository's
configuration takes part.
"""

import argparse
import calendar
import datetime
import importlib.util
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SUITE = HERE / "test_v6_acceptance.py"
REFERENCE = HERE / "reference.py"
DATES = ("add_months", "parse_horizon", "shift_date", "horizon_end",
         "last_on_or_before", "first_on_or_after")
FINANCE = ("level_change", "direction")


def package_root(path):
    """The directory to put on sys.path so that `import tools` finds the build."""
    path = Path(path).resolve()
    if (path / "tools" / "__init__.py").is_file():
        return path
    if path.name == "tools" and (path / "__init__.py").is_file():
        return path.parent
    raise SystemExit("no tools package in %s" % path)


def reference_build(tmp):
    """A copy of this repository's tools package with the reference's v6 functions in it."""
    shutil.copytree(REPO / "tools" / "tools", tmp / "tools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(REFERENCE, tmp / "tools" / "_v6_reference.py")
    for module, names in (("dates", DATES), ("finance", FINANCE)):
        with open(tmp / "tools" / (module + ".py"), "a", encoding="utf-8") as f:
            f.write("\n\nfrom tools._v6_reference import %s  # noqa: E402,F401\n"
                    % ", ".join(names))
    return tmp


def run_suite(root):
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        (work / "tests").mkdir()
        shutil.copyfile(SUITE, work / "tests" / SUITE.name)
        (work / "pytest.ini").write_text(
            "[pytest]\nfilterwarnings = error::EncodingWarning\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(root)
        done = subprocess.run(
            [sys.executable, "-X", "warn_default_encoding", "-m", "pytest", "-q",
             "-p", "no:cacheprovider", "-c", str(work / "pytest.ini"), str(work / "tests")],
            cwd=str(work), env=env)
        return done.returncode


# ---- differential fuzzing ------------------------------------------------------------

def load(root):
    """The build's tools.dates and tools.finance, and the reference, side by side."""
    sys.path.insert(0, str(root))
    import tools.dates as dates
    import tools.finance as finance
    spec = importlib.util.spec_from_file_location("v6_reference", REFERENCE)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    return dates, finance, reference


def outcome(fn, args):
    try:
        return ("value", fn(*args))
    except Exception as exc:  # noqa: BLE001 -- the kind of exception is what is compared
        return ("raises", type(exc).__name__)


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def same(a, b):
    """Two outcomes agree: the same exception, or equal values (numbers to 1e-12)."""
    if a[0] != b[0]:
        return False
    x, y = a[1], b[1]
    if a[0] == "raises":
        return x == y
    if _number(x) and _number(y):
        return x == y or math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12)
    if isinstance(x, tuple) and isinstance(y, tuple):
        return len(x) == len(y) and all(same(("value", p), ("value", q)) for p, q in zip(x, y))
    return type(x) is type(y) and x == y


def a_date(rng):
    roll = rng.random()
    if roll < 0.05:
        return datetime.date.max - datetime.timedelta(days=rng.randrange(0, 400))
    if roll < 0.10:
        return datetime.date.min + datetime.timedelta(days=rng.randrange(0, 400))
    if roll < 0.30:
        year = rng.choice([2024, 2026, 2028, 1900, 2000, 2100])
        month = rng.randint(1, 12)
        return datetime.date(year, month, calendar.monthrange(year, month)[1] - rng.randrange(0, 3))
    return datetime.date(2000, 1, 1) + datetime.timedelta(days=rng.randrange(0, 365 * 40))


def a_count(rng):
    roll = rng.random()
    if roll < 0.70:
        return rng.randint(-40, 40)
    if roll < 0.80:
        return rng.choice([10 ** 6, -(10 ** 6), 10 ** 12, -(10 ** 12), 10 ** 30, 119988, -119988])
    return rng.choice([True, False, 1.0, -2.5, None, "1", [1]])


def a_unit(rng):
    return rng.choice(["d", "w", "m", "y", "d", "w", "m", "y", "D", "M", "", "q", None, 1, "mm"])


def a_horizon(rng):
    if rng.random() < 0.5:
        return "%d%s" % (rng.randint(1, 30), rng.choice("dwmy"))
    alphabet = "0123456789dwmyDWMYq .+-\t\u0661\uff11\u00b2"
    text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 6)))
    return rng.choice([text, text, None, 1, b"1w", ["1w"]])


def observations(rng):
    base = datetime.date(2026, 1, 1)
    pairs = []
    for _ in range(rng.randrange(0, 10)):
        when = base + datetime.timedelta(days=rng.randrange(0, 30))
        value = rng.choice([rng.random(), None, "x", rng.randint(-5, 5)])
        pairs.append((when, value) if rng.random() < 0.8 else [when, value])
    return pairs, base + datetime.timedelta(days=rng.randrange(-3, 33))


NUMBERS = [0, 0.0, -0.0, 1, -1, 4.21, 4.33, -0.5, 100.0, 1e-300, 1e300, -1e308, 1e308,
           10 ** 20, 10 ** 400, float("nan"), float("inf"), -float("inf"), None, True,
           False, "4.21", [1.0]]


def a_level(rng):
    return rng.choice(NUMBERS) if rng.random() < 0.4 else rng.uniform(-50, 150)


def a_kind(rng):
    return rng.choice(["bp", "return", "points", "bp", "return", "points", "bps", "BP", "",
                       None, 1])


def a_band(rng):
    if rng.random() < 0.6:
        return rng.choice([0.0, 0, 0.5, 1, 10.0])
    return rng.choice([-0.1, -1, None, float("nan"), float("inf"), True, "0", 1e300])


def a_direction(rng):
    band = a_band(rng)
    change = rng.choice([a_level(rng), rng.uniform(-1, 1)])
    if _number(band) and rng.random() < 0.3:
        change = rng.choice([band, -band])  # exactly at the band's edge
    return change, band


def fuzz(root, n, seed):
    dates, finance, ref = load(root)
    rng = random.Random(seed)
    cases = {
        "add_months": lambda: (a_date(rng), a_count(rng)),
        "parse_horizon": lambda: (a_horizon(rng),),
        "shift_date": lambda: (a_date(rng), a_count(rng), a_unit(rng)),
        "horizon_end": lambda: (a_date(rng), a_horizon(rng)),
        "last_on_or_before": lambda: observations(rng),
        "first_on_or_after": lambda: observations(rng),
        "level_change": lambda: (a_level(rng), a_level(rng), a_kind(rng)),
        "direction": lambda: a_direction(rng),
    }
    mismatches = 0
    for name, make in cases.items():
        built = getattr(finance if name in FINANCE else dates, name)
        expected = getattr(ref, name)
        bad = 0
        for _ in range(n):
            args = make()
            got, want = outcome(built, args), outcome(expected, args)
            if not same(got, want):
                bad += 1
                if bad <= 5:
                    print("  %s%r: build %r, reference %r" % (name, args, got, want))
        print("%-18s %6d calls, %d mismatch(es)" % (name, n, bad))
        mismatches += bad
    print("total: %d calls, %d mismatch(es)" % (n * len(cases), mismatches))
    return 1 if mismatches else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("suite", help="run the acceptance suite")
    s.add_argument("--tools", help="the folder holding the tools package (default: the reference)")
    f = sub.add_parser("fuzz", help="compare a build with the reference")
    f.add_argument("--tools", required=True, help="the folder holding the tools package")
    f.add_argument("--n", type=int, default=2500, help="calls per function (default 2500)")
    f.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.command == "fuzz":
        return fuzz(package_root(args.tools), args.n, args.seed)
    if args.tools:
        return run_suite(package_root(args.tools))
    with tempfile.TemporaryDirectory() as tmp:
        return run_suite(reference_build(Path(tmp)))


if __name__ == "__main__":
    sys.exit(main())
