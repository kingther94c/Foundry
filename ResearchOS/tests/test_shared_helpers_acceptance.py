"""Acceptance tests for ResearchOS v12 (shared helpers), written from SPEC.md independently of
the code (held back from the agent that built it).

Each case traces to the "Shared helpers (v12)" section of SPEC.md. Behaviour is pinned by the
other acceptance files, which must all still pass; these check that no private copies remain.
"""

from __future__ import annotations

import ast
from pathlib import Path

import researchos

PACKAGE = Path(researchos.__file__).parent


def sources():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(PACKAGE.glob("*.py"))}


def test_fromisoformat_only_where_a_malformed_file_must_raise():
    users = sorted(name for name, text in sources().items() if "fromisoformat" in text)
    assert users in ([], ["sources.py"]), users


def test_no_private_number_or_date_checks():
    for name, text in sources().items():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.unparse(node)
                assert "isfinite" not in body, f"{name}: {node.name} checks finiteness itself"


def test_the_shared_helpers_are_used():
    text = sources()
    assert "is_number" in text["compare.py"] and "is_number" in text["blend.py"]
    for name in ("loop.py", "playbook.py", "blend.py"):
        assert "parse_iso_date" in text[name], name
