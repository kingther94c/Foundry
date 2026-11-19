"""The fourth set of probe projects, harder than the first two, whose eleven
projects a GPT model behind a gateway passed ten of in one run each:
python verify/probes/make_projects_d.py OUT

Each project OUT/NAME has the layout of make_projects.py -- repo/ (the scaffold
Foundry gets), task.txt, allow.txt, extra.toml, hidden/ -- and reference/,
the scaffold with a reference solution laid over it, for check_c.py (which
checks that the hidden tests fail on repo/ and pass on reference/). Each is
judged by its hidden tests alone (hidden_only): a hidden test runs the
project's own tests where leaving them red is a failure.

o-money     floats to whole cents across ten modules, old files still loading
p-spec      thirteen rules in a spec; the visible tests pass long before it is done
q-rootcause a stale build from a fingerprint that loses boundaries, next to a
            known failure that is someone else's
r-long      seven steps against a format document, under a 12,000-token
            compaction limit
"""
import hashlib
import shutil
import sys
import textwrap
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "projects-d"
PYPROJECT = '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n'


def d(s):
    return textwrap.dedent(s).lstrip("\n")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tree(root, files):
    for rel, text in files.items():
        if text is None:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def project(name, files, task, reference, hidden, allow=("python -m pytest*",), extra=""):
    root = OUT / name
    if root.exists():
        shutil.rmtree(root)
    tree(root / "repo", files)
    tree(root / "reference", {**files, **reference})
    (root / "task.txt").write_text(task.strip() + "\n", encoding="utf-8")
    (root / "allow.txt").write_text("\n".join(allow) + "\n", encoding="utf-8")
    (root / "extra.toml").write_text(extra, encoding="utf-8")
    tree(root / "hidden", hidden)
    (root / "hidden_only").write_text("", encoding="utf-8")


# A hidden test that the project's own tests pass, the hidden ones aside.
OWN_SUITE = d(r'''
    def test_the_projects_own_tests_pass():
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "--ignore-glob=*test_hidden_*", *DESELECT], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stdout[-3000:]
''')


# --- O: money as whole cents, across the package ---------------------------------------
SHOP = {
    "pyproject.toml": PYPROJECT,
    "README.md": d(r'''
        # shop

        Pricing for a small web shop: items, orders, bulk discounts and coupons,
        sales tax by region, shipping by weight, invoices and reports.

        - `python -m shop invoice ORDERS.json ID` prints an order's invoice.
        - `python -m shop report ORDERS.json` prints the totals by region.

        Every computation of money is in `shop/`; `data/` holds a catalog and a
        month of orders.
    '''),
    "MIGRATION.md": d(r'''
        # Money as whole cents

        Money is a float number of dollars throughout `shop/`, and floats drift:
        `round(2.675, 2)` is 2.67 and `round(10.00 * 0.0725, 2)` is 0.72, where
        the rules say 2.68 and 0.73. Every amount of money becomes an `int`
        number of cents, end to end. No float takes part in any computation of
        money; where a fraction of a cent comes up -- a percentage coupon, tax --
        it is rounded to the cent half up (half a cent becomes a cent), once,
        where it comes up.

        | Before | After |
        |---|---|
        | `models.Item.price` (dollars) | `Item.price_cents` |
        | `models.Line.total()` | `Line.total_cents()` |
        | `money.fmt(dollars)`, `money.parse(text) -> dollars` | `money.fmt(cents) -> "12.34"`, `money.parse(text) -> cents` (a third decimal or more: rounded half up) |
        | `discounts.COUPONS["FIVER"] == ("fixed", 5.00)` | `("fixed", 500)` |
        | `discounts.line_discount(line)` | `discounts.line_discount_cents(line)` |
        | `discounts.coupon_discount(amount, code)` | `discounts.coupon_discount_cents(amount_cents, code)` |
        | `tax.RATES` as fractions (`0.0725`) | `tax.RATES` in hundredths of a percent (`725`) |
        | `tax.tax(amount, region)` | `tax.tax_cents(amount_cents, region)` |
        | `shipping.FREE_OVER`, `BASE`, `PER_500G` | `FREE_OVER_CENTS`, `BASE_CENTS`, `PER_500G_CENTS` |
        | `shipping.shipping(amount, weight_g)` | `shipping.shipping_cents(amount_cents, weight_g)` |
        | `pricing.breakdown(order)` with keys `subtotal`, `discount`, `tax`, `shipping`, `total` | the same function; keys `subtotal_cents`, `discount_cents`, `tax_cents`, `shipping_cents`, `total_cents` |
        | `reports.total(orders)`, `reports.by_region(orders)` | `reports.total_cents(orders)`, `reports.by_region_cents(orders)` |

        Files: catalogs and order files written from now on hold `price_cents`
        (an int) where they held `price`. Files written before -- `data/` has
        some -- hold `price` in dollars and must still load, read as
        `money.parse` reads a price.

        The old names go: no aliases. The command line prints what it printed
        before, to the cent, except where the float drift had it a cent off.
    '''),
    "shop/__init__.py": "",
    "shop/money.py": d(r'''
        """Money helpers. Amounts are floats, in dollars."""


        def fmt(amount):
            """12.5 -> '12.50'."""
            return f"{amount:.2f}"


        def parse(text):
            """'12.50' -> 12.5."""
            return float(text)
    '''),
    "shop/models.py": d(r'''
        """What the shop sells and what customers order."""
        from dataclasses import dataclass, field


        @dataclass
        class Item:
            sku: str
            name: str
            price: float  # dollars
            weight_g: int = 0


        @dataclass
        class Line:
            item: Item
            quantity: int

            def total(self):
                return round(self.item.price * self.quantity, 2)


        @dataclass
        class Order:
            id: str
            region: str
            lines: list = field(default_factory=list)
            coupon: str = None

            def weight_g(self):
                return sum(line.item.weight_g * line.quantity for line in self.lines)
    '''),
    "shop/catalog.py": d(r'''
        """The catalog: a JSON file {"items": [{"sku", "name", "price", "weight_g"}]}."""
        import json

        from shop.models import Item


        def load(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return {row["sku"]: Item(row["sku"], row["name"], float(row["price"]),
                                     row.get("weight_g", 0))
                    for row in data["items"]}
    '''),
    "shop/discounts.py": d(r'''
        """Discounts: bulk lines and coupons."""

        # ("percent", N) takes N% off; ("fixed", D) takes D dollars off, never more
        # than the amount it applies to.
        COUPONS = {"TENOFF": ("percent", 10), "HALF": ("percent", 50), "FIVER": ("fixed", 5.00)}


        def line_discount(line):
            """BULK- items: every fourth one is free."""
            if not line.item.sku.startswith("BULK-"):
                return 0.0
            return round(line.quantity // 4 * line.item.price, 2)


        def coupon_discount(amount, code):
            if code is None:
                return 0.0
            if code not in COUPONS:
                raise KeyError(f"unknown coupon {code}")
            kind, value = COUPONS[code]
            if kind == "percent":
                return round(amount * value / 100, 2)
            return min(value, amount)
    '''),
    "shop/tax.py": d(r'''
        """Sales tax by region."""

        RATES = {"CA": 0.0725, "NY": 0.04, "TX": 0.0625, "OR": 0.0}


        def tax(amount, region):
            return round(amount * RATES[region], 2)
    '''),
    "shop/shipping.py": d(r'''
        """Shipping: free from FREE_OVER, else a base fee and a fee per started 500 g."""
        import math

        FREE_OVER = 50.00
        BASE = 4.99
        PER_500G = 0.50


        def shipping(amount, weight_g):
            if amount >= FREE_OVER:
                return 0.0
            return round(BASE + PER_500G * math.ceil(weight_g / 500), 2)
    '''),
    "shop/pricing.py": d(r'''
        """An order's breakdown."""
        from shop import discounts, shipping, tax


        def breakdown(order):
            subtotal = round(sum(line.total() for line in order.lines), 2)
            bulk = round(sum(discounts.line_discount(line) for line in order.lines), 2)
            after_bulk = round(subtotal - bulk, 2)
            coupon = discounts.coupon_discount(after_bulk, order.coupon)
            taxable = round(after_bulk - coupon, 2)
            sales_tax = tax.tax(taxable, order.region)
            ship = shipping.shipping(taxable, order.weight_g())
            return {
                "subtotal": subtotal,
                "discount": round(bulk + coupon, 2),
                "tax": sales_tax,
                "shipping": ship,
                "total": round(taxable + sales_tax + ship, 2),
            }
    '''),
    "shop/invoice.py": d(r'''
        """An order as text."""
        from shop.money import fmt
        from shop.pricing import breakdown

        ROWS = (("Subtotal", "subtotal"), ("Discount", "discount"), ("Tax", "tax"),
                ("Shipping", "shipping"), ("Total", "total"))


        def render(order):
            out = [f"Order {order.id} ({order.region})"]
            for line in order.lines:
                out.append(f"  {line.quantity:>3} x {line.item.name:<20}{fmt(line.total()):>10}")
            b = breakdown(order)
            for label, key in ROWS:
                if key == "discount":
                    if b[key]:
                        out.append(f"{label:<28}{'-' + fmt(b[key]):>10}")
                    continue
                out.append(f"{label:<28}{fmt(b[key]):>10}")
            return "\n".join(out) + "\n"
    '''),
    "shop/storage.py": d(r'''
        """Orders on disk: {"orders": [{"id", "region", "coupon",
        "lines": [{"sku", "name", "price", "weight_g", "quantity"}]}]}."""
        import json

        from shop.models import Item, Line, Order


        def load(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            orders = []
            for row in data["orders"]:
                lines = [Line(Item(l["sku"], l["name"], float(l["price"]), l.get("weight_g", 0)),
                              l["quantity"]) for l in row["lines"]]
                orders.append(Order(row["id"], row["region"], lines, row.get("coupon")))
            return orders


        def save(orders, path):
            data = {"orders": [{"id": o.id, "region": o.region, "coupon": o.coupon,
                                "lines": [{"sku": l.item.sku, "name": l.item.name,
                                           "price": l.item.price, "weight_g": l.item.weight_g,
                                           "quantity": l.quantity} for l in o.lines]}
                               for o in orders]}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
    '''),
    "shop/reports.py": d(r'''
        """Totals over many orders."""
        from collections import defaultdict

        from shop.pricing import breakdown


        def total(orders):
            return round(sum(breakdown(o)["total"] for o in orders), 2)


        def by_region(orders):
            out = defaultdict(float)
            for o in orders:
                out[o.region] = round(out[o.region] + breakdown(o)["total"], 2)
            return dict(sorted(out.items()))
    '''),
    "shop/__main__.py": d(r'''
        """python -m shop invoice ORDERS.json ID | python -m shop report ORDERS.json"""
        import sys

        from shop import invoice, reports, storage
        from shop.money import fmt


        def main(argv=None):
            argv = sys.argv[1:] if argv is None else argv
            if len(argv) == 3 and argv[0] == "invoice":
                for order in storage.load(argv[1]):
                    if order.id == argv[2]:
                        sys.stdout.write(invoice.render(order))
                        return 0
                print(f"shop: no order {argv[2]}", file=sys.stderr)
                return 1
            if len(argv) == 2 and argv[0] == "report":
                orders = storage.load(argv[1])
                for region, amount in reports.by_region(orders).items():
                    print(f"{region:<4}{fmt(amount):>12}")
                print(f"{'all':<4}{fmt(reports.total(orders)):>12}")
                return 0
            print(__doc__, file=sys.stderr)
            return 2


        if __name__ == "__main__":
            sys.exit(main())
    '''),
    "data/catalog.json": d(r'''
        {"items": [
         {"sku": "W-1", "name": "Widget", "price": 2.675, "weight_g": 300},
         {"sku": "BULK-B", "name": "Bolt", "price": 0.29, "weight_g": 20},
         {"sku": "G-1", "name": "Gizmo", "price": 14.38, "weight_g": 900}
        ]}
    '''),
    "data/orders-2026-10.json": d(r'''
        {"orders": [
         {"id": "A-1", "region": "CA", "coupon": "TENOFF", "lines": [
          {"sku": "W-1", "name": "Widget", "price": 2.675, "weight_g": 300, "quantity": 1},
          {"sku": "BULK-B", "name": "Bolt", "price": 0.29, "weight_g": 20, "quantity": 9},
          {"sku": "G-1", "name": "Gizmo", "price": 14.38, "weight_g": 900, "quantity": 1}]},
         {"id": "A-2", "region": "NY", "coupon": null, "lines": [
          {"sku": "G-1", "name": "Gizmo", "price": 14.38, "weight_g": 900, "quantity": 4}]},
         {"id": "A-3", "region": "OR", "coupon": "FIVER", "lines": [
          {"sku": "G-1", "name": "Gizmo", "price": 14.38, "weight_g": 900, "quantity": 3}]},
         {"id": "A-4", "region": "TX", "coupon": "HALF", "lines": [
          {"sku": "W-1", "name": "Widget", "price": 1.005, "weight_g": 300, "quantity": 3}]}
        ]}
    '''),
    "tests/test_pricing.py": d(r'''
        from shop.models import Item, Line, Order
        from shop.pricing import breakdown


        def order(*lines, region="OR", coupon=None):
            return Order("T", region, [Line(Item(sku, sku, price, weight), qty)
                                       for sku, price, weight, qty in lines], coupon)


        def test_one_line():
            assert breakdown(order(("W", 10.0, 100, 2))) == {
                "subtotal": 20.0, "discount": 0.0, "tax": 0.0, "shipping": 5.49, "total": 25.49}


        def test_bulk_and_coupon():
            b = breakdown(order(("BULK-X", 1.0, 0, 8), region="NY", coupon="TENOFF"))
            assert b == {"subtotal": 8.0, "discount": 2.6, "tax": 0.22, "shipping": 4.99,
                         "total": 10.61}


        def test_free_shipping():
            assert breakdown(order(("G", 25.0, 5000, 2)))["shipping"] == 0.0
    '''),
    "tests/test_storage.py": d(r'''
        from shop import storage


        def test_round_trip(tmp_path):
            orders = storage.load("data/orders-2026-10.json")
            storage.save(orders, tmp_path / "o.json")
            assert storage.load(tmp_path / "o.json") == orders


        def test_old_file():
            orders = storage.load("data/orders-2026-10.json")
            assert [o.id for o in orders] == ["A-1", "A-2", "A-3", "A-4"]
            assert orders[0].lines[1].quantity == 9
    '''),
    "tests/test_invoice.py": d(r'''
        from shop import invoice, storage


        def test_invoice_has_every_line():
            order = storage.load("data/orders-2026-10.json")[2]
            text = invoice.render(order)
            assert text.splitlines()[0] == "Order A-3 (OR)"
            assert "Gizmo" in text and text.splitlines()[-1].endswith("46.13")
    '''),
}

