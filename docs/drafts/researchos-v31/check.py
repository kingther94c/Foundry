"""Check a ResearchOS v31 build against the hidden suite and the hand-written reference.

    python check.py suite [--build WS]        hidden suite + every existing test
    python check.py fuzz --build WS [--n N]   differential run on random pages

The reference is a copy of ResearchOS/ with reference/sources_append.py appended to
sources.py and the snapshot command's fetch_page given main_only=True. The fuzzer runs the
same random pages through three packages: ResearchOS as landed (the whole text must not
change), the reference (the main text and get_page), and the build. Run it with an
interpreter that has pytest and anthropic.
"""

from __future__ import annotations

import argparse
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
HIDDEN = "test_main_text_acceptance.py"
MAIN_OLD = "fetch_page=lambda url: get_page(url, user_agent=user_agent))"
MAIN_NEW = "fetch_page=lambda url: get_page(url, user_agent=user_agent, main_only=True))"


def _append(path: Path, extra: Path) -> None:
    text = path.read_text(encoding="utf-8").rstrip(NL) + NL + extra.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8", newline=NL)


def reference_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    _append(pkg / "sources.py", HERE / "reference" / "sources_append.py")
    main = pkg / "__main__.py"
    text = main.read_text(encoding="utf-8")
    assert text.count(MAIN_OLD) == 1, "the snapshot command's fetch_page moved"
    main.write_text(text.replace(MAIN_OLD, MAIN_NEW), encoding="utf-8", newline=NL)
    return pkg


def build_package(ws: Path, dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(Path(ws) / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    return pkg


def base_package(dest: Path) -> Path:
    pkg = dest / "researchos"
    shutil.copytree(ROS / "researchos", pkg, ignore=shutil.ignore_patterns("__pycache__"))
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
        shutil.copyfile(HERE / HIDDEN, root / "tests" / HIDDEN)
        shutil.copyfile(ROS / "pyproject.toml", root / "pyproject.toml")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        return subprocess.run(cmd, cwd=root, env=env_for(root), check=False).returncode


# --- the differential run -------------------------------------------------------------------------

TAGS = ["div", "div", "main", "p", "span", "section", "table", "tr", "td", "th", "pre", "ul",
        "li", "br", "b", "nav", "article", "header", "footer", "h3", "a", "ol",
        "script", "style", "template", "svg", "noscript", "head", "title", "meta", "body"]
ROLES = ['role="main"', 'role="main"', "role=main", 'role=" Main "', 'ROLE="MAIN"', "role",
         'role=""', 'role="main navigation"', 'role="navigation"', 'role="mainly"',
         'aria-role="main"', 'class="main"']
TEXTS = ["a", "B c", "  ", "&amp;", "&nbsp;x", "line" + NL + "break", "\t", "d&lt;e", "f",
         "Home", "Last Update", "1 | 2", "&#x26;", "café"]
CONTENT_TYPES = ["text/html; charset=utf-8", "text/html", "application/xhtml+xml", "",
                 "text/plain", "text/plain; charset=utf-8", "application/pdf", "TEXT/HTML"]


def _name(rng: random.Random, tag: str) -> str:
    return tag.upper() if rng.random() < 0.05 else tag


def _nodes(rng: random.Random, depth: int) -> str:
    out = []
    for _ in range(rng.randint(0, 4)):
        roll = rng.random()
        if roll < 0.3 or depth > 5:
            out.append(rng.choice(TEXTS))
        elif roll < 0.36:
            out.append(f"</{_name(rng, rng.choice(TAGS))}>")  # a stray end tag
        else:
            tag = rng.choice(TAGS)
            attrs = f" {rng.choice(ROLES)}" if rng.random() < 0.12 else ""
            if rng.random() < 0.04:
                attrs += " " + rng.choice(ROLES)
            close = rng.random()
            if close < 0.08:
                out.append(f"<{_name(rng, tag)}{attrs}/>")
            else:
                inner = _nodes(rng, depth + 1)
                end = f"</{_name(rng, tag)}>" if close < 0.85 else ""
                out.append(f"<{_name(rng, tag)}{attrs}>{inner}{end}")
    return "".join(out)


MAIN_OPENERS = ["<main>", '<div role="main">', '<div id="content" role="main">',
                '<section role="Main">', '<td role="main">', '<pre role="main">']


def page(rng: random.Random) -> str:
    body = _nodes(rng, 0)
    if rng.random() < 0.6:  # most pages hold one main element among random surroundings
        opener = rng.choice(MAIN_OPENERS)
        tag = opener[1:].split(">")[0].split(" ")[0]
        closer = f"</{tag}>" if rng.random() < 0.9 else ""
        body = _nodes(rng, 3) + opener + _nodes(rng, 1) + closer + _nodes(rng, 3)
    if rng.random() < 0.5:
        head = "<head><title>" + rng.choice(TEXTS) + "</title>" + _nodes(rng, 4) + "</head>"
        return f"<html>{head}<body>{body}</body></html>"
    return body


def worker(pkg_root: str, n: int, seed: int, out_path: str) -> None:
    sys.path[:0] = [pkg_root, str(REPO / "tools")]
    from researchos import sources

    rng = random.Random(seed)
    results = []
    for _ in range(n):
        doc = page(rng)
        content_type = rng.choice(CONTENT_TYPES)
        main_only = rng.random() < 0.7
        row = {"whole": list(sources.html_to_text(doc))}
        try:
            row["main"] = list(sources.html_to_text(doc, main_only=True))
        except TypeError as exc:  # the landed package has no main_only
            row["main"] = ["TypeError", str(exc)[:0]]
        try:
            kw = {"main_only": main_only} if main_only else {}
            got = sources.get_page("https://x.example/p", user_agent="ua",
                                   transport=lambda url, headers: (doc.encode("utf-8"),
                                                                   content_type), **kw)
            row["get_page"] = [got.url, got.title, got.text]
        except (ValueError, TypeError) as exc:
            row["get_page"] = [type(exc).__name__, str(exc) if isinstance(exc, ValueError) else ""]
        results.append(row)
    Path(out_path).write_text(json.dumps(results), encoding="utf-8")


def _run_worker(root: Path, n: int, seed: int, out: Path) -> list:
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "worker", str(root),
                           str(n), str(seed), str(out)], env=env_for(root), check=False)
    if done.returncode:
        raise SystemExit(f"worker for {root} exited {done.returncode}")
    return json.loads(out.read_text(encoding="utf-8"))


