"""CI names each failed test where anyone can read it (scripts/ci_annotate.py).

A job's log opens only to someone signed in to GitHub; its annotations come
back from the public API. When the suite fails, CI turns the JUnit report
into `::error` lines, one per failed test.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_annotate.py"
spec = importlib.util.spec_from_file_location("ci_annotate", SCRIPT)
ci_annotate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_annotate)

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4" failures="1" errors="1">
<testcase classname="tests.test_a" name="test_passes" time="0.1"/>
<testcase classname="tests.test_a" name="test_fails[x,y]" time="0.1">
  <failure message="assert 1 == 2">def test_fails():
&gt;       assert 1 == 2
E       assert 1 == 2
tests/test_a.py:5: AssertionError</failure></testcase>
<testcase classname="" name="tests.test_b" time="0.0">
  <error message="collection failure">ImportError: No module named 'nowhere'</error></testcase>
<testcase classname="tests.test_a" name="test_skipped" time="0.0">
  <skipped message="not here"/></testcase>
</testsuite></testsuites>
"""


def _report(tmp_path, text=REPORT):
    path = tmp_path / "report.xml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_each_failure_and_error_is_found_and_nothing_else(tmp_path):
    found = ci_annotate.failures(_report(tmp_path))

    assert [name for name, _ in found] == ["tests.test_a::test_fails[x,y]", "tests.test_b"]
    assert found[0][1].startswith("assert 1 == 2\ndef test_fails():")
    assert "ImportError: No module named 'nowhere'" in found[1][1]


def test_an_annotation_is_one_line_whatever_the_message_holds(tmp_path):
    lines = ci_annotate.annotations(ci_annotate.failures(_report(tmp_path)))

    assert lines[0] == "::error title=2 failed::tests.test_a::test_fails[x,y], tests.test_b"
    # In the title, ":" and "," would end the property; in the message a
    # newline would end the command.
    assert lines[1].startswith("::error title=tests.test_a%3A%3Atest_fails[x%2Cy]::assert 1 == 2%0A")
    assert all("\n" not in line and "\r" not in line for line in lines)
    assert ci_annotate._escape("100%") == "100%25"


def test_ten_annotations_at_most_and_the_rest_counted(tmp_path):
    cases = "".join(f'<testcase classname="t" name="n{i}"><failure message="m{i}"/></testcase>'
                    for i in range(12))
    lines = ci_annotate.annotations(ci_annotate.failures(
        _report(tmp_path, f"<testsuite>{cases}</testsuite>")))

    assert len(lines) == 10
    assert lines[0].startswith("::error title=12 failed::t::n0, ") and lines[0].endswith(
        ", and 3 more")
    long = ci_annotate.annotations([("t", "\n".join(f"line {i}" for i in range(50)))])[1]
    assert long.count("%0A") == ci_annotate.MESSAGE_LINES - 1


def test_the_command_line(tmp_path):
    def run(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                              text=True, encoding="utf-8")

    done = run(_report(tmp_path))
    assert done.returncode == 0 and done.stdout.count("::error ") == 3

    clean = run(_report(tmp_path, '<testsuite><testcase classname="t" name="ok"/></testsuite>'))
    assert clean.returncode == 0 and clean.stdout == "the report names no failed test\n"

    missing = run(str(tmp_path / "never-written.xml"))
    assert missing.returncode == 0 and missing.stdout.startswith("::error title=no test report::")

    assert run().returncode == 2


def test_both_jobs_that_run_foundrys_suite_annotate_its_failures():
    workflow = (SCRIPT.parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    # A job starts at a line with its name indented two spaces.
    jobs = re.split(r"\n  (?=[a-z-]+:\n)", workflow)

    for job in ("test:", "run-from-clone:"):
        text = next(part for part in jobs if part.startswith(job))
        assert 'pytest tests -q --junitxml="${{ runner.temp }}/foundry-tests.xml"' in text, job
        step = text[text.index("The failed tests, where anyone can read them"):]
        assert step.index("if: failure()") < step.index(
            'python scripts/ci_annotate.py "${{ runner.temp }}/foundry-tests.xml"'), job