SHOP_REF = {
    "shop/money.py": d(r'''
        """Money helpers. Amounts are ints, in cents."""
        from decimal import ROUND_HALF_UP, Decimal


        def fmt(cents):
            """1250 -> '12.50'."""
            sign = "-" if cents < 0 else ""
            whole, rest = divmod(abs(cents), 100)
            return f"{sign}{whole}.{rest:02d}"


        def parse(text):
            """'12.50' -> 1250; a third decimal or more is rounded half up."""
            return int((Decimal(str(text)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


        def half_up(numerator, denominator):
            """numerator / denominator to the nearest int, halves away from zero."""
            return int((Decimal(numerator) / Decimal(denominator)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP))
    '''),
    "shop/models.py": d(r'''
        """What the shop sells and what customers order."""
        from dataclasses import dataclass, field


        @dataclass
        class Item:
            sku: str
            name: str
            price_cents: int
            weight_g: int = 0


        @dataclass
        class Line:
            item: Item
            quantity: int

            def total_cents(self):
                return self.item.price_cents * self.quantity


        @dataclass
        class Order:
            id: str
            region: str
            lines: list = field(default_factory=list)
            coupon: str = None

            def weight_g(self):
                return sum(line.item.weight_g * line.quantity for line in self.lines)
    '''),
    "shop/catalog.py": d(r'''
        """The catalog: a JSON file {"items": [{"sku", "name", "price_cents", "weight_g"}]};
        one written before holds "price", in dollars."""
        import json

        from shop import money
        from shop.models import Item


        def price_cents(row):
            if "price_cents" in row:
                return int(row["price_cents"])
            return money.parse(row["price"])


        def load(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return {row["sku"]: Item(row["sku"], row["name"], price_cents(row),
                                     row.get("weight_g", 0))
                    for row in data["items"]}
    '''),
    "shop/discounts.py": d(r'''
        """Discounts: bulk lines and coupons. Amounts in cents."""
        from shop.money import half_up

        # ("percent", N) takes N% off; ("fixed", C) takes C cents off, never more
        # than the amount it applies to.
        COUPONS = {"TENOFF": ("percent", 10), "HALF": ("percent", 50), "FIVER": ("fixed", 500)}


        def line_discount_cents(line):
            """BULK- items: every fourth one is free."""
            if not line.item.sku.startswith("BULK-"):
                return 0
            return line.quantity // 4 * line.item.price_cents


        def coupon_discount_cents(amount_cents, code):
            if code is None:
                return 0
            if code not in COUPONS:
                raise KeyError(f"unknown coupon {code}")
            kind, value = COUPONS[code]
            if kind == "percent":
                return half_up(amount_cents * value, 100)
            return min(value, amount_cents)
    '''),
    "shop/tax.py": d(r'''
        """Sales tax by region; rates in hundredths of a percent."""
        from shop.money import half_up

        RATES = {"CA": 725, "NY": 400, "TX": 625, "OR": 0}


        def tax_cents(amount_cents, region):
            return half_up(amount_cents * RATES[region], 10000)
    '''),
    "shop/shipping.py": d(r'''
        """Shipping: free from FREE_OVER_CENTS, else a base fee and a fee per started 500 g."""

        FREE_OVER_CENTS = 5000
        BASE_CENTS = 499
        PER_500G_CENTS = 50


        def shipping_cents(amount_cents, weight_g):
            if amount_cents >= FREE_OVER_CENTS:
                return 0
            return BASE_CENTS + PER_500G_CENTS * -(-weight_g // 500)
    '''),
    "shop/pricing.py": d(r'''
        """An order's breakdown, in cents."""
        from shop import discounts, shipping, tax


        def breakdown(order):
            subtotal = sum(line.total_cents() for line in order.lines)
            bulk = sum(discounts.line_discount_cents(line) for line in order.lines)
            after_bulk = subtotal - bulk
            coupon = discounts.coupon_discount_cents(after_bulk, order.coupon)
            taxable = after_bulk - coupon
            sales_tax = tax.tax_cents(taxable, order.region)
            ship = shipping.shipping_cents(taxable, order.weight_g())
            return {
                "subtotal_cents": subtotal,
                "discount_cents": bulk + coupon,
                "tax_cents": sales_tax,
                "shipping_cents": ship,
                "total_cents": taxable + sales_tax + ship,
            }
    '''),
    "shop/invoice.py": d(r'''
        """An order as text."""
        from shop.money import fmt
        from shop.pricing import breakdown

        ROWS = (("Subtotal", "subtotal_cents"), ("Discount", "discount_cents"),
                ("Tax", "tax_cents"), ("Shipping", "shipping_cents"), ("Total", "total_cents"))


        def render(order):
            out = [f"Order {order.id} ({order.region})"]
            for line in order.lines:
                out.append(f"  {line.quantity:>3} x {line.item.name:<20}"
                           f"{fmt(line.total_cents()):>10}")
            b = breakdown(order)
            for label, key in ROWS:
                if key == "discount_cents":
                    if b[key]:
                        out.append(f"{label:<28}{'-' + fmt(b[key]):>10}")
                    continue
                out.append(f"{label:<28}{fmt(b[key]):>10}")
            return "\n".join(out) + "\n"
    '''),
    "shop/storage.py": d(r'''
        """Orders on disk: {"orders": [{"id", "region", "coupon",
        "lines": [{"sku", "name", "price_cents", "weight_g", "quantity"}]}]}; a file
        written before holds "price", in dollars."""
        import json

        from shop.catalog import price_cents
        from shop.models import Item, Line, Order


        def load(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            orders = []
            for row in data["orders"]:
                lines = [Line(Item(l["sku"], l["name"], price_cents(l), l.get("weight_g", 0)),
                              l["quantity"]) for l in row["lines"]]
                orders.append(Order(row["id"], row["region"], lines, row.get("coupon")))
            return orders


        def save(orders, path):
            data = {"orders": [{"id": o.id, "region": o.region, "coupon": o.coupon,
                                "lines": [{"sku": l.item.sku, "name": l.item.name,
                                           "price_cents": l.item.price_cents,
                                           "weight_g": l.item.weight_g,
                                           "quantity": l.quantity} for l in o.lines]}
                               for o in orders]}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
    '''),
    "shop/reports.py": d(r'''
        """Totals over many orders, in cents."""
        from collections import defaultdict

        from shop.pricing import breakdown


        def total_cents(orders):
            return sum(breakdown(o)["total_cents"] for o in orders)


        def by_region_cents(orders):
            out = defaultdict(int)
            for o in orders:
                out[o.region] += breakdown(o)["total_cents"]
            return dict(sorted(out.items()))
    '''),
    "shop/__main__.py": d(r'''
        """python -m shop invoice ORDERS.json ID | python -m shop report ORDERS.json"""
        import sys

        from shop import invoice, reports, storage
        from shop.money import fmt


        def main(argv=None):
            argv = sys.argv[1:] if argv is None else argv
            if len(argv) == 3 and argv[0] == "invoice":
                for order in storage.load(argv[1]):
                    if order.id == argv[2]:
                        sys.stdout.write(invoice.render(order))
                        return 0
                print(f"shop: no order {argv[2]}", file=sys.stderr)
                return 1
            if len(argv) == 2 and argv[0] == "report":
                orders = storage.load(argv[1])
                for region, amount in reports.by_region_cents(orders).items():
                    print(f"{region:<4}{fmt(amount):>12}")
                print(f"{'all':<4}{fmt(reports.total_cents(orders)):>12}")
                return 0
            print(__doc__, file=sys.stderr)
            return 2


        if __name__ == "__main__":
            sys.exit(main())
    '''),
    "tests/test_pricing.py": d(r'''
        from shop.models import Item, Line, Order
        from shop.pricing import breakdown


        def order(*lines, region="OR", coupon=None):
            return Order("T", region, [Line(Item(sku, sku, price, weight), qty)
                                       for sku, price, weight, qty in lines], coupon)


        def test_one_line():
            assert breakdown(order(("W", 1000, 100, 2))) == {
                "subtotal_cents": 2000, "discount_cents": 0, "tax_cents": 0,
                "shipping_cents": 549, "total_cents": 2549}


        def test_bulk_and_coupon():
            b = breakdown(order(("BULK-X", 100, 0, 8), region="NY", coupon="TENOFF"))
            assert b == {"subtotal_cents": 800, "discount_cents": 260, "tax_cents": 22,
                         "shipping_cents": 499, "total_cents": 1061}


        def test_free_shipping():
            assert breakdown(order(("G", 2500, 5000, 2)))["shipping_cents"] == 0
    '''),
}

