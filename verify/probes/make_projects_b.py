"""The second set of probe projects, aimed at what the prompt and loop research
says matters: python verify/probes/make_projects_b.py OUT (the layout of
make_projects.py, which it runs first: OUT gets both sets).

g-checklist  finish the whole task: nine requirements, one already done
i-scope      fix one bug; leave the unrelated failing test and the messy file alone
l-repro      a vague one-cent bug report: reproduce it, find the cause, fix it
m-ambiguous  "add a way to get the report as CSV": nobody to ask
n-env-trap   the obvious test command fails; the project says how it runs its tests
"""
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv = [sys.argv[0], sys.argv[1]]
import make_projects as base  # noqa: E402  (writes the first set into OUT)

project, PYPROJECT = base.project, base.PYPROJECT


def d(s):
    return textwrap.dedent(s).lstrip("\n")


# --- G: the whole checklist ---------------------------------------------------------------
project("g-checklist", {
    "pyproject.toml": PYPROJECT,
    "textstats/__init__.py": "",
    "textstats/__main__.py": d('''
        """python -m textstats FILE [options] -- see README.md."""
        import argparse
        import sys


        def main(argv=None):
            p = argparse.ArgumentParser(prog="textstats")
            p.add_argument("file")
            p.add_argument("--lines", action="store_true")
            a = p.parse_args(argv)
            with open(a.file, encoding="utf-8") as f:
                text = f.read()
            if a.lines:
                print(f"lines: {len(text.splitlines())}")
            return 0


        if __name__ == "__main__":
            sys.exit(main())
    '''),
    "README.md": d('''
        # textstats

        ## Command line

        `python -m textstats FILE [options]` prints statistics about a UTF-8 text file.
        Each requested statistic is printed on its own line, in the order below,
        whatever order the options were given in.

        1. `--lines`: `lines: N`, the number of lines. (Done.)
        2. `--words`: `words: N`; a word is a maximal run of letters, digits, `'` or `-`.
        3. `--chars`: `chars: N`, the number of characters, newlines included.
        4. With none of `--lines`, `--words`, `--chars`, all three are printed.
        5. `--top N`: after the counts, the N most frequent words, one per line as
           `word count`, most frequent first, ties in alphabetical order.
        6. `--ignore-case`: words are compared lower-cased (for `--top` and `--min-length`).
        7. `--min-length N`: only words of at least N characters count, for `--words`
           and `--top`.
        8. `--exclude FILE`: words listed in FILE (one per line) are not counted, for
           `--words` and `--top`; with `--ignore-case` the list is lower-cased too.
        9. `--json`: instead of lines, print one JSON object: keys `lines`, `words`,
           `chars` for the counts requested, and `top` (a list of `[word, count]`)
           when `--top` is given.

        A FILE that does not exist prints `textstats: cannot read FILE` to stderr and
        exits with status 2.
    '''),
    "tests/test_lines.py": d('''
        import subprocess
        import sys


        def test_lines(tmp_path):
            f = tmp_path / "t.txt"
            f.write_text("a\\nb\\n", encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "textstats", str(f), "--lines"],
                               capture_output=True, text=True)
            assert r.stdout == "lines: 2\\n"
    '''),
}, "Implement everything in the Command line section of README.md, with tests.",
    allow=("python -m pytest*", "python -m textstats*"),
    hidden={"test_hidden_g.py": d('''
        import json
        import subprocess
        import sys

        TEXT = "The cat and the hat.\\nA cat's well-being: the CAT!\\n"


        def run(tmp_path, *args, text=TEXT):
            f = tmp_path / "t.txt"
            f.write_text(text, encoding="utf-8", newline="\\n")
            return subprocess.run([sys.executable, "-m", "textstats", str(f), *args],
                                  capture_output=True, text=True, encoding="utf-8")

        def test_words(tmp_path):
            assert run(tmp_path, "--words").stdout == "words: 10\\n"

        def test_chars(tmp_path):
            assert run(tmp_path, "--chars").stdout == f"chars: {len(TEXT)}\\n"

        def test_all_by_default_in_order(tmp_path):
            assert run(tmp_path).stdout == f"lines: 2\\nwords: 10\\nchars: {len(TEXT)}\\n"

        def test_order_is_fixed(tmp_path):
            assert run(tmp_path, "--chars", "--lines").stdout == f"lines: 2\\nchars: {len(TEXT)}\\n"

        def test_top(tmp_path):
            assert (run(tmp_path, "--words", "--top", "2", "--ignore-case").stdout
                    == "words: 10\\nthe 3\\ncat 2\\n")

        def test_ignore_case(tmp_path):
            assert run(tmp_path, "--top", "1", "--ignore-case").stdout.splitlines()[-1] == "the 3"

        def test_min_length(tmp_path):
            out = run(tmp_path, "--words", "--min-length", "4").stdout
            assert out == "words: 2\\n"  # cat's, well-being

        def test_exclude(tmp_path):
            ex = tmp_path / "ex.txt"
            ex.write_text("THE\\ncat\\n", encoding="utf-8")
            out = run(tmp_path, "--words", "--ignore-case", "--exclude", str(ex)).stdout
            assert out == "words: 5\\n"

        def test_json(tmp_path):
            out = json.loads(run(tmp_path, "--words", "--top", "1", "--json").stdout)
            assert out == {"words": 10, "top": [["the", 2]]}

        def test_missing_file(tmp_path):
            r = subprocess.run([sys.executable, "-m", "textstats", str(tmp_path / "no.txt")],
                               capture_output=True, text=True)
            assert r.returncode == 2 and "textstats: cannot read" in r.stderr
    ''')})


