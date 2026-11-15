"""The documents say what is so.

On 2026-11-15 the documents quoted four different sizes of the suite, a count
of decisions 163 short, and a home folder the code does not use (D-198).
Prose goes stale without a word, so the few statements a machine can check
are checked here: the size of the suite is quoted once and is the size
collected, the roadmap names the latest decision, decisions are numbered once
with no gap, relative links lead somewhere, and Foundry's folder is the one
the code uses.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

from foundry.core import config as foundry_config

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ROADMAP = DOCS / "roadmap.md"
DECISIONS = DOCS / "decision-log.md"
README = ROOT / "README.md"

# A record of its day keeps the figures and the names of its day: the
# decisions, the research notes and the scorecard of past builds.
DATED = {"decision-log.md", "foundry-acceptance.md"}

# The one sentence that may quote the size of the suite.
SUITE_SIZE = re.compile(r"The suite collects ([0-9][0-9,]*) tests")
# A size for the suite wherever it is said: a figure of four digits or more,
# of tests. A handful of tests named in passing is not one.
A_SIZE = re.compile(r"(?<![\w.,-])(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})\s+tests\b")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _name(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _present_documents() -> list[Path]:
    """What describes Foundry as it is, as against what records a day."""
    demo = ROOT / "demo"
    return [
        README, ROOT / "AGENT.md",
        *sorted(p for p in DOCS.glob("*.md") if p.name not in DATED),
        *sorted(demo.glob("*.md")), *sorted(demo.glob("*.py")),
        *sorted(demo.glob("*.ipynb")),
    ]


def _linked_documents() -> list[Path]:
    """Every Markdown document a reader follows links in."""
    return [README, ROOT / "AGENT.md", ROOT / "DELTA.md",
            *sorted(DOCS.rglob("*.md")), *sorted((ROOT / "demo").glob("*.md"))]


# -- the size of the suite ---------------------------------------------------

def suite_size_problem(roadmap: str, collected: int) -> str | None:
    """What is wrong with the roadmap's figure for a run that collected
    ``collected`` tests, or None."""
    quoted = SUITE_SIZE.findall(roadmap)
    if len(quoted) != 1:
        return ("docs/roadmap.md is to say 'The suite collects N tests' once, in "
                f"its Status; it says so {len(quoted)} times")
    if int(quoted[0].replace(",", "")) != collected:
        return (f"docs/roadmap.md says the suite collects {quoted[0]} tests; this "
                f"run collected {collected:,}. Bring the sentence in its Status up "
                "to date.")
    return None


def the_roadmap_today(roadmap: str) -> str:
    """The roadmap without the snapshots it keeps: a section headed
    '### Status as of DATE' holds the figures of that date."""
    return re.sub(r"(?ms)^### Status as of \d{4}-\d{2}-\d{2}.*?(?=^## )", "", roadmap)


def collected_as_ci_does(config) -> bool:
    """Whether this run collected what CI's does: the whole of tests/, with
    nothing narrowing it."""
    option = config.option
    narrowed = any((
        getattr(option, "keyword", ""), getattr(option, "markexpr", ""),
        getattr(option, "deselect", None), getattr(option, "ignore", None),
        getattr(option, "ignore_glob", None), getattr(option, "lf", False),
        getattr(option, "stepwise", False),
    ))
    here = config.invocation_params.dir
    asked = {(here / arg).resolve() for arg in config.args}
    return not narrowed and asked == {Path(__file__).resolve().parent}


def test_the_roadmap_quotes_the_suite_size_collected(request):
    if sys.platform != "win32":
        pytest.skip("the figure is of a run on Windows, as CI's are")
    if not collected_as_ci_does(request.config):
        pytest.skip("only a run of the whole suite can check its size")

    problem = suite_size_problem(_read(ROADMAP), len(request.session.items))

    assert problem is None, problem


def test_a_roadmap_that_quotes_the_size_collected_has_no_problem():
    assert suite_size_problem("The suite collects 5,021 tests, which run.", 5021) is None
    assert suite_size_problem("The suite collects 987 tests.", 987) is None
    assert suite_size_problem("The suite collects 12,345 tests.", 12345) is None


def test_a_roadmap_that_quotes_another_size_is_told_both():
    problem = suite_size_problem("The suite collects 5,021 tests, which run.", 5022)

    assert problem is not None
    assert "5,021" in problem and "5,022" in problem


def test_a_roadmap_that_quotes_no_size_or_two_is_a_problem():
    assert "0 times" in suite_size_problem("Every test passes.", 5021)
    twice = "The suite collects 9 tests. The suite collects 9 tests."
    assert "2 times" in suite_size_problem(twice, 9)


def test_a_dated_snapshot_in_the_roadmap_keeps_its_figure():
    roadmap = ("## Status (2026-11-15)\n\nThe suite collects 5,021 tests.\n\n"
               "### Status as of 2026-09-01, after a check\n\n1436 tests pass.\n\n"
               "## M0\n\n1,234 tests were written here.\n")

    today = the_roadmap_today(roadmap)

    assert "1436 tests" not in today
    assert A_SIZE.findall(today) == ["5,021 tests", "1,234 tests"]


@pytest.mark.parametrize("text, found", [
    ("4658 tests: golden scenarios", ["4658 tests"]),
    ("Foundry's 1,436 tests can run", ["1,436 tests"]),
    ("All 12,345 tests pass", ["12,345 tests"]),
    ("its 30 tests", []),
    ("about thirty tests", []),
    ("version 3.1436 tests nothing", []),
    ("the 2026 tests", ["2026 tests"]),
])
def test_what_counts_as_a_size_for_the_suite(text, found):
    assert A_SIZE.findall(text) == found


def test_nothing_else_quotes_the_suite_size():
    said = {}
    for path in _present_documents():
        text = _read(path)
        if path == ROADMAP:
            text = SUITE_SIZE.sub("", the_roadmap_today(text))
        if A_SIZE.search(text):
            said[_name(path)] = A_SIZE.findall(text)

    assert not said, (
        "the size of the suite is quoted in docs/roadmap.md's Status and nowhere "
        f"else, so that one sentence is all there is to keep true: {said}")


# -- decisions ---------------------------------------------------------------

def _decisions() -> list[int]:
    return [int(n) for n in re.findall(r"(?m)^## D-(\d+)\b", _read(DECISIONS))]


def test_decisions_are_numbered_once_with_no_gap():
    numbers = _decisions()
    assert numbers, "docs/decision-log.md has no '## D-NNN' heading"

    twice = sorted({n for n in numbers if numbers.count(n) > 1})
    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))

    assert not twice, f"a decision's number is its name; given twice: {twice}"
    assert not missing, f"numbers are never skipped or reused; missing: {missing}"


def test_the_roadmap_names_the_latest_decision():
    latest = f"D-{max(_decisions()):03d}"

    assert re.search(rf"\b{latest}\b", _read(ROADMAP)), (
        f"docs/roadmap.md does not mention {latest}, the latest decision: say "
        "there, in the section for the work under way, what it changed")


# -- links -------------------------------------------------------------------

FENCE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
CODE_SPAN = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HEADING = re.compile(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


def _prose(markdown: str) -> str:
    """Markdown without its fenced blocks: what is in one is an example."""
    return FENCE.sub("", markdown)


def anchors(markdown: str) -> set[str]:
    """The fragments GitHub gives a document's headings: lower case, what is
    neither a word character, a hyphen nor a space dropped, spaces to hyphens,
    and -1, -2 for a heading said again."""
    seen: dict[str, int] = {}
    found = set(re.findall(r"<a\s+(?:id|name)=\"([^\"]+)\"", markdown))
    for heading in HEADING.findall(_prose(markdown)):
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)   # a link: its text
        text = text.replace("`", "").strip().lower()
        slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        found.add(slug if not count else f"{slug}-{count}")
    return found


def broken_links(path: Path, markdown: str) -> list[str]:
    """The relative links in ``markdown``, a document at ``path``, that lead
    nowhere: no such file, or no such heading in it."""
    broken = []
    for target in LINK.findall(CODE_SPAN.sub("", _prose(markdown))):
        if SCHEME.match(target) or target.startswith("//"):
            continue                     # another site's: not checked offline
        where, _, fragment = target.partition("#")
        linked = (path.parent / unquote(where)) if where else path
        if not linked.exists():
            broken.append(f"{target}: no such file")
        elif fragment and linked.suffix == ".md":
            text = markdown if linked == path else _read(linked)
            if unquote(fragment).lower() not in anchors(text):
                broken.append(f"{target}: no such heading")
    return broken


def test_relative_links_in_the_documents_resolve():
    broken = {}
    for path in _linked_documents():
        found = broken_links(path, _read(path))
        if found:
            broken[_name(path)] = found

    assert not broken, f"links that lead nowhere: {broken}"


def test_a_heading_gets_the_anchor_github_gives_it():
    markdown = ("# Foundry\n\n"
                "## After parity (2026-11-10): prove it, then ship it\n\n"
                "### 5.2 The tool surface (the 9 V1 tools and `update_plan`)\n\n"
                "## Open\n\n## Open\n\n"
                "```text\n## not a heading\n```\n")

    assert anchors(markdown) == {
        "foundry", "after-parity-2026-11-10-prove-it-then-ship-it",
        "52-the-tool-surface-the-9-v1-tools-and-update_plan", "open", "open-1",
    }


def test_a_link_to_a_missing_file_or_heading_is_broken(tmp_path):
    (tmp_path / "other.md").write_text("# Other\n\n## A part\n", encoding="utf-8")
    page = tmp_path / "page.md"
    markdown = ("# Page\n\n## Here\n\n"
                "[ok](other.md) [ok](other.md#a-part) [ok](#here) [ok](.)\n"
                "[site](https://example.com/x.md#nowhere) [mail](mailto:a@example.com)\n"
                "[gone](missing.md) [no part](other.md#b-part) [not here](#there)\n"
                "`[code](in-a-span.md)`\n\n"
                "```text\n[code](in-a-fence.md)\n```\n")
    page.write_text(markdown, encoding="utf-8")

    assert broken_links(page, markdown) == [
        "missing.md: no such file", "other.md#b-part: no such heading",
        "#there: no such heading",
    ]


# -- Foundry's folder --------------------------------------------------------

OLD_HOME = re.compile(r"~[/\\]\.foundry\b")


def _the_folder(monkeypatch) -> str:
    """Foundry's folder as the code finds it, written the way a document
    writes it."""
    monkeypatch.delenv(foundry_config.USER_DIR_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "LOCAL")
    folder = foundry_config.user_dir()
    assert folder.parent == Path("LOCAL"), "the folder is no longer under %LOCALAPPDATA%"
    return "%LOCALAPPDATA%\\" + folder.name


def test_the_readme_names_the_folder_the_code_uses(monkeypatch):
    folder = _the_folder(monkeypatch)
    readme = _read(README)

    assert folder in readme and foundry_config.USER_DIR_ENV in readme
    assert not OLD_HOME.search(readme), (
        "README.md tells a user where to put files, and nothing is read from "
        f"~/.foundry: the folder is {folder}, or the one "
        f"{foundry_config.USER_DIR_ENV} names")


def test_a_document_that_writes_the_short_form_says_what_it_stands_for(monkeypatch):
    folder = _the_folder(monkeypatch)
    unsaid = [
        _name(path) for path in _present_documents()
        if path.suffix == ".md" and OLD_HOME.search(text := _read(path))
        and not (folder in text and foundry_config.USER_DIR_ENV in text)
    ]

    assert not unsaid, (
        f"these write ~/.foundry/ without saying it stands for {folder}, or the "
        f"folder {foundry_config.USER_DIR_ENV} names: {unsaid}")