def fuzz(build: str, n: int, seed: int) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        roots = {"base": tmp / "base", "reference": tmp / "reference", "build": tmp / "build"}
        base_package(roots["base"])
        reference_package(roots["reference"])
        build_package(Path(build), roots["build"])
        got = {name: _run_worker(root, n, seed, tmp / f"{name}.json") for name, root in roots.items()}
    rng = random.Random(seed)
    docs = []
    for _ in range(n):
        docs.append(page(rng))
        rng.choice(CONTENT_TYPES)
        rng.random()
    parts = {"whole vs landed": 0, "main vs reference": 0, "get_page vs reference": 0,
             "reference whole vs landed": 0}
    shown = 0
    for i in range(n):
        base, ref, built = got["base"][i], got["reference"][i], got["build"][i]
        checks = [("whole vs landed", built["whole"], base["whole"]),
                  ("main vs reference", built["main"], ref["main"]),
                  ("get_page vs reference", built["get_page"], ref["get_page"]),
                  ("reference whole vs landed", ref["whole"], base["whole"])]
        for part, a, b in checks:
            if a != b:
                parts[part] += 1
                if shown < 5:
                    shown += 1
                    print(f"mismatch ({part}) on {docs[i]!r}:{NL}  build/ref {a!r}{NL}  "
                          f"expected  {b!r}")
    main_used = sum(1 for i in range(n) if got["reference"][i]["main"] != got["reference"][i]["whole"])
    print("mismatches by part:", parts)
    print(f"pages whose main text differs from the whole text: {main_used} of {n}")
    total = sum(parts.values())
    print(f"total: {n} pages, {total} mismatch(es)")
    return 1 if total else 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
        return 0
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["suite", "fuzz"])
    p.add_argument("--build")
    p.add_argument("--n", type=int, default=20000)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.command == "suite":
        return suite(a.build)
    if not a.build:
        p.error("fuzz needs --build")
    return fuzz(a.build, a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