project("o-money", SHOP, d('''
    Do the migration MIGRATION.md describes, across the whole package, and keep
    the project's tests passing (bring them over to the new names). Run
    `python -m pytest -q` before you finish.
    '''), SHOP_REF, allow=("python -m pytest*", "python -m shop*", "python -c *"),
    hidden={"test_hidden_o.py": d(r'''
        import dataclasses
        import json
        import subprocess
        import sys
        from pathlib import Path

        from shop import catalog, discounts, money, pricing, reports, shipping, storage, tax
        from shop.models import Item, Line

        ROOT = Path(__file__).resolve().parents[1]
        OLD = str(ROOT / "data" / "orders-2026-10.json")
        DESELECT = ()


        def test_money():
            assert (money.fmt(1234), money.fmt(5), money.fmt(1200)) == ("12.34", "0.05", "12.00")
            assert (money.parse("0.29"), money.parse("2.675"), money.parse("12")) == (29, 268, 1200)
            assert type(money.parse("1.10")) is int


        def test_models():
            assert "price" not in {f.name for f in dataclasses.fields(Item)}
            total = Line(Item("W", "Widget", price_cents=268, weight_g=300), 3).total_cents()
            assert total == 804 and type(total) is int


        def test_discounts():
            assert discounts.COUPONS["FIVER"] == ("fixed", 500)
            assert discounts.line_discount_cents(Line(Item("BULK-B", "Bolt", price_cents=29), 9)) == 58
            assert discounts.line_discount_cents(Line(Item("W", "W", price_cents=29), 9)) == 0
            assert discounts.coupon_discount_cents(1005, "TENOFF") == 101
            assert discounts.coupon_discount_cents(303, "HALF") == 152
            assert discounts.coupon_discount_cents(300, "FIVER") == 300
            assert discounts.coupon_discount_cents(1000, None) == 0


        def test_tax():
            assert (tax.RATES["CA"], tax.RATES["NY"]) == (725, 400)
            assert tax.tax_cents(1000, "CA") == 73
            assert tax.tax_cents(1718, "CA") == 125
            assert tax.tax_cents(151, "TX") == 9


        def test_shipping():
            assert (shipping.FREE_OVER_CENTS, shipping.BASE_CENTS,
                    shipping.PER_500G_CENTS) == (5000, 499, 50)
            assert shipping.shipping_cents(4999, 1200) == 649
            assert shipping.shipping_cents(5000, 1200) == 0
            assert shipping.shipping_cents(100, 0) == 499


        def test_breakdown_of_the_old_orders():
            got = [pricing.breakdown(o) for o in storage.load(OLD)]
            assert got[0] == {"subtotal_cents": 1967, "discount_cents": 249, "tax_cents": 125,
                              "shipping_cents": 649, "total_cents": 2492}
            assert [b["total_cents"] for b in got] == [2492, 5982, 4613, 759]
            assert all(type(v) is int for b in got for v in b.values())


        def test_storage(tmp_path):
            orders = storage.load(OLD)
            assert [l.item.price_cents for l in orders[0].lines] == [268, 29, 1438]
            assert orders[3].lines[0].item.price_cents == 101
            out = tmp_path / "new.json"
            storage.save(orders, out)
            lines = [l for o in json.loads(out.read_text(encoding="utf-8"))["orders"]
                     for l in o["lines"]]
            assert all("price" not in l and type(l["price_cents"]) is int for l in lines)
            assert storage.load(out) == orders


        def test_catalog(tmp_path):
            old = catalog.load(ROOT / "data" / "catalog.json")
            assert {sku: item.price_cents for sku, item in old.items()} == {
                "W-1": 268, "BULK-B": 29, "G-1": 1438}
            new = tmp_path / "c.json"
            new.write_text(json.dumps({"items": [{"sku": "X", "name": "x", "price_cents": 105}]}),
                           encoding="utf-8")
            assert catalog.load(new)["X"].price_cents == 105


        def test_reports():
            orders = storage.load(OLD)
            assert reports.total_cents(orders) == 13846
            assert reports.by_region_cents(orders) == {"CA": 2492, "NY": 5982, "OR": 4613, "TX": 759}


        def test_the_old_names_are_gone():
            gone = [(discounts, "line_discount"), (discounts, "coupon_discount"), (tax, "tax"),
                    (shipping, "shipping"), (shipping, "FREE_OVER"), (reports, "total"),
                    (reports, "by_region")]
            assert [f"{m.__name__}.{n}" for m, n in gone if hasattr(m, n)] == []
            assert not hasattr(Line, "total")


        def shop(*args):
            return subprocess.run([sys.executable, "-m", "shop", *args], cwd=ROOT,
                                  capture_output=True, text=True)


        def test_the_command_line():
            assert shop("invoice", OLD, "A-1").stdout.splitlines() == [
                "Order A-1 (CA)",
                f"  {1:>3} x {'Widget':<20}{'2.68':>10}",
                f"  {9:>3} x {'Bolt':<20}{'2.61':>10}",
                f"  {1:>3} x {'Gizmo':<20}{'14.38':>10}",
                f"{'Subtotal':<28}{'19.67':>10}", f"{'Discount':<28}{'-2.49':>10}",
                f"{'Tax':<28}{'1.25':>10}", f"{'Shipping':<28}{'6.49':>10}",
                f"{'Total':<28}{'24.92':>10}"]
            assert shop("report", OLD).stdout.splitlines() == [
                f"{'CA':<4}{'24.92':>12}", f"{'NY':<4}{'59.82':>12}", f"{'OR':<4}{'46.13':>12}",
                f"{'TX':<4}{'7.59':>12}", f"{'all':<4}{'138.46':>12}"]
    ''') + "\n\n" + OWN_SUITE})


# --- P: thirteen rules; the visible tests pass early --------------------------------------
# The spec shows the fraction characters themselves; the sources here stay ASCII.
QTY_SPEC = d('''
    # qty: recipe quantities

    `qty` reads the quantities written in recipes, writes them back, converts
    and scales them. Everything is exact: amounts are `fractions.Fraction`, and
    no float takes part.

        from qty import Quantity, QtyError, parse, fmt, convert, scale

    `Quantity(low, high, unit)` is a frozen dataclass: `low == high` except for
    a range; `unit` is a canonical unit name (below) or None. Every error is a
    `QtyError`, a kind of ValueError.

    ## parse(text) -> Quantity

    The text is stripped first.

    1. **Numbers.** A whole number (`2`), a decimal (`1.5`, `.5`), a fraction
       (`3/4`) or a mixed number (`1 1/2`, any number of spaces between). `0.1`
       is exactly `Fraction(1, 10)`.
    2. **Fraction characters.** \u00bd \u2153 \u2154 \u00bc \u00be \u215b are 1/2, 1/3, 2/3, 1/4, 3/4 and
       1/8, alone (`\u00be`) or after a whole number, with or without a space
       (`1\u00bd`, `1 \u00bd`).
    3. **Ranges.** Two numbers joined by `-` or `\u2013` (an en dash; spaces
       allowed around either) or by ` to `: `2-3`, `2 \u2013 3`, `1 1/2 to 2`. The
       second must be larger than the first.
    4. **Units** follow the amount, with or without spaces between (`2kg`,
       `2 kg`), and may end in a `.` (`tbsp.`). The canonical units and what
       else names them:

       | unit | also |
       |---|---|
       | `g` | gram, grams |
       | `kg` | kilogram, kilograms |
       | `ml` | milliliter, milliliters, millilitre, millilitres |
       | `l` | liter, liters, litre, litres |
       | `tsp` | teaspoon, teaspoons, `t` |
       | `tbsp` | tbs, tablespoon, tablespoons, `T` |
       | `cup` | cups, c |

       Names match in any case (`ML`, `Cups`), except `t` and `T`, which match
       only as written: `t` is a teaspoon and `T` a tablespoon. Any other word
       is an error. No unit at all: `unit` is None.
    5. **Words.** `a` or `an` is 1, and `half a` or `half an` is 1/2, in any
       case, followed by a unit: `a cup`, `Half a tsp`.
    6. **Errors.** An empty text, an amount of zero, and anything this list
       does not describe.

    ## fmt(q) -> str

    7. Amounts in `g`, `kg`, `ml` and `l` are written as decimals rounded half
       up to two places, trailing zeros and a trailing point dropped: `250 g`,
       `1.5 kg`, `0.33 l`, `2.01 kg` (for 2.005).
    8. Other amounts, and amounts with no unit, are written as the nearest
       whole number plus a multiple of an eighth or of a third -- using only
       1/8, 1/4, 3/8, 1/2, 5/8, 3/4, 7/8, 1/3 and 2/3; of two equally near, the
       larger. A whole part and a fraction are separated by one space: `2`,
       `3/4`, `1 1/2`. An amount nearest to 0 is written `1/8`.
    9. The unit follows after one space, by its canonical name, except that
       `cup` is written `cups` when the amount as written is more than 1
       (`1 cup`, `1 1/8 cups`). No other unit takes a plural.
    10. A range is `low-high` and then the unit; the high end decides `cups`:
        `2-3 cups`, `1/2-1 cup`.
    11. No unit: the amount alone (`1 1/2`).

    ## convert(q, unit) -> Quantity

    12. `unit` is a canonical name or anything `parse` takes for one. Mass:
        1 kg = 1000 g. Volume: 1 l = 1000 ml, 1 tsp = 5 ml, 1 tbsp = 3 tsp,
        1 cup = 16 tbsp. The result is exact, in the canonical unit. Mass to
        volume, volume to mass, and a quantity with no unit are errors.

    ## scale(q, factor) -> Quantity

    13. Both ends multiplied by `factor`, the unit kept. `factor` is an `int`,
        a `Fraction`, or a string that `parse` reads as one number (`"1 1/2"`,
        `"\u00bd"`). A float is an error (it is not exact), and so is a factor of
        zero or less.
''')