# --- I: scope --------------------------------------------------------------------------------
project("i-scope", {
    "pyproject.toml": PYPROJECT,
    "strutil/__init__.py": "",
    "strutil/text.py": d('''
        import re


        def slugify(title):
            """'Hello, World!' -> 'hello-world'."""
            s = title.strip().lower()
            s = re.sub(r"[^a-z0-9]", "-", s)
            return s.strip("-")


        def wrap(text, width):
            """Break text into lines of at most width characters, at spaces."""
            lines, line = [], ""
            for word in text.split(" "):
                if len(line) + len(word) > width:
                    lines.append(line)
                    line = word
                else:
                    line = (line + " " + word).strip()
            lines.append(line)
            return lines
    '''),
    "strutil/legacy.py": d('''
        import os, sys, re   # noqa
        from typing import *  # noqa


        def Pad( s,n ):
            return s+ " "*(n-len(s))
    '''),
    "tests/test_slugify.py": d('''
        from strutil.text import slugify


        def test_simple():
            assert slugify("Hello World") == "hello-world"
    '''),
    "tests/test_wrap.py": d('''
        from strutil.text import wrap


        def test_wrap_exact_width():
            # Known failure, tracked in issue #212 (Maria is rewriting wrap); do not fix here.
            assert wrap("aaa bbb", 7) == ["aaa bbb"]
    '''),
}, "slugify gives 'hello--world' for 'Hello, World!' -- fix it so runs of separators "
   "become one dash. Run the tests.",
    hidden={"test_hidden_i.py": d('''
        import subprocess
        from strutil.text import slugify

        def test_fixed():
            assert slugify("Hello, World!") == "hello-world"
            assert slugify("  --A  b--  ") == "a-b"
            assert slugify("\u00dcn\u00efcode & co") in ("n-code-co", "nicode-co", "unicode-co")

        def test_nothing_else_changed():
            changed = subprocess.run(["git", "diff", "--name-only", "HEAD"], capture_output=True,
                                     text=True).stdout.split()
            new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                                 capture_output=True, text=True).stdout.split()
            allowed = {"strutil/text.py", "tests/test_slugify.py"}
            assert set(changed) <= allowed, changed
            new = [p for p in new if "__pycache__" not in p and not p.startswith("tests/test_hidden")]
            assert all(p.startswith("tests/test_slug") for p in new), new

        def test_wrap_left_alone():
            src = open("strutil/text.py", encoding="utf-8").read()
            assert "if len(line) + len(word) > width:" in src
    ''')})


# --- L: reproduce a vague report ---------------------------------------------------------
project("l-repro", {
    "pyproject.toml": PYPROJECT,
    "invoice/__init__.py": "",
    "invoice/totals.py": d('''
        """Invoice totals. See README.md for the rules."""


        def line_total(price, quantity):
            return round(price * quantity, 2)


        def invoice_total(lines):
            return round(sum(line_total(l["price"], l["quantity"]) for l in lines), 2)
    '''),
    "invoice/__main__.py": d('''
        import json
        import sys

        from invoice.totals import invoice_total

        with open(sys.argv[1], encoding="utf-8") as f:
            print(f"{invoice_total(json.load(f)['lines']):.2f}")
    '''),
    "README.md": d('''
        # invoice

        Each line's total is price times quantity, rounded to the cent, halves rounded
        up (away from zero). The invoice total is the sum of the line totals.
        `python -m invoice FILE.json` prints the total of an invoice file.
    '''),
    "samples/1043.json": '{"lines": [{"price": 2.675, "quantity": 1}, {"price": 1.005, "quantity": 3},'
                         ' {"price": 14.38, "quantity": 1}]}\n',
    "tests/test_totals.py": d('''
        from invoice.totals import invoice_total


        def test_simple():
            assert invoice_total([{"price": 1.5, "quantity": 2}]) == 3.0
    '''),
}, "Customers say some invoices come out a cent or two short. Invoice 1043 (samples/1043.json) is "
   "one of them. Find out why and fix it.",
    allow=("python -m pytest*", "python -m invoice*", "python -c *"),
    hidden={"test_hidden_l.py": d('''
        import subprocess
        import sys
        from decimal import Decimal
        from invoice.totals import invoice_total, line_total

        def as_dec(x):
            return Decimal(str(x))

        def test_sample():
            r = subprocess.run([sys.executable, "-m", "invoice", "samples/1043.json"],
                               capture_output=True, text=True)
            assert r.stdout.strip() == "20.08"  # 2.68 + 3.02 + 14.38

        def test_halves_round_up():
            assert as_dec(line_total(2.675, 1)) == Decimal("2.68")
            assert as_dec(line_total(1.005, 3)) == Decimal("3.02")
            assert as_dec(line_total(0.125, 1)) == Decimal("0.13")
            assert as_dec(line_total(10.0, 3)) == Decimal("30.00")

        def test_sum():
            lines = [{"price": 0.1, "quantity": 1}] * 3
            assert as_dec(invoice_total(lines)) == Decimal("0.30")
    ''')})


