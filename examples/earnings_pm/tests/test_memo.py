"""Tests for earnings_pm.memo, and strict-schema checks on both client tools.

The memo fixture below is synthetic and hand-written; it describes no real company.
"""

import copy

import pytest

from earnings_pm.memo import MEMO_SCHEMA, SUBMIT_MEMO_TOOL, render_markdown
from earnings_pm.metrics import METRICS_TOOL

UNSUPPORTED_KEYWORDS = {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}

# Synthetic memo.
MEMO = {
    "ticker": "EXMP",
    "company": "Example Corp",
    "period": "Q3 2026",
    "report_date": "2026-10-01",
    "rating": "overweight",
    "conviction": "medium",
    "headline": "Revenue up 20% with 500 bps of operating margin expansion.",
    "thesis": "Pricing is holding and costs are scaling; the position earns its weight.",
    "key_metrics": [
        {"name": "Revenue", "value": "$1.20B", "change": "+20.0% YoY", "basis": "reported"},
        {"name": "Operating margin", "value": "25.0%", "change": "+500 bps", "basis": "computed"},
        {"name": "FCF | adj.", "value": "$300M", "change": None, "basis": "computed"},
    ],
    "guidance": "FY revenue raised to $4.8B-$4.9B.",
    "positives": ["Gross margin up 500 bps"],
    "risks": ["Capex doubled year over year"],
    "watch_items": ["Q4 bookings"],
    "sources": [{"title": "Q3 2026 press release", "url": "https://example.com/q3"}],
}


def walk_schemas(node, path="$"):
    """Yield (path, schema) for every subschema in a JSON schema."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            if key == "properties":
                for name, sub in value.items():
                    yield from walk_schemas(sub, f"{path}.{name}")
            elif key == "items":
                yield from walk_schemas(value, f"{path}[]")
            elif key in ("anyOf", "oneOf", "allOf"):
                for i, sub in enumerate(value):
                    yield from walk_schemas(sub, f"{path}<{key}{i}>")


def is_object_schema(schema):
    t = schema.get("type")
    return t == "object" or (isinstance(t, list) and "object" in t) or "properties" in schema


@pytest.mark.parametrize("tool", [METRICS_TOOL, SUBMIT_MEMO_TOOL], ids=lambda t: t["name"])
def test_tool_schema_is_strict(tool):
    assert tool["strict"] is True
    assert set(tool) == {"name", "description", "input_schema", "strict"}
    objects = 0
    for path, schema in walk_schemas(tool["input_schema"]):
        assert not (UNSUPPORTED_KEYWORDS & set(schema)), path
        if is_object_schema(schema):
            objects += 1
            assert schema.get("additionalProperties") is False, path
            assert sorted(schema.get("required", [])) == sorted(schema["properties"]), path
    assert objects >= 2


def test_submit_memo_tool_uses_memo_schema():
    assert SUBMIT_MEMO_TOOL["name"] == "submit_memo"
    assert SUBMIT_MEMO_TOOL["input_schema"] is MEMO_SCHEMA


def test_memo_schema_fields():
    props = MEMO_SCHEMA["properties"]
    assert set(props) == set(MEMO)
    assert props["rating"]["enum"] == ["overweight", "neutral", "underweight"]
    assert props["conviction"]["enum"] == ["low", "medium", "high"]
    assert props["report_date"]["type"] == ["string", "null"]
    assert props["guidance"]["type"] == ["string", "null"]
    km = props["key_metrics"]["items"]["properties"]
    assert km["change"]["type"] == ["string", "null"]
    assert km["basis"]["enum"] == ["computed", "reported"]
    for name in ("positives", "risks", "watch_items"):
        assert props[name] == {"type": "array", "items": {"type": "string"}}
    assert set(props["sources"]["items"]["properties"]) == {"title", "url"}


def test_render_markdown_contents():
    md = render_markdown(MEMO)
    assert md.startswith("# EXMP - Example Corp: Q3 2026\n")
    assert "**Rating:** Overweight | **Conviction:** Medium | **Reported:** 2026-10-01" in md
    assert "| Operating margin | 25.0% | +500 bps | computed |" in md
    assert "| FCF \\| adj. | $300M | - | computed |" in md
    assert "FY revenue raised" in md
    assert "- Capex doubled year over year" in md
    assert "- [Q3 2026 press release](https://example.com/q3)" in md
    assert md.endswith("\n")


def test_render_markdown_is_deterministic_and_pure():
    before = copy.deepcopy(MEMO)
    assert render_markdown(MEMO) == render_markdown(copy.deepcopy(MEMO))
    assert MEMO == before


def test_render_markdown_handles_nulls_and_empties():
    memo = dict(MEMO, report_date=None, guidance=None, key_metrics=[], risks=[], sources=[])
    md = render_markdown(memo)
    assert "Reported" not in md
    assert "No guidance given." in md
    assert "None reported." in md
    assert "## Risks\n\n- None noted.\n" in md
    assert "None" not in md.replace("None noted", "").replace("None reported", "").replace(
        "None listed", ""
    )