QTY = {
    "pyproject.toml": PYPROJECT,
    "SPEC.md": QTY_SPEC,
    "qty/__init__.py": d(r'''
        """Recipe quantities: see SPEC.md."""
        from dataclasses import dataclass
        from fractions import Fraction


        class QtyError(ValueError):
            pass


        @dataclass(frozen=True)
        class Quantity:
            low: Fraction
            high: Fraction
            unit: str = None


        UNITS = {"g": "g", "kg": "kg", "ml": "ml", "l": "l", "tsp": "tsp", "tbsp": "tbsp",
                 "cup": "cup", "cups": "cup"}


        def _number(text):
            parts = text.split()
            if len(parts) == 2:
                return Fraction(parts[0]) + Fraction(parts[1])
            return Fraction(text)


        def parse(text):
            words = text.strip().split()
            if words and words[-1].lower() in UNITS:
                unit = UNITS[words[-1].lower()]
                words = words[:-1]
            else:
                unit = None
            try:
                n = _number(" ".join(words))
            except (ValueError, ZeroDivisionError):
                raise QtyError(f"not a quantity: {text!r}") from None
            return Quantity(n, n, unit)


        def fmt(q):
            n = q.low
            whole, rest = divmod(n, 1)
            if q.unit in ("g", "kg", "ml", "l"):
                text = f"{float(n):g}"
            elif rest == 0:
                text = str(whole)
            elif whole == 0:
                text = str(rest)
            else:
                text = f"{whole} {rest}"
            return f"{text} {q.unit}" if q.unit else text


        def convert(q, unit):
            raise NotImplementedError


        def scale(q, factor):
            return Quantity(q.low * factor, q.high * factor, q.unit)
    '''),
    "tests/test_qty.py": d(r'''
        from fractions import Fraction

        from qty import Quantity, fmt, parse


        def test_parse_simple():
            assert parse("2 cups") == Quantity(Fraction(2), Fraction(2), "cup")
            assert parse("1 1/2 tsp") == Quantity(Fraction(3, 2), Fraction(3, 2), "tsp")


        def test_fmt_simple():
            assert fmt(parse("3/4 tsp")) == "3/4 tsp"
            assert fmt(parse("250 g")) == "250 g"
    '''),
}

QTY_REF = {
    "qty/__init__.py": d(r'''
        """Recipe quantities: see SPEC.md."""
        import re
        from dataclasses import dataclass
        from decimal import ROUND_HALF_UP, Decimal
        from fractions import Fraction


        class QtyError(ValueError):
            pass


        @dataclass(frozen=True)
        class Quantity:
            low: Fraction
            high: Fraction
            unit: str = None


        VULGAR = {"\u00bd": Fraction(1, 2), "\u2153": Fraction(1, 3), "\u2154": Fraction(2, 3),
                  "\u00bc": Fraction(1, 4), "\u00be": Fraction(3, 4), "\u215b": Fraction(1, 8)}
        NAMES = {
            "g": ("g", "gram", "grams"),
            "kg": ("kg", "kilogram", "kilograms"),
            "ml": ("ml", "milliliter", "milliliters", "millilitre", "millilitres"),
            "l": ("l", "liter", "liters", "litre", "litres"),
            "tsp": ("tsp", "teaspoon", "teaspoons"),
            "tbsp": ("tbsp", "tbs", "tablespoon", "tablespoons"),
            "cup": ("cup", "cups", "c"),
        }
        UNITS = {name: unit for unit, names in NAMES.items() for name in names}
        METRIC = ("g", "kg", "ml", "l")
        SIZE = {"g": (1, "mass"), "kg": (1000, "mass"), "ml": (1, "volume"),
                "l": (1000, "volume"), "tsp": (5, "volume"), "tbsp": (15, "volume"),
                "cup": (240, "volume")}
        STEPS = sorted({Fraction(k, 8) for k in range(9)} | {Fraction(k, 3) for k in range(4)})


        def unit_named(word):
            word = word[:-1] if word.endswith(".") else word
            if word in ("t", "T"):
                return "tsp" if word == "t" else "tbsp"
            unit = UNITS.get(word.lower())
            if unit is None:
                raise QtyError(f"no unit {word!r}")
            return unit


        def number(text):
            text = text.strip()
            m = re.fullmatch(r"(\d+)?\s*([" + "".join(VULGAR) + r"])", text)
            if m:
                return int(m.group(1) or 0) + VULGAR[m.group(2)]
            m = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", text)
            if m and int(m.group(3)):
                return int(m.group(1)) + Fraction(int(m.group(2)), int(m.group(3)))
            m = re.fullmatch(r"(\d+)/(\d+)", text)
            if m and int(m.group(2)):
                return Fraction(int(m.group(1)), int(m.group(2)))
            if re.fullmatch(r"\d+(\.\d+)?|\.\d+", text):
                return Fraction(text)
            raise QtyError(f"not a number: {text!r}")


        def parse(text):
            text = text.strip()
            m = re.fullmatch(r"(half\s+an?|an?)\s+(\S+)", text, re.IGNORECASE)
            if m:
                n = Fraction(1, 2) if m.group(1).lower().startswith("half") else Fraction(1)
                return Quantity(n, n, unit_named(m.group(2)))
            m = re.fullmatch(r"(.*?)\s*([A-Za-z]+\.?)?", text)
            amount, word = m.group(1), m.group(2)
            unit = unit_named(word) if word else None
            parts = re.split(r"\s*[-\u2013]\s*|\s+to\s+", amount)
            if len(parts) > 2:
                raise QtyError(f"not a quantity: {text!r}")
            low, high = number(parts[0]), number(parts[-1])
            if low <= 0 or (len(parts) == 2 and high <= low):
                raise QtyError(f"not a quantity: {text!r}")
            return Quantity(low, high, unit)


        def _metric(n):
            d = (Decimal(n.numerator) / Decimal(n.denominator)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            s = format(d, "f")
            return s.rstrip("0").rstrip(".") if "." in s else s


        def _nearest(n):
            whole = n.numerator // n.denominator
            best = None
            for step in STEPS:
                v = whole + step
                if best is None or abs(n - v) < abs(n - best) or (
                        abs(n - v) == abs(n - best) and v > best):
                    best = v
            return best or Fraction(1, 8)


        def _kitchen(v):
            whole, rest = divmod(v, 1)
            if not rest:
                return str(whole)
            return f"{whole} {rest}" if whole else str(rest)


        def fmt(q):
            if q.unit in METRIC:
                lo, hi = _metric(q.low), _metric(q.high)
                big = False
            else:
                low, high = _nearest(q.low), _nearest(q.high)
                lo, hi = _kitchen(low), _kitchen(high)
                big = high > 1
            text = lo if q.low == q.high else f"{lo}-{hi}"
            if q.unit is None:
                return text
            return f"{text} {'cups' if q.unit == 'cup' and big else q.unit}"


        def convert(q, unit):
            target = unit_named(unit)
            if q.unit is None or SIZE[q.unit][1] != SIZE[target][1]:
                raise QtyError(f"cannot convert {q.unit} to {target}")
            f = Fraction(SIZE[q.unit][0], SIZE[target][0])
            return Quantity(q.low * f, q.high * f, target)


        def scale(q, factor):
            if isinstance(factor, str):
                factor = number(factor)
            elif isinstance(factor, bool) or not isinstance(factor, (int, Fraction)):
                raise QtyError(f"not an exact factor: {factor!r}")
            if factor <= 0:
                raise QtyError(f"not a factor: {factor!r}")
            return Quantity(q.low * factor, q.high * factor, q.unit)
    '''),
    "tests/test_qty.py": d(r'''
        from fractions import Fraction

        import pytest

        from qty import Quantity, QtyError, convert, fmt, parse, scale


        def test_parse_simple():
            assert parse("2 cups") == Quantity(Fraction(2), Fraction(2), "cup")
            assert parse("1 1/2 tsp") == Quantity(Fraction(3, 2), Fraction(3, 2), "tsp")


        def test_fmt_simple():
            assert fmt(parse("3/4 tsp")) == "3/4 tsp"
            assert fmt(parse("250 g")) == "250 g"


        def test_the_rest():
            assert fmt(parse("2-3 c")) == "2-3 cups"
            assert convert(parse("1 cup"), "ml").low == 240
            with pytest.raises(QtyError):
                scale(parse("1 cup"), 1.5)
    '''),
}

