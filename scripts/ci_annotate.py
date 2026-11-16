"""Name each failed test of a JUnit XML report as a GitHub annotation:
python scripts/ci_annotate.py REPORT

CI's logs open only to someone signed in to GitHub, and an agent working on
this repository usually is not; the annotations of a check run are returned
by the public API to anyone. So when a suite fails, CI runs this, and each
failed or erroring test becomes one `::error` line with the start of its
message. GitHub keeps ten error annotations a step: the first says how many
failed and names them, and one each follows for the first nine.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

SHOWN = 9
MESSAGE_LINES = 12
MESSAGE_CHARS = 2000


def _escape(text: str, *, in_property: bool = False) -> str:
    """Text as a workflow command carries it: these characters would end
    the command or the property early."""
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if in_property:
        text = text.replace(":", "%3A").replace(",", "%2C")
    return text


def failures(report: str) -> list[tuple[str, str]]:
    """(test id, message) for each test the report says failed or erred, a
    collection error included."""
    found = []
    for case in ET.parse(report).getroot().iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            name = "::".join(part for part in (case.get("classname"), case.get("name")) if part)
            text = "\n".join(part for part in (node.get("message"), node.text) if part)
            found.append((name or "(unnamed)", text.strip()))
            break
    return found


def annotations(found: list[tuple[str, str]]) -> list[str]:
    if not found:
        return []
    names = ", ".join(name for name, _ in found[:SHOWN])
    if len(found) > SHOWN:
        names += f", and {len(found) - SHOWN} more"
    lines = [f"::error title={_escape(f'{len(found)} failed', in_property=True)}::"
             f"{_escape(names)}"]
    for name, text in found[:SHOWN]:
        message = "\n".join(text.splitlines()[:MESSAGE_LINES])[:MESSAGE_CHARS]
        lines.append(f"::error title={_escape(name, in_property=True)}::"
                     f"{_escape(message or '(no message)')}")
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python scripts/ci_annotate.py REPORT", file=sys.stderr)
        return 2
    try:
        found = failures(argv[0])
    except (OSError, ET.ParseError) as exc:
        # The suite died before it wrote a report: say that much.
        print(f"::error title=no test report::{_escape(str(exc))}")
        return 0
    for line in annotations(found):
        print(line)
    if not found:
        print("the report names no failed test")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
