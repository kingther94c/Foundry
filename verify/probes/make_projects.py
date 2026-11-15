"""Write the first set of probe projects: python verify/probes/make_projects.py OUT

Each project OUT/NAME has repo/ (the scaffold Foundry gets), task.txt, allow.txt
(run_command patterns allowed, one per line), extra.toml (more config, may be
empty) and hidden/ (acceptance tests run after, never shown to Foundry).
"""
import shutil
import sys
import textwrap
from pathlib import Path

OUT = Path(sys.argv[1])
PYPROJECT = '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n'


def d(s):
    return textwrap.dedent(s).lstrip("\n")


def project(name, files, task, allow=("python -m pytest*",), extra="", hidden=None,
            binary=None):
    root = OUT / name
    if root.exists():
        shutil.rmtree(root)
    repo = root / "repo"
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    for rel, data in (binary or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (root / "task.txt").write_text(task.strip() + "\n", encoding="utf-8")
    (root / "allow.txt").write_text("\n".join(allow) + "\n", encoding="utf-8")
    (root / "extra.toml").write_text(extra, encoding="utf-8")
    h = root / "hidden"
    h.mkdir()
    for rel, text in (hidden or {}).items():
        (h / rel).write_text(text, encoding="utf-8", newline="\n")


# --- A: greenfield ---------------------------------------------------------------
project("a-greenfield", {
    "pyproject.toml": PYPROJECT,
    "README.md": d('''
        # inicfg

        A small INI reader. Write the package `inicfg/` and its tests from this spec.

        - `inicfg.parse(text: str) -> dict[str, dict[str, str]]`
          - `[name]` starts a section; the name is stripped and kept as written.
          - Before any section header, keys go in the section `"DEFAULT"`.
          - `key = value` and `key: value` (the first `=` or `:` splits). Keys are
            stripped and lower-cased; values are stripped. A later key wins.
          - Blank lines, and lines whose first non-blank character is `#` or `;`,
            are ignored. Nothing else is a comment: `a = b # c` has the value `b # c`.
          - A section that appears twice is one section (its keys merge).
          - Any other line raises `inicfg.ParseError`, whose `line` attribute is the
            1-based line number and whose message contains it.
          - An empty section still appears, as `{}`. `"DEFAULT"` appears only if it
            has keys.
        - `python -m inicfg FILE` prints `json.dumps(parse(text), indent=2,
          sort_keys=True)` of the file (read as UTF-8) and exits 0; on a ParseError it
          prints `error: line N: ...` to stderr and exits 1; a missing file exits 2
          with a message on stderr.
        '''),
    "tests/.gitkeep": "",
}, d('''
    Implement the inicfg package described in README.md, with unit tests under tests/.
    Run `python -m pytest -q` before you finish.
    '''), allow=("python -m pytest*", "python -m inicfg*"), hidden={"test_hidden_a.py": d('''
    import json, subprocess, sys
    import pytest
    import inicfg

    def test_basic():
        text = "x=1\\n[Main]\\n Name : Foo \\n; c\\n# c\\n\\nA = 1 # not a comment\\n[Empty]\\n[Main]\\nname=Bar\\n"
        assert inicfg.parse(text) == {"DEFAULT": {"x": "1"}, "Main": {"name": "Bar", "a": "1 # not a comment"}, "Empty": {}}

    def test_no_default_when_empty():
        assert inicfg.parse("[s]\\nk=v\\n") == {"s": {"k": "v"}}
        assert inicfg.parse("") == {}

    def test_first_separator_splits():
        assert inicfg.parse("[s]\\nurl = http://x:8080/a=b\\n")["s"]["url"] == "http://x:8080/a=b"
        assert inicfg.parse("[s]\\nk: a=b\\n")["s"]["k"] == "a=b"

    def test_error_line():
        with pytest.raises(inicfg.ParseError) as info:
            inicfg.parse("[s]\\nok=1\\njunk\\n")
        assert info.value.line == 3 and "3" in str(info.value)

    def test_cli(tmp_path):
        f = tmp_path / "c.ini"; f.write_text("[b]\\nk=1\\n[a]\\n", encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "inicfg", str(f)], capture_output=True, text=True)
        assert r.returncode == 0 and json.loads(r.stdout) == {"a": {}, "b": {"k": "1"}}
        assert r.stdout == json.dumps({"a": {}, "b": {"k": "1"}}, indent=2, sort_keys=True) + "\\n"
        f.write_text("[a]\\nbad\\n", encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "inicfg", str(f)], capture_output=True, text=True)
        assert r.returncode == 1 and r.stderr.startswith("error: line 2")
        r = subprocess.run([sys.executable, "-m", "inicfg", str(tmp_path / "none.ini")], capture_output=True, text=True)
        assert r.returncode == 2 and r.stderr
    ''')})

# --- B: surgical edit in a long file of look-alikes ------------------------------------
funcs = []
for i in range(1, 41):
    rate = f"0.{i:02d}" if i < 100 else "1.00"
    funcs.append(d(f'''
        def fee_tier_{i:02d}(amount):
            """Fee for tier {i}: {i} percent of the amount, to the cent."""
            if amount < 0:
                raise ValueError("amount must not be negative")
            fee = amount * {rate}
            return round(fee, 2)
        '''))
project("b-surgical", {
    "pyproject.toml": PYPROJECT,
    "ledger/__init__.py": "",
    "ledger/rules.py": '"""Fee rules, one function per tier."""\n\n\n' + "\n\n".join(funcs),
    "tests/test_rules.py": d('''
        from ledger import rules

        def test_every_tier_is_a_percentage():
            for i in range(1, 41):
                assert getattr(rules, f"fee_tier_{i:02d}")(100) == float(i)
        '''),
}, d('''
    Two fee rules in ledger/rules.py change, and nothing else:
    - tier 27's fee is capped at 50.00 (never more than 50.00);
    - tier 7's fee is at least 1.00 (never less than 1.00), except that an amount of 0 still has a fee of 0.
    The existing test checks every tier at 100, which both changes keep; update it only if you must, and add tests for the new rules.
    Run `python -m pytest -q` before you finish.
    '''), hidden={"test_hidden_b.py": d('''
    import ast, pathlib
    from ledger import rules

    def test_rules():
        assert rules.fee_tier_27(1000) == 50.0 and rules.fee_tier_27(100) == 27.0
        assert rules.fee_tier_07(10) == 1.0 and rules.fee_tier_07(0) == 0 and rules.fee_tier_07(100) == 7.0
        assert rules.fee_tier_17(1000) == 170.0 and rules.fee_tier_26(1000) == 260.0

    def test_only_two_functions_changed():
        import subprocess
        old = subprocess.run(["git", "show", "HEAD:ledger/rules.py"], capture_output=True, text=True, check=True).stdout
        new = pathlib.Path("ledger/rules.py").read_text(encoding="utf-8")
        def funcs(src):
            return {n.name: ast.dump(n) for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
        o, n = funcs(old), funcs(new)
        changed = sorted(k for k in o if o[k] != n.get(k))
        assert changed == ["fee_tier_07", "fee_tier_27"], changed
        assert set(n) == set(o)
    ''')})

# --- C: debug, the cause elsewhere, a decoy, TODOs to count --------------------------------
project("c-debug", {
    "pyproject.toml": PYPROJECT,
    "shipping/__init__.py": "",
    "shipping/calendar_utils.py": d('''
        """Business-day helpers."""
        import datetime as dt


        def is_weekend(day: dt.date) -> bool:
            return day.weekday() > 5


        def add_business_days(start: dt.date, days: int) -> dt.date:
            current = start
            added = 0
            while added < days:
                current += dt.timedelta(days=1)
                if not is_weekend(current):
                    added += 1
            return current
        '''),
    "shipping/pricing.py": d('''
        """Shipping prices. TODO: regional surcharges."""


        def price(weight_kg: float, express: bool) -> float:
            # Looks odd, is right: the first kilo is included in the base.
            extra = max(0.0, weight_kg - 1.0)
            base = 4.99 + extra * 1.25
            return round(base * (1.8 if express else 1.0), 2)  # TODO: express cap
        '''),
    "shipping/orders.py": d('''
        """Orders and their promised delivery dates."""
        import datetime as dt

        from shipping.calendar_utils import add_business_days
        from shipping.pricing import price


        def quote(ordered: dt.date, weight_kg: float, express: bool = False) -> dict:
            days = 1 if express else 3
            # TODO: holidays
            return {"deliver_by": add_business_days(ordered, days),
                    "price": price(weight_kg, express)}
        '''),
    "tests/test_orders.py": d('''
        import datetime as dt
        from shipping.orders import quote

        def test_friday_standard_skips_the_weekend():
            assert quote(dt.date(2026, 11, 13), 2.0)["deliver_by"] == dt.date(2026, 11, 18)

        def test_thursday_express():
            assert quote(dt.date(2026, 11, 12), 0.5, express=True)["deliver_by"] == dt.date(2026, 11, 13)

        def test_price():
            assert quote(dt.date(2026, 11, 12), 3.0)["price"] == 7.49
        '''),
}, d('''
    The test suite fails. Find the root cause and fix it there, without changing the tests.
    Also report, in your final summary, how many TODO comments the repository has and where they are.
    Run `python -m pytest -q` before you finish.
    '''), hidden={"test_hidden_c.py": d('''
    import datetime as dt, subprocess
    from shipping.calendar_utils import is_weekend, add_business_days

    def test_weekend():
        assert is_weekend(dt.date(2026, 11, 14)) and is_weekend(dt.date(2026, 11, 15))
        assert not is_weekend(dt.date(2026, 11, 13))
        assert add_business_days(dt.date(2026, 11, 13), 1) == dt.date(2026, 11, 16)

    def test_tests_and_decoy_untouched():
        out = subprocess.run(["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True).stdout.split()
        assert "tests/test_orders.py" not in out and "shipping/pricing.py" not in out, out
    ''')})

# --- D: refactor across files -----------------------------------------------------------
callers = {}
for name in ("stores", "routes", "zones", "fleet", "depots", "report"):
    callers[f"geo/{name}.py"] = d(f'''
        """{name.title()} near a point."""
        from geo.utils import haversine_km


        def nearest_{name}(origin, places):
            return min(places, key=lambda p: haversine_km(origin, p))
        ''')
project("d-refactor", {
    "pyproject.toml": PYPROJECT,
    "geo/__init__.py": "",
    "geo/utils.py": d('''
        """Geometry helpers."""
        import math


        def haversine_km(a, b):
            """Great-circle distance in km between two (lat, lon) points in degrees."""
            lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
            h = (math.sin((lat2 - lat1) / 2) ** 2
                 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
            return 2 * 6371.0 * math.asin(math.sqrt(h))


        def clamp(x, lo, hi):
            return max(lo, min(hi, x))
        '''),
    **callers,
    "README.md": d('''
        # geo

        `geo.utils.haversine_km(a, b)` gives the distance in km between two points.
        Every `nearest_*` helper uses it.
        '''),
    "tests/test_geo.py": d('''
        from geo.utils import haversine_km
        from geo.stores import nearest_stores

        def test_distance():
            assert round(haversine_km((0, 0), (0, 1)), 1) == 111.2

        def test_nearest():
            assert nearest_stores((0, 0), [(0, 2), (0, 1)]) == (0, 1)
        '''),
}, d('''
    Move haversine_km out of geo/utils.py into a new module geo/distance.py and rename it great_circle_km.
    Update every caller, the tests and README.md to use geo.distance.great_circle_km.
    Keep geo.utils.haversine_km working as a deprecated alias that emits a DeprecationWarning and returns the same result; clamp stays in geo/utils.py.
    Run `python -m pytest -q` before you finish.
    '''), hidden={"test_hidden_d.py": d('''
    import pathlib, re, warnings
    import pytest

    def test_new_home():
        from geo.distance import great_circle_km
        assert round(great_circle_km((0, 0), (0, 1)), 1) == 111.2

    def test_alias_warns():
        from geo import utils
        with pytest.warns(DeprecationWarning):
            assert round(utils.haversine_km((0, 0), (0, 1)), 1) == 111.2
        assert utils.clamp(5, 0, 3) == 3

    def test_no_caller_uses_the_old_name():
        for p in pathlib.Path("geo").glob("*.py"):
            if p.name in ("utils.py",):
                continue
            assert "haversine_km" not in p.read_text(encoding="utf-8"), p
        assert "great_circle_km" in pathlib.Path("README.md").read_text(encoding="utf-8")
        for p in pathlib.Path("tests").glob("*.py"):
            text = p.read_text(encoding="utf-8")
            # A test of the deprecated alias may import it; no other test should.
            assert "geo.utils import haversine_km" not in text or "DeprecationWarning" in text, p

    def test_callers_work_without_warning():
        import importlib
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for name in ("stores", "routes", "zones", "fleet", "depots", "report"):
                mod = importlib.import_module(f"geo.{name}")
                assert getattr(mod, f"nearest_{name}")((0, 0), [(0, 2), (0, 1)]) == (0, 1)
    ''')})

# --- E: line endings, BOM, tabs, non-ASCII ----------------------------------------------
# The accented text and the byte order mark are what this probe is about. They are
# written as escapes because the sources here stay ASCII; the files written hold the
# characters themselves.
project("e-encodings", {
    "pyproject.toml": PYPROJECT,
    "app/__init__.py": "",
    "app/messages_fr.py": d('''
        """Messages en fran\u00e7ais."""

        GREETING = "Bonjour, caf\u00e9 ouvert \u00e0 8 h"
        FAREWELL = "\u00c0 bient\u00f4t \u2014 merci !"
        '''),
    "tests/test_messages.py": d('''
        from app.messages_fr import GREETING

        def test_greeting():
            assert "caf\u00e9" in GREETING
        '''),
}, d('''
    Make these edits, and change nothing else in any file (keep each file's line endings, byte order mark, indentation and characters as they are):
    1. app/messages_fr.py: GREETING opens at 9 h instead of 8 h; add EVENING = "Bonsoir, \u00e0 demain".
    2. config.ini: in the [server] section add the line `timeout = 30` after the port line.
    3. Makefile: add a target `lint` whose recipe runs `python -m pyflakes app`, after the test target.
    4. data/notes.txt: append a final line `4. V\u00e9rifier les accents`.
    5. Add a test for EVENING.
    Run `python -m pytest -q` before you finish.
    '''), binary={
    "config.ini": "\ufeff[server]\r\nhost = 0.0.0.0\r\nport = 8080\r\n\r\n[client]\r\nretries = 3\r\n".encode("utf-8"),
    "Makefile": b"test:\n\tpython -m pytest -q\n\nclean:\n\trm -rf .pytest_cache\n",
    "data/notes.txt": "1. Pr\u00e9parer\r\n2. Cuire\r\n3. Servir\r\n".encode("utf-8"),
}, hidden={"test_hidden_e.py": d('''
    import pathlib

    def b(p):
        return pathlib.Path(p).read_bytes()

    def test_messages():
        from app import messages_fr as m
        assert m.GREETING == "Bonjour, caf\u00e9 ouvert \u00e0 9 h"
        assert m.EVENING == "Bonsoir, \u00e0 demain"
        assert m.FAREWELL == "\u00c0 bient\u00f4t \u2014 merci !"

    def test_config_bom_crlf():
        data = b("config.ini")
        assert data.startswith(b"\\xef\\xbb\\xbf"), data[:10]
        assert data.replace(b"\\r\\n", b"").count(b"\\n") == 0, "a bare LF crept in"
        assert data == "\\ufeff[server]\\r\\nhost = 0.0.0.0\\r\\nport = 8080\\r\\ntimeout = 30\\r\\n\\r\\n[client]\\r\\nretries = 3\\r\\n".encode("utf-8"), data

    def test_makefile_tabs():
        text = b("Makefile").decode("utf-8")
        assert "\\r" not in text
        assert "lint:\\n\\tpython -m pyflakes app" in text, text
        assert text.index("test:") < text.index("lint:")
        assert "\\tpython -m pytest -q\\n" in text and "clean:\\n\\trm -rf .pytest_cache\\n" in text

    def test_notes_crlf():
        assert b("data/notes.txt") in ("1. Pr\u00e9parer\\r\\n2. Cuire\\r\\n3. Servir\\r\\n4. V\u00e9rifier les accents\\r\\n".encode("utf-8"),
                                       "1. Pr\u00e9parer\\r\\n2. Cuire\\r\\n3. Servir\\r\\n4. V\u00e9rifier les accents".encode("utf-8"))
    ''')})

# --- F: a longer task under a small context budget ----------------------------------------
project("f-long", {
    "pyproject.toml": PYPROJECT,
    "README.md": d('''
        # tinykv

        Build `tinykv/` in five steps, each with tests:

        1. `Store()` with `put(key, value)`, `get(key, default=None)`, `delete(key) -> bool`
           (True if the key was there) and `len(store)`. Keys are strings (anything
           else raises TypeError); values are any JSON value.
        2. TTL: `put(key, value, ttl=None)`; with ttl seconds the key expires after
           it. `Store(clock=time.monotonic)` takes the clock, so tests pass a fake one.
           An expired key is gone for `get`, `delete` and `len`.
        3. `snapshot(path)` writes the live (unexpired) keys as JSON
           `{"version": 1, "items": {key: value}}`, UTF-8, sorted keys; TTLs are not
           saved. `Store.load(path, clock=...)` reads one back; a wrong version raises
           ValueError.
        4. `keys(prefix="")` returns the live keys starting with prefix, sorted.
        5. `python -m tinykv FILE get KEY`, `put KEY JSON`, `delete KEY`, `keys [PREFIX]`
           work on a snapshot file (created when missing): get prints the value as
           JSON (exit 1 if missing), put and delete save the file, keys prints one per line.
        '''),
    "tests/.gitkeep": "",
}, d('''
    Build tinykv as README.md describes, step by step, keeping a plan with update_plan.
    Add tests for each step and run `python -m pytest -q` before you finish.
    '''), allow=("python -m pytest*", "python -m tinykv*"),
    extra="[runtime]\nauto_compact_token_limit = 12000\n",
    hidden={"test_hidden_f.py": d('''
    import json, subprocess, sys
    import pytest
    from tinykv import Store

    class Clock:
        def __init__(self): self.t = 0.0
        def __call__(self): return self.t

    def test_store_and_ttl(tmp_path):
        c = Clock(); s = Store(clock=c)
        s.put("a", 1); s.put("b", {"x": [1]}, ttl=10)
        assert len(s) == 2 and s.get("b") == {"x": [1]}
        c.t = 11
        assert s.get("b") is None and len(s) == 1 and s.delete("b") is False
        assert s.delete("a") is True and s.get("a", "d") == "d"
        with pytest.raises(TypeError):
            s.put(1, 2)

    def test_snapshot_and_keys(tmp_path):
        c = Clock(); s = Store(clock=c)
        s.put("app.x", 1); s.put("app.y", 2, ttl=1); s.put("b", 3)
        c.t = 2
        p = tmp_path / "s.json"; s.snapshot(p)
        assert json.loads(p.read_text(encoding="utf-8")) == {"version": 1, "items": {"app.x": 1, "b": 3}}
        t = Store.load(p, clock=c)
        assert t.keys() == ["app.x", "b"] and t.keys("app") == ["app.x"]
        p.write_text(json.dumps({"version": 2, "items": {}}), encoding="utf-8")
        with pytest.raises(ValueError):
            Store.load(p, clock=c)

    def test_cli(tmp_path):
        f = str(tmp_path / "db.json")
        run = lambda *a: subprocess.run([sys.executable, "-m", "tinykv", f, *a], capture_output=True, text=True)
        assert run("put", "k", '{"a": 1}').returncode == 0
        r = run("get", "k"); assert r.returncode == 0 and json.loads(r.stdout) == {"a": 1}
        assert run("get", "nope").returncode == 1
        run("put", "k2", "2")
        assert run("keys").stdout.split() == ["k", "k2"]
        assert run("delete", "k").returncode == 0 and run("keys").stdout.split() == ["k2"]
    ''')})
print("ok")