project("p-spec", QTY, d('''
    Finish qty: it should do everything SPEC.md says, with tests. Run
    `python -m pytest -q` before you finish.
    '''), QTY_REF, allow=("python -m pytest*", "python -c *"),
    hidden={"test_hidden_p.py": d(r'''
        import subprocess
        import sys
        from fractions import Fraction as F
        from pathlib import Path

        import pytest

        from qty import Quantity, QtyError, convert, fmt, parse, scale

        ROOT = Path(__file__).resolve().parents[1]
        DESELECT = ()


        def q(low, high=None, unit=None):
            return Quantity(F(low), F(low if high is None else high), unit)


        def test_1_numbers():
            assert parse("2") == q(2) and isinstance(parse("1.5").low, F)
            assert parse("1.5") == q("3/2") and parse(".5") == q("1/2")
            assert parse("0.1").low == F(1, 10)
            assert parse("3/4") == q("3/4") and parse("1  1/2") == q("3/2")


        def test_2_fraction_characters():
            assert parse("\u00be") == q("3/4")
            assert parse("1\u00bd tsp") == q("3/2", unit="tsp")
            assert parse("2 \u2153 cups") == q("7/3", unit="cup")
            assert parse("\u215b tsp") == q("1/8", unit="tsp")
            assert parse("\u2154 cup") == q("2/3", unit="cup")
            assert parse("\u00bc cup") == q("1/4", unit="cup")


        def test_3_ranges():
            assert parse("2-3 cups") == q(2, 3, "cup")
            assert parse("2 \u2013 3") == q(2, 3)
            assert parse("1 1/2 to 2 tbsp") == q("3/2", 2, "tbsp")
            assert parse("1/2 - 1 cup") == q("1/2", 1, "cup")
            for bad in ("3-2 cups", "2-2", "2 to"):
                with pytest.raises(QtyError):
                    parse(bad)


        def test_4_units():
            assert parse("2kg") == q(2, unit="kg") and parse("2 Kilograms") == q(2, unit="kg")
            assert parse("250 mL") == q(250, unit="ml") and parse("1 Litre") == q(1, unit="l")
            assert parse("1 tbsp.") == q(1, unit="tbsp") and parse("1 Tbs") == q(1, unit="tbsp")
            assert parse("1 T") == q(1, unit="tbsp") and parse("1 t") == q(1, unit="tsp")
            assert parse("1 TSP") == q(1, unit="tsp") and parse("3 C") == q(3, unit="cup")
            assert parse("100 grams") == q(100, unit="g")
            with pytest.raises(QtyError):
                parse("2 handfuls")


        def test_5_words():
            assert parse("a cup") == q(1, unit="cup")
            assert parse("Half a tsp") == q("1/2", unit="tsp")
            assert parse("HALF A CUP") == q("1/2", unit="cup")
            with pytest.raises(QtyError):
                parse("an egg")


        def test_6_errors():
            for bad in ("", "   ", "0 g", "-1 cup", "cup", "1/0 cup", "two cups", "1 1/2/3"):
                with pytest.raises(QtyError):
                    parse(bad)


        def test_7_metric():
            assert fmt(parse("250 g")) == "250 g" and fmt(parse("1.5 kg")) == "1.5 kg"
            assert fmt(parse("2.005 kg")) == "2.01 kg"
            assert fmt(q("1/3", unit="l")) == "0.33 l"
            assert fmt(parse("250.50 ml")) == "250.5 ml" and fmt(parse("1000 g")) == "1000 g"


        def test_8_kitchen_fractions():
            assert fmt(parse("3/4 tsp")) == "3/4 tsp"
            assert fmt(q("0.3", unit="tsp")) == "1/3 tsp"
            assert fmt(q("7/24", unit="tsp")) == "1/3 tsp"   # as near 1/4 as 1/3
            assert fmt(q("1.97", unit="tbsp")) == "2 tbsp"
            assert fmt(q("0.05", unit="tsp")) == "1/8 tsp"
            assert fmt(q("2.5")) == "2 1/2"
            assert fmt(q("5/12", unit="tbsp")) == "3/8 tbsp"


        def test_9_cups():
            assert fmt(parse("1 cup")) == "1 cup" and fmt(parse("2 cups")) == "2 cups"
            assert fmt(parse("1 1/8 c")) == "1 1/8 cups"
            assert fmt(parse("3/4 cups")) == "3/4 cup"
            assert fmt(q("26/25", unit="cup")) == "1 cup"     # written as 1
            assert fmt(parse("2 tbsp")) == "2 tbsp"


        def test_10_11_ranges_and_no_unit():
            assert fmt(parse("2-3 cups")) == "2-3 cups"
            assert fmt(parse("1/2-1 cup")) == "1/2-1 cup"
            assert fmt(parse("1-1.5 kg")) == "1-1.5 kg"
            assert fmt(parse("1 1/2")) == "1 1/2"


        def test_12_convert():
            assert convert(parse("1 kg"), "g") == q(1000, unit="g")
            assert convert(parse("1 cup"), "ml") == q(240, unit="ml")
            assert convert(parse("1 tbsp"), "tsp") == q(3, unit="tsp")
            assert convert(parse("2-3 tsp"), "ml") == q(10, 15, "ml")
            assert convert(parse("100 ml"), "cup") == q("5/12", unit="cup")
            assert fmt(convert(parse("1 l"), "cups")) == "4 1/8 cups"
            assert convert(parse("500 g"), "Kilograms") == q("1/2", unit="kg")
            for bad in ((parse("1 kg"), "ml"), (parse("2"), "g"), (parse("1 cup"), "pint")):
                with pytest.raises(QtyError):
                    convert(*bad)


        def test_13_scale():
            assert scale(parse("1 1/2 cups"), 2) == q(3, unit="cup")
            assert scale(parse("2-3 tbsp"), F(1, 2)) == q(1, "3/2", "tbsp")
            assert scale(parse("1 cup"), "1 1/2") == q("3/2", unit="cup")
            assert scale(parse("1 cup"), "\u00bd") == q("1/2", unit="cup")
            for bad in (1.5, 0, -2, "0", "abc"):
                with pytest.raises(QtyError):
                    scale(parse("1 cup"), bad)
    ''') + "\n\n" + OWN_SUITE})