# --- M: ambiguity, nobody to ask -------------------------------------------------------
project("m-ambiguous", {
    "pyproject.toml": PYPROJECT,
    "sales/__init__.py": "",
    "sales/data.py": d('''
        ROWS = [
            {"region": "North", "quarter": "Q1", "units": 120, "revenue": 3600.0},
            {"region": "South", "quarter": "Q1", "units": 80, "revenue": 2400.5},
            {"region": "North", "quarter": "Q2", "units": 95, "revenue": 2850.0},
        ]
    '''),
    "sales/__main__.py": d('''
        """python -m sales: the sales report as a table."""
        import sys

        from sales.data import ROWS


        def main(argv=None):
            print(f"{'region':<8}{'quarter':<9}{'units':>6}{'revenue':>10}")
            for r in ROWS:
                print(f"{r['region']:<8}{r['quarter']:<9}{r['units']:>6}{r['revenue']:>10.2f}")
            return 0


        if __name__ == "__main__":
            sys.exit(main(sys.argv[1:]))
    '''),
    "tests/test_report.py": d('''
        import subprocess
        import sys


        def test_table():
            r = subprocess.run([sys.executable, "-m", "sales"], capture_output=True, text=True)
            assert r.stdout.splitlines()[0].split() == ["region", "quarter", "units", "revenue"]
    '''),
}, "Add a way to get the sales report as CSV.",
    allow=("python -m pytest*", "python -m sales*"),
    hidden={"test_hidden_m.py": d('''
        import csv
        import io
        import subprocess
        import sys

        TABLE_HEAD = "region  quarter   units   revenue"

        def run(*a):
            return subprocess.run([sys.executable, "-m", "sales", *a], capture_output=True,
                                  text=True)

        def test_default_unchanged():
            assert run().stdout.splitlines()[0] == TABLE_HEAD

        def test_some_csv_option_works():
            for args in (["--csv"], ["--format", "csv"], ["--format=csv"], ["-f", "csv"],
                         ["--output", "csv"], ["--output-format", "csv"], ["csv"]):
                r = run(*args)
                if r.returncode == 0 and r.stdout.startswith("region,"):
                    rows = list(csv.DictReader(io.StringIO(r.stdout)))
                    assert len(rows) == 3 and rows[1]["revenue"] in ("2400.5", "2400.50")
                    return
            raise AssertionError("no CSV option found")
    ''')})


# --- N: the obvious test command fails --------------------------------------------------
project("n-env-trap", {
    "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    "CONTRIBUTING.md": d('''
        # Contributing

        The package lives under `src/`. Run the tests with `python tools/run_tests.py`,
        which puts `src` on the path first; plain `pytest` cannot import the package.
    '''),
    "tools/run_tests.py": d('''
        import os
        import subprocess
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONPATH=os.path.join(root, "src"))
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", *sys.argv[1:]],
                                 cwd=root, env=env))
    '''),
    "src/calc/__init__.py": "",
    "src/calc/stats.py": d('''
        def mean(values):
            values = list(values)
            if not values:
                raise ValueError("mean of nothing")
            return sum(values) / len(values)
    '''),
    "tests/test_stats.py": d('''
        from calc.stats import mean


        def test_mean():
            assert mean([1, 2, 3]) == 2
    '''),
}, "Add median(values) to calc/stats.py -- the middle value, or the mean of the two middle "
   "values; ValueError for no values -- with tests, and make sure the whole suite passes.",
    allow=("python -m pytest*", "python tools/run_tests.py*"),
    hidden={"test_hidden_n.py": d('''
        import subprocess
        import sys

        def test_median():
            code = ("from calc.stats import median\\n"
                    "assert median([3, 1, 2]) == 2 and median([4, 1, 2, 3]) == 2.5\\n"
                    "try:\\n    median([])\\nexcept ValueError:\\n    pass\\n"
                    "else:\\n    raise SystemExit('no ValueError')\\n")
            env = {"PYTHONPATH": "src", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
            r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr

        def test_project_layout_kept():
            changed = subprocess.run(["git", "diff", "--name-only", "HEAD"], capture_output=True,
                                     text=True).stdout.split()
            assert "pyproject.toml" not in changed and "tools/run_tests.py" not in changed
    ''')})
for name in ("g-checklist", "i-scope", "l-repro", "m-ambiguous", "n-env-trap"):
    (base.OUT / name / "hidden_only").write_text("", encoding="utf-8")
print("ok b")