# --- Q: the root cause, next to someone else's failure -----------------------------------
TASKR = {
    "pyproject.toml": PYPROJECT,
    "README.md": d(r'''
        # taskr

        A small task runner. `taskr.json` names the tasks:

            {"tasks": {"build": {"cmd": ["python", "build.py"], "inputs": ["src/*.txt"],
                                 "outputs": ["out/all.txt"], "deps": ["gen"]}}}

        `python -m taskr [-C DIR] run [--force] [TASK ...]` runs the tasks named (all
        when none are), each after the tasks it depends on, and skips each one that
        is up to date. `python -m taskr status` shows which are.

        ## When a task runs again

        A task is up to date, and skipped, when none of these changed since its last
        successful run:

        - its command: the list of arguments, as written in taskr.json;
        - which files its inputs match: their paths;
        - the content of each of those files;

        and every one of its outputs exists. `--force` runs it anyway. What decided
        each task's last run is kept in `.taskr/state.json`.

        ## Output

        `NAME: up to date` or `NAME: running` for each task, then `N task(s) ran`.
        An error is `taskr: MESSAGE` on stderr and exit status 1.
    '''),
    "taskr/__init__.py": '__version__ = "0.3.0"\n',
    "taskr/errors.py": d(r'''
        class TaskrError(Exception):
            """A problem with taskr.json or with a task; printed as `taskr: MESSAGE`."""
    '''),
    "taskr/config.py": d(r'''
        """taskr.json: {"tasks": {NAME: {"cmd": [ARG, ...], "inputs": [GLOB, ...],
        "outputs": [PATH, ...], "deps": [NAME, ...]}}}"""
        import json
        from dataclasses import dataclass
        from pathlib import Path

        from taskr.errors import TaskrError


        @dataclass(frozen=True)
        class Task:
            name: str
            cmd: tuple
            inputs: tuple = ()
            outputs: tuple = ()
            deps: tuple = ()


        def load(root):
            path = Path(root) / "taskr.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise TaskrError(f"no taskr.json in {root}") from None
            except json.JSONDecodeError as exc:
                raise TaskrError(f"taskr.json: {exc}") from None
            tasks = {}
            for name, spec in data.get("tasks", {}).items():
                cmd = spec.get("cmd")
                if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
                    raise TaskrError(f"task {name}: cmd must be a non-empty list of strings")
                tasks[name] = Task(name, tuple(cmd), tuple(spec.get("inputs", ())),
                                   tuple(spec.get("outputs", ())), tuple(spec.get("deps", ())))
            for task in tasks.values():
                for dep in task.deps:
                    if dep not in tasks:
                        raise TaskrError(f"task {task.name}: unknown dependency {dep}")
            return tasks
    '''),
    "taskr/graph.py": d(r'''
        """The order tasks run in."""
        from taskr.errors import TaskrError


        def order(tasks, targets=None):
            """The tasks needed for targets (all when None), each after its deps; ties by name."""
            done, out = set(), []

            def visit(name, path):
                if name in done:
                    return
                if name in path:
                    raise TaskrError("dependency cycle: " + " -> ".join([*path, name]))
                for dep in sorted(tasks[name].deps):
                    visit(dep, [*path, name])
                done.add(name)
                out.append(tasks[name])

            for name in sorted(targets or tasks):
                if name not in tasks:
                    raise TaskrError(f"no task {name}")
                visit(name, [])
            return out
    '''),
    "taskr/paths.py": d(r'''
        """Input globs."""
        from pathlib import Path


        def expand(root, patterns):
            """The files the patterns match under root: relative, with / separators,
            sorted, each once."""
            root = Path(root)
            found = set()
            for pattern in patterns:
                for p in root.glob(pattern):
                    if p.is_file():
                        found.add(p.relative_to(root).as_posix())
            return sorted(found)
    '''),
    "taskr/fingerprint.py": d(r'''
        """A task's fingerprint: what decides whether it must run again."""
        import hashlib
        from pathlib import Path


        def fingerprint(root, task, files):
            """A hex digest of the task's command and of its input files."""
            h = hashlib.sha256()
            h.update(" ".join(task.cmd).encode("utf-8"))
            for name in files:
                h.update((Path(root) / name).read_bytes())
            return h.hexdigest()
    '''),
    "taskr/state.py": d(r'''
        """The last successful runs: .taskr/state.json, {"version": 1, "tasks": {NAME: FINGERPRINT}}."""
        import json
        from pathlib import Path

        VERSION = 1


        def load(root):
            path = Path(root) / ".taskr" / "state.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                return {}
            if data.get("version") != VERSION:
                return {}
            return dict(data.get("tasks", {}))


        def save(root, fingerprints):
            path = Path(root) / ".taskr" / "state.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"version": VERSION, "tasks": fingerprints}, indent=1,
                                       sort_keys=True), encoding="utf-8")
    '''),
    "taskr/env.py": d(r'''
        """The environment a task's command runs in."""
        import os


        def for_task(root, task):
            env = dict(os.environ)
            env["TASKR_ROOT"] = str(root)
            env["TASKR_TASK"] = task.name
            return env
    '''),
    "taskr/log.py": d(r'''
        """What taskr prints."""
        import sys


        def say(message):
            print(message, flush=True)


        def warn(message):
            print(f"taskr: {message}", file=sys.stderr, flush=True)
    '''),
    "taskr/runner.py": d(r'''
        """Running tasks."""
        import subprocess
        from pathlib import Path

        from taskr import env, log, paths, state
        from taskr.errors import TaskrError
        from taskr.fingerprint import fingerprint


        def run(root, tasks, force=False):
            """Run each task, in the order given, unless it is up to date; how many ran."""
            root = Path(root)
            known = state.load(root)
            ran = 0
            for task in tasks:
                files = paths.expand(root, task.inputs)
                fp = fingerprint(root, task, files)
                outputs_there = all((root / out).exists() for out in task.outputs)
                if not force and known.get(task.name) == fp and outputs_there:
                    log.say(f"{task.name}: up to date")
                    continue
                log.say(f"{task.name}: running")
                done = subprocess.run(list(task.cmd), cwd=root, env=env.for_task(root, task))
                if done.returncode != 0:
                    known.pop(task.name, None)
                    state.save(root, known)
                    raise TaskrError(f"{task.name} failed with status {done.returncode}")
                missing = [out for out in task.outputs if not (root / out).exists()]
                if missing:
                    raise TaskrError(f"{task.name} did not write {', '.join(missing)}")
                known[task.name] = fp
                state.save(root, known)
                ran += 1
            return ran
    '''),
    "taskr/report.py": d(r'''
        """`taskr status`: a row per task, its name, whether it is fresh, how many inputs."""
        from taskr import paths, state
        from taskr.fingerprint import fingerprint


        def rows(root, tasks):
            known = state.load(root)
            out = []
            for task in tasks:
                files = paths.expand(root, task.inputs)
                fresh = known.get(task.name) == fingerprint(root, task, files)
                out.append((task.name, "fresh" if fresh else "stale", len(files)))
            return out


        def table(rows):
            width = max(len(name) for name, _, _ in rows)
            lines = [f"{'task':<{width}}  state  inputs"]
            for name, st, n in rows:
                lines.append(f"{name:<{width}}  {st:<5}  {n:>6}")
            return "\n".join(lines)
    '''),
    "taskr/__main__.py": d(r'''
        """python -m taskr [-C DIR] run [--force] [TASK ...] | python -m taskr [-C DIR] status"""
        import argparse
        import sys
        from pathlib import Path

        from taskr import config, graph, log, report, runner
        from taskr.errors import TaskrError


        def main(argv=None):
            p = argparse.ArgumentParser(prog="taskr")
            p.add_argument("-C", dest="root", default=".")
            sub = p.add_subparsers(dest="command", required=True)
            r = sub.add_parser("run")
            r.add_argument("--force", action="store_true")
            r.add_argument("targets", nargs="*")
            sub.add_parser("status")
            a = p.parse_args(argv)
            root = Path(a.root)
            try:
                tasks = config.load(root)
                if a.command == "run":
                    n = runner.run(root, graph.order(tasks, a.targets or None), force=a.force)
                    log.say(f"{n} task(s) ran")
                else:
                    log.say(report.table(report.rows(root, graph.order(tasks))))
            except TaskrError as exc:
                log.warn(str(exc))
                return 1
            return 0


        if __name__ == "__main__":
            sys.exit(main())
    '''),
    "tests/test_config.py": d(r'''
        import json

        import pytest

        from taskr import config
        from taskr.errors import TaskrError


        def write(tmp_path, data):
            (tmp_path / "taskr.json").write_text(json.dumps(data), encoding="utf-8")


        def test_load(tmp_path):
            write(tmp_path, {"tasks": {"a": {"cmd": ["echo", "hi"], "inputs": ["*.txt"]}}})
            assert config.load(tmp_path)["a"] == config.Task("a", ("echo", "hi"), ("*.txt",))


        def test_a_cmd_must_be_a_list(tmp_path):
            write(tmp_path, {"tasks": {"a": {"cmd": "echo hi"}}})
            with pytest.raises(TaskrError, match="cmd"):
                config.load(tmp_path)


        def test_an_unknown_dependency(tmp_path):
            write(tmp_path, {"tasks": {"a": {"cmd": ["x"], "deps": ["b"]}}})
            with pytest.raises(TaskrError, match="unknown dependency"):
                config.load(tmp_path)
    '''),
    "tests/test_graph.py": d(r'''
        import pytest

        from taskr.config import Task
        from taskr.errors import TaskrError
        from taskr.graph import order


        def tasks(**deps):
            return {name: Task(name, ("true",), deps=tuple(d)) for name, d in deps.items()}


        def test_deps_first():
            got = order(tasks(app=["lib", "gen"], lib=["gen"], gen=[]))
            assert [t.name for t in got] == ["gen", "lib", "app"]


        def test_only_what_a_target_needs():
            got = order(tasks(app=["lib"], lib=[], docs=[]), ["app"])
            assert [t.name for t in got] == ["lib", "app"]


        def test_a_cycle():
            with pytest.raises(TaskrError, match="cycle"):
                order(tasks(a=["b"], b=["a"]))
    '''),
    "tests/test_paths.py": d(r'''
        from taskr.paths import expand


        def test_expand(tmp_path):
            (tmp_path / "src" / "sub").mkdir(parents=True)
            for name in ("src/b.txt", "src/a.txt", "src/sub/c.txt", "src/x.md"):
                (tmp_path / name).write_text("", encoding="utf-8")
            assert expand(tmp_path, ["src/*.txt", "src/a.txt", "src/**/*.txt"]) == [
                "src/a.txt", "src/b.txt", "src/sub/c.txt"]
    '''),
    "tests/test_runner.py": d(r'''
        import json
        import sys

        from taskr import config, graph, runner

        CODE = ("import pathlib; "
                "pathlib.Path('out.txt').write_text(pathlib.Path('in.txt').read_text())")


        def setup(tmp_path):
            (tmp_path / "in.txt").write_text("hello", encoding="utf-8")
            (tmp_path / "taskr.json").write_text(json.dumps({"tasks": {"copy": {
                "cmd": [sys.executable, "-c", CODE], "inputs": ["in.txt"],
                "outputs": ["out.txt"]}}}), encoding="utf-8")
            return graph.order(config.load(tmp_path))


        def test_runs_then_skips(tmp_path, capsys):
            tasks = setup(tmp_path)
            assert runner.run(tmp_path, tasks) == 1
            assert runner.run(tmp_path, tasks) == 0
            assert capsys.readouterr().out.splitlines() == ["copy: running", "copy: up to date"]


        def test_a_changed_input_runs_again(tmp_path):
            tasks = setup(tmp_path)
            runner.run(tmp_path, tasks)
            (tmp_path / "in.txt").write_text("bye", encoding="utf-8")
            assert runner.run(tmp_path, tasks) == 1


        def test_a_missing_output_runs_again(tmp_path):
            tasks = setup(tmp_path)
            runner.run(tmp_path, tasks)
            (tmp_path / "out.txt").unlink()
            assert runner.run(tmp_path, tasks) == 1
    '''),
    "tests/test_report.py": d(r'''
        from taskr.report import table


        def test_rows_line_up():
            # Known failure, issue #41: with every name shorter than the header the
            # columns do not line up. Sam has the fix in review; leave this test and
            # report.py alone.
            lines = table([("a", "fresh", 1), ("bb", "stale", 12)]).splitlines()
            assert len({len(line) for line in lines}) == 1
    '''),
}

TASKR_REF = {
    "taskr/fingerprint.py": d(r'''
        """A task's fingerprint: what decides whether it must run again."""
        import hashlib
        import json
        from pathlib import Path


        def fingerprint(root, task, files):
            """A hex digest of the task's command, as a list of arguments, and of each
            input file's path and content, kept apart so that no two differ alike."""
            inputs = [[name, hashlib.sha256((Path(root) / name).read_bytes()).hexdigest()]
                      for name in files]
            record = json.dumps({"cmd": list(task.cmd), "inputs": inputs})
            return hashlib.sha256(record.encode("utf-8")).hexdigest()
    '''),
    "tests/test_fingerprint.py": d(r'''
        from taskr.config import Task
        from taskr.fingerprint import fingerprint


        def test_content_moved_between_inputs(tmp_path):
            (tmp_path / "a").write_text("1\n2\n", encoding="utf-8")
            (tmp_path / "b").write_text("3\n", encoding="utf-8")
            task = Task("t", ("x",))
            before = fingerprint(tmp_path, task, ["a", "b"])
            (tmp_path / "a").write_text("1\n", encoding="utf-8")
            (tmp_path / "b").write_text("2\n3\n", encoding="utf-8")
            assert fingerprint(tmp_path, task, ["a", "b"]) != before
    '''),
}

ORIGINAL_TESTS = {rel: sha(text) for rel, text in TASKR.items() if rel.startswith("tests/")}

project("q-rootcause", TASKR, d('''
    A user reports: "I moved the last line of src/a.txt to the top of src/b.txt --
    both are inputs of my build task -- and `taskr run` said `build: up to date`,
    so out/all.txt was stale." Find the cause and fix it, with a regression test.
    The suite has one known failure, in tests/test_report.py, which is someone
    else's to fix.
    '''), TASKR_REF, allow=("python -m pytest*", "python -m taskr*", "python -c *"),
    hidden={"test_hidden_q.py": d(r'''
        import hashlib
        import json
        import subprocess
        import sys
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        DESELECT = ("--deselect", "tests/test_report.py::test_rows_line_up")
        ORIGINAL_TESTS = %r
        REPORT = %r
        CONCAT = ("import pathlib\n"
                  "src = sorted(pathlib.Path('src').glob('*.txt'))\n"
                  "pathlib.Path('out').mkdir(exist_ok=True)\n"
                  "pathlib.Path('out/all.txt').write_text(''.join(p.read_text() for p in src))\n")


        def project(tmp_path, *extra):
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "taskr.json").write_text(json.dumps({"tasks": {"build": {
                "cmd": [sys.executable, "-c", CONCAT, *extra], "inputs": ["src/*.txt"],
                "outputs": ["out/all.txt"]}}}), encoding="utf-8")


        def write(tmp_path, files):
            for name, text in files.items():
                (tmp_path / "src" / name).write_text(text, encoding="utf-8")


        def taskr(tmp_path):
            r = subprocess.run([sys.executable, "-m", "taskr", "-C", str(tmp_path), "run"],
                               cwd=ROOT, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            return r.stdout


        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()


        def test_nothing_changed_is_up_to_date(tmp_path):
            project(tmp_path)
            write(tmp_path, {"a.txt": "1\n2\n", "b.txt": "3\n"})
            assert "build: running" in taskr(tmp_path)
            assert "build: up to date" in taskr(tmp_path)


        def test_the_report(tmp_path):
            project(tmp_path)
            write(tmp_path, {"a.txt": "1\n2\n", "b.txt": "3\n"})
            taskr(tmp_path)
            write(tmp_path, {"a.txt": "1\n", "b.txt": "2\n3\n"})
            assert "build: running" in taskr(tmp_path)
            assert "build: up to date" in taskr(tmp_path)


        def test_a_renamed_input(tmp_path):
            project(tmp_path)
            write(tmp_path, {"a.txt": "1\n", "b.txt": "2\n"})
            taskr(tmp_path)
            (tmp_path / "src" / "a.txt").rename(tmp_path / "src" / "a2.txt")
            assert "build: running" in taskr(tmp_path)


        def test_a_new_empty_input(tmp_path):
            project(tmp_path)
            write(tmp_path, {"a.txt": "1\n"})
            taskr(tmp_path)
            write(tmp_path, {"b.txt": ""})
            assert "build: running" in taskr(tmp_path)


        def test_the_command_is_its_list_of_arguments(tmp_path):
            project(tmp_path, "x y")
            write(tmp_path, {"a.txt": "1\n"})
            taskr(tmp_path)
            project(tmp_path, "x", "y")
            assert "build: running" in taskr(tmp_path)


        def test_the_known_failure_is_left_alone():
            assert digest(ROOT / "taskr" / "report.py") == REPORT
            assert digest(ROOT / "tests" / "test_report.py") == ORIGINAL_TESTS["tests/test_report.py"]


        def test_a_regression_test_was_added():
            now = {p.relative_to(ROOT).as_posix(): digest(p)
                   for p in (ROOT / "tests").rglob("test_*.py") if "test_hidden" not in p.name}
            assert any(ORIGINAL_TESTS.get(rel) != h for rel, h in now.items())
    ''') % (ORIGINAL_TESTS, sha(TASKR["taskr/report.py"])) + "\n\n" + OWN_SUITE})


# --- R: seven steps against a format document, compacting on the way ---------------------
LOGQ = {
    "pyproject.toml": PYPROJECT,
    "README.md": d(r'''
        # logq

        A library and a command to query log files; docs/FORMAT.md describes the
        records. Build `logq/` in seven steps, each with tests. Everything below is
        importable from `logq` (`from logq import parse`).

        1. `parse(lines) -> (records, skipped)`: `lines` is an iterable of strings,
           each with or without its line end; `records` a list of `Record`s in the
           order they start; `skipped` the number of lines skipped, as FORMAT.md
           says. `Record` is a frozen dataclass with the fields `ts` (a `datetime`
           in UTC, `tzinfo=timezone.utc`), `level` (its canonical name),
           `component`, `message` and `line` (the 1-based number of the line the
           record starts on).
        2. `read(path) -> (records, skipped)`: the same, from a file.
        3. `select(records, level=None, component=None, since=None, until=None,
           grep=None) -> list`: the records that pass every filter given, in order.
           - `level`: that level and every level above it (DEBUG < INFO < WARN <
             ERROR < FATAL); a canonical name or another name for it, in any case.
           - `component`: that component and those below it: `db` selects `db` and
             `db.pool`, not `dbx`.
           - `since` (included) and `until` (left out): an aware `datetime`, or a
             string written as FORMAT.md writes a timestamp.
           - `grep`: text the message contains, ignoring case; a message's
             continuation lines are part of it.
        4. `counts(records, by) -> dict`: how many records per `level` (keys in
           level order), per `component`, or per `hour` (keys `YYYY-MM-DDTHH`, in
           UTC); `component` and `hour` keys sorted; only keys with records.
        5. `top(records, n) -> list[tuple[str, int]]`: the `n` most frequent
           message patterns with their counts, most frequent first, ties in the
           order the pattern first appears. A message's pattern is its first line
           with every run of digits replaced by `#`.
        6. `export(records, fmt) -> str`: with `fmt="jsonl"`, one JSON object per
           line, as `json.dumps` writes it by default, with the keys `ts`, `level`,
           `component`, `line`, `message` in that order and `ts` written
           `YYYY-MM-DDTHH:MM:SSZ` in UTC; with `fmt="csv"`, the header
           `ts,level,component,line,message` and a row per record, as Python's
           `csv` module writes them with `lineterminator="\n"`.
        7. `python -m logq FILE [--level L] [--component C] [--since TS]
           [--until TS] [--grep TEXT]` and exactly one of `--count
           level|component|hour` (prints `KEY COUNT` lines), `--top N` (prints
           `COUNT PATTERN` lines) or `--export jsonl|csv` (prints what `export`
           returns). When lines were skipped, it then prints `logq: skipped N
           lines` on stderr. A FILE it cannot read: `logq: cannot read FILE` on
           stderr, exit status 2. A `--since` or `--until` that is not a
           timestamp: `logq: bad time TS` on stderr, exit status 2.
    '''),
    "docs/FORMAT.md": d(r'''
        # The log format

        A log file is UTF-8, with or without a byte order mark at its start, and
        its lines end in LF or CRLF. Whitespace at the end of a line means
        nothing; a line of nothing but whitespace is empty.

        A record starts on a line written

            TIMESTAMP LEVEL [COMPONENT] MESSAGE

        with one space between the parts.

        - TIMESTAMP is `YYYY-MM-DDTHH:MM:SS` and then `Z` (UTC) or an offset from
          UTC, `+HH:MM` or `-HH:MM`. Records are compared, selected, counted and
          written in UTC: `2026-11-02T10:20:00+01:00` is 09:20 UTC.
        - LEVEL is DEBUG, INFO, WARN, ERROR or FATAL, in capitals. Some programs
          write WARNING for WARN, ERR for ERROR and CRITICAL for FATAL: these are
          the same levels, and logq always writes the first name.
        - COMPONENT is letters, digits, `.`, `_` and `-`: a dotted path, so
          `db.pool` is part of `db`. It may be empty, `[]`: that record's
          component is `-`.
        - MESSAGE is the rest of the line, and may be empty.

        A line that starts with a space or a tab continues the message of the last
        record before it: it is added to that message after a newline, without its
        leading whitespace. Before the first record, such a line is skipped.

        An empty line is ignored. Any other line that is not a record -- one whose
        LEVEL is none of the above, say -- is skipped. logq counts the lines it
        skips; it does not count empty lines.
    '''),
    "logq/__init__.py": '"""logq: see README.md."""\n',
    "tests/.gitkeep": "",
}

LOGQ_REF = {
    "logq/__init__.py": d(r'''
        """logq: query log files. See README.md, and docs/FORMAT.md for the records."""
        import csv
        import io
        import json
        import re
        from collections import Counter
        from dataclasses import dataclass, replace
        from datetime import datetime, timezone
        from pathlib import Path

        LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")
        ALIASES = {"WARNING": "WARN", "ERR": "ERROR", "CRITICAL": "FATAL"}
        STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})"
        RECORD = re.compile(rf"({STAMP}) ([A-Z]+) \[([A-Za-z0-9._-]*)\](?: (.*))?")


        @dataclass(frozen=True)
        class Record:
            ts: datetime
            level: str
            component: str
            message: str
            line: int


        def timestamp(text):
            """A timestamp as FORMAT.md writes one, as an aware datetime in UTC."""
            if not re.fullmatch(STAMP, text):
                raise ValueError(f"not a timestamp: {text!r}")
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


        def parse(lines):
            records, skipped = [], 0
            for number, raw in enumerate(lines, 1):
                line = raw.rstrip()
                if not line:
                    continue
                if line[0] in " \t":
                    if records:
                        last = records[-1]
                        records[-1] = replace(last, message=last.message + "\n" + line.lstrip())
                    else:
                        skipped += 1
                    continue
                match = RECORD.fullmatch(line)
                level = ALIASES.get(match.group(2), match.group(2)) if match else None
                try:
                    ts = timestamp(match.group(1)) if level in LEVELS else None
                except ValueError:
                    ts = None
                if ts is None:
                    skipped += 1
                    continue
                records.append(Record(ts, level, match.group(3) or "-", match.group(4) or "",
                                      number))
            return records, skipped


        def read(path):
            return parse(Path(path).read_text(encoding="utf-8-sig").split("\n"))


        def _rank(level):
            name = ALIASES.get(level.upper(), level.upper())
            if name not in LEVELS:
                raise ValueError(f"no level {level!r}")
            return LEVELS.index(name)


        def _moment(value):
            if isinstance(value, str):
                return timestamp(value)
            if value.tzinfo is None:
                raise ValueError("a datetime without a time zone")
            return value.astimezone(timezone.utc)


        def select(records, level=None, component=None, since=None, until=None, grep=None):
            low = None if level is None else _rank(level)
            since = None if since is None else _moment(since)
            until = None if until is None else _moment(until)
            needle = None if grep is None else grep.lower()
            out = []
            for r in records:
                if low is not None and LEVELS.index(r.level) < low:
                    continue
                if component is not None and r.component != component \
                        and not r.component.startswith(component + "."):
                    continue
                if since is not None and r.ts < since:
                    continue
                if until is not None and r.ts >= until:
                    continue
                if needle is not None and needle not in r.message.lower():
                    continue
                out.append(r)
            return out


        def counts(records, by):
            if by == "level":
                found = Counter(r.level for r in records)
                return {name: found[name] for name in LEVELS if found[name]}
            if by == "component":
                found = Counter(r.component for r in records)
            elif by == "hour":
                found = Counter(r.ts.strftime("%Y-%m-%dT%H") for r in records)
            else:
                raise ValueError(f"cannot count by {by!r}")
            return dict(sorted(found.items()))


        def top(records, n):
            found, first = Counter(), {}
            for i, r in enumerate(records):
                pattern = re.sub(r"\d+", "#", r.message.split("\n")[0])
                found[pattern] += 1
                first.setdefault(pattern, i)
            ranked = sorted(found, key=lambda p: (-found[p], first[p]))
            return [(p, found[p]) for p in ranked[:n]]


        def _stamp(ts):
            return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


        def export(records, fmt):
            if fmt == "jsonl":
                return "".join(json.dumps({"ts": _stamp(r.ts), "level": r.level,
                                           "component": r.component, "line": r.line,
                                           "message": r.message}) + "\n" for r in records)
            if fmt == "csv":
                out = io.StringIO()
                writer = csv.writer(out, lineterminator="\n")
                writer.writerow(["ts", "level", "component", "line", "message"])
                for r in records:
                    writer.writerow([_stamp(r.ts), r.level, r.component, r.line, r.message])
                return out.getvalue()
            raise ValueError(f"no format {fmt!r}")
    '''),
    "logq/__main__.py": d(r'''
        """python -m logq FILE [filters] (--count BY | --top N | --export FMT): see README.md."""
        import argparse
        import sys

        from logq import counts, export, read, select, timestamp, top


        def main(argv=None):
            p = argparse.ArgumentParser(prog="logq")
            p.add_argument("file")
            for name in ("--level", "--component", "--since", "--until", "--grep"):
                p.add_argument(name)
            what = p.add_mutually_exclusive_group(required=True)
            what.add_argument("--count", choices=("level", "component", "hour"))
            what.add_argument("--top", type=int)
            what.add_argument("--export", choices=("jsonl", "csv"))
            a = p.parse_args(argv)
            for text in (a.since, a.until):
                if text is not None:
                    try:
                        timestamp(text)
                    except ValueError:
                        print(f"logq: bad time {text}", file=sys.stderr)
                        return 2
            try:
                records, skipped = read(a.file)
            except (OSError, UnicodeDecodeError):
                print(f"logq: cannot read {a.file}", file=sys.stderr)
                return 2
            chosen = select(records, level=a.level, component=a.component, since=a.since,
                            until=a.until, grep=a.grep)
            if a.count:
                for key, n in counts(chosen, a.count).items():
                    print(f"{key} {n}")
            elif a.top is not None:
                for pattern, n in top(chosen, a.top):
                    print(f"{n} {pattern}")
            else:
                sys.stdout.write(export(chosen, a.export))
            if skipped:
                print(f"logq: skipped {skipped} lines", file=sys.stderr)
            return 0


        if __name__ == "__main__":
            sys.exit(main())
    '''),
    "tests/test_logq.py": d(r'''
        from logq import counts, parse, select


        def test_a_record_and_its_continuation():
            records, skipped = parse(["x", "2026-01-01T00:00:00+01:00 WARNING [a.b] m", " more"])
            assert skipped == 1 and records[0].level == "WARN" and records[0].message == "m\nmore"
            assert counts(records, "hour") == {"2025-12-31T23": 1}
            assert select(records, component="a") == records
    '''),
}

project("r-long", LOGQ, d('''
    Build logq as README.md describes, step by step, keeping a plan with
    update_plan. Add tests for each step and run `python -m pytest -q` before you
    finish.
    '''), LOGQ_REF, allow=("python -m pytest*", "python -m logq*", "python -c *"),
    extra="[runtime]\nauto_compact_token_limit = 12000\n",
    hidden={"test_hidden_r.py": d(r'''
        import csv
        import io
        import json
        import subprocess
        import sys
        from datetime import datetime, timedelta, timezone
        from pathlib import Path

        import logq

        ROOT = Path(__file__).resolve().parents[1]
        DESELECT = ()
        UTC = timezone.utc
        LOG = [
            "  orphan continuation",                                           # 1 skipped
            "2026-11-02T09:15:00Z INFO [api] started on port 8080",            # 2
            "2026-11-02T09:15:01Z DEBUG [db.pool] opened 4 connections",       # 3
            "2026-11-02T10:20:00+01:00 WARNING [db] slow query took 1532 ms",  # 4
            "    SELECT * FROM orders",
            "\tWHERE id = 7   ",
            "this line is garbage",                                            # 7 skipped
            "2026-11-02T09:30:00Z ERR [api] request 17 failed",                # 8
            "2026-11-02T09:31:00Z ERR [api] request 18 failed",                # 9
            "   ",
            "2026-11-02T09:59:59-00:30 CRITICAL [] disk full",                 # 11
            "2026-11-02T10:05:00Z INFO [dbx] unrelated component",             # 12
            "2026-11-02T10:06:00Z INFO [db.pool] closed 4 connections",        # 13
            "  trailing continuation",
            "info lowercase is garbage too",                                   # 15 skipped
            "   after garbage",
            "2026-11-02T10:07:00Z NOTICE [api] unknown level",                 # 17 skipped
        ]
        SLOW = "slow query took 1532 ms\nSELECT * FROM orders\nWHERE id = 7"
        CLOSED = "closed 4 connections\ntrailing continuation\nafter garbage"


        def records():
            return logq.parse([line + "\n" for line in LOG])[0]


        def lines(rs):
            return [r.line for r in rs]


        def test_1_parse():
            rs, skipped = logq.parse([line + "\n" for line in LOG])
            assert skipped == 4 and lines(rs) == [2, 3, 4, 8, 9, 11, 12, 13]
            assert rs[2] == logq.Record(datetime(2026, 11, 2, 9, 20, tzinfo=UTC), "WARN", "db",
                                        SLOW, 4)
            assert rs[2].ts.utcoffset() == timedelta(0)
            assert (rs[3].level, rs[5].level, rs[5].component) == ("ERROR", "FATAL", "-")
            assert rs[5].ts == datetime(2026, 11, 2, 10, 29, 59, tzinfo=UTC)
            assert rs[7].message == CLOSED


        def test_2_read(tmp_path):
            path = tmp_path / "x.log"
            path.write_bytes(("\ufeff" + "\r\n".join(LOG[1:]) + "\r\n").encode("utf-8"))
            rs, skipped = logq.read(path)
            assert skipped == 3 and len(rs) == 8
            assert (rs[0].line, rs[0].message, rs[2].message) == (1, "started on port 8080", SLOW)


        def test_3_select():
            rs = records()
            assert lines(logq.select(rs, level="warn")) == [4, 8, 9, 11]
            assert lines(logq.select(rs, level="WARNING")) == [4, 8, 9, 11]
            assert lines(logq.select(rs, component="db")) == [3, 4, 13]
            assert lines(logq.select(rs, since="2026-11-02T10:00:00+01:00",
                                     until="2026-11-02T09:30:00Z")) == [2, 3, 4]
            assert lines(logq.select(rs, since=datetime(2026, 11, 2, 10, 29, 59, tzinfo=UTC))) == [11]
            assert lines(logq.select(rs, grep="where ID")) == [4]
            assert lines(logq.select(rs, level="INFO", component="api", grep="REQUEST")) == [8, 9]


        def test_4_counts():
            rs = records()
            by_level = logq.counts(rs, "level")
            assert by_level == {"DEBUG": 1, "INFO": 3, "WARN": 1, "ERROR": 2, "FATAL": 1}
            assert list(by_level) == ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
            by_component = logq.counts(rs, "component")
            assert list(by_component.items()) == [("-", 1), ("api", 3), ("db", 1), ("db.pool", 2),
                                                  ("dbx", 1)]
            assert logq.counts(rs, "hour") == {"2026-11-02T09": 5, "2026-11-02T10": 3}


        def test_5_top():
            assert logq.top(records(), 3) == [("request # failed", 2), ("started on port #", 1),
                                              ("opened # connections", 1)]


        def test_6_export():
            text = logq.export(records(), "jsonl")
            assert text.splitlines()[0] == json.dumps({
                "ts": "2026-11-02T09:15:00Z", "level": "INFO", "component": "api", "line": 2,
                "message": "started on port 8080"})
            rows = [json.loads(x) for x in text.splitlines()]
            assert len(rows) == 8 and list(rows[2]) == ["ts", "level", "component", "line", "message"]
            assert rows[2] == {"ts": "2026-11-02T09:20:00Z", "level": "WARN", "component": "db",
                               "line": 4, "message": SLOW}
            text = logq.export(records(), "csv")
            assert text.startswith("ts,level,component,line,message\n")
            table = list(csv.reader(io.StringIO(text)))
            assert len(table) == 9
            assert table[3] == ["2026-11-02T09:20:00Z", "WARN", "db", "4", SLOW]
            assert table[6] == ["2026-11-02T10:29:59Z", "FATAL", "-", "11", "disk full"]


        def cli(tmp_path, *args):
            path = tmp_path / "app.log"
            path.write_bytes(("\ufeff" + "\r\n".join(LOG) + "\r\n").encode("utf-8"))
            return subprocess.run([sys.executable, "-m", "logq", str(path), *args], cwd=ROOT,
                                  capture_output=True, text=True, encoding="utf-8")


        def test_7_command_line(tmp_path):
            r = cli(tmp_path, "--level", "warn", "--count", "level")
            assert (r.returncode, r.stdout) == (0, "WARN 1\nERROR 2\nFATAL 1\n")
            assert r.stderr.strip() == "logq: skipped 4 lines"
            assert cli(tmp_path, "--component", "api", "--top", "2").stdout == (
                "2 request # failed\n1 started on port #\n")
            assert cli(tmp_path, "--count", "hour").stdout == "2026-11-02T09 5\n2026-11-02T10 3\n"
            table = list(csv.reader(io.StringIO(
                cli(tmp_path, "--since", "2026-11-02T10:00:00Z", "--export", "csv").stdout)))
            assert table[1:] == [["2026-11-02T10:29:59Z", "FATAL", "-", "11", "disk full"],
                                 ["2026-11-02T10:05:00Z", "INFO", "dbx", "12", "unrelated component"],
                                 ["2026-11-02T10:06:00Z", "INFO", "db.pool", "13", CLOSED]]


        def test_7_command_line_errors(tmp_path):
            r = subprocess.run([sys.executable, "-m", "logq", str(tmp_path / "none.log"),
                                "--count", "level"], cwd=ROOT, capture_output=True, text=True)
            assert r.returncode == 2 and "logq: cannot read" in r.stderr
            r = cli(tmp_path, "--since", "yesterday", "--count", "level")
            assert r.returncode == 2 and "logq: bad time yesterday" in r.stderr
    ''') + "\n\n" + OWN_SUITE})
print("ok d")
