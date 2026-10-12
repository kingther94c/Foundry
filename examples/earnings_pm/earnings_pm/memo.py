"""The memo: its strict JSON schema (the submit_memo tool) and markdown rendering."""

from __future__ import annotations

from typing import Any

_STRING_LIST = {"type": "array", "items": {"type": "string"}}

MEMO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string", "description": "Ticker symbol, e.g. 'MSFT'."},
        "company": {"type": "string", "description": "Company name."},
        "period": {"type": "string", "description": "Reported period, e.g. 'Q3 FY2026'."},
        "report_date": {
            "type": ["string", "null"],
            "description": "Date the results were released (YYYY-MM-DD), or null.",
        },
        "rating": {"type": "string", "enum": ["overweight", "neutral", "underweight"]},
        "conviction": {"type": "string", "enum": ["low", "medium", "high"]},
        "headline": {"type": "string", "description": "One sentence: what happened."},
        "thesis": {
            "type": "string",
            "description": "What it means for the position, in a short paragraph.",
        },
        "key_metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string", "description": "Formatted value."},
                    "change": {
                        "type": ["string", "null"],
                        "description": "Formatted change vs. comparison period, or null.",
                    },
                    "basis": {
                        "type": "string",
                        "enum": ["computed", "reported"],
                        "description": (
                            "'computed' if from compute_metrics output, "
                            "'reported' if quoted from the report."
                        ),
                    },
                },
                "required": ["name", "value", "change", "basis"],
                "additionalProperties": False,
            },
        },
        "guidance": {
            "type": ["string", "null"],
            "description": "Management guidance, or null if none was given.",
        },
        "positives": _STRING_LIST,
        "risks": _STRING_LIST,
        "watch_items": _STRING_LIST,
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "ticker",
        "company",
        "period",
        "report_date",
        "rating",
        "conviction",
        "headline",
        "thesis",
        "key_metrics",
        "guidance",
        "positives",
        "risks",
        "watch_items",
        "sources",
    ],
    "additionalProperties": False,
}

SUBMIT_MEMO_TOOL: dict[str, Any] = {
    "name": "submit_memo",
    "description": (
        "Submit the finished portfolio-manager memo. Calling this ends the run. "
        "Every growth rate, margin and free-cash-flow figure must come from "
        "compute_metrics output and be marked basis 'computed'."
    ),
    "input_schema": MEMO_SCHEMA,
    "strict": True,
}


def _cell(text: Any) -> str:
    """Make a value safe for a markdown table cell."""
    s = "" if text is None else str(text)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _bullets(items: list[str] | None) -> list[str]:
    if not items:
        return ["- None noted."]
    return [f"- {item}" for item in items]


def render_markdown(memo: dict[str, Any]) -> str:
    """Render a memo as markdown. Same input, same output."""
    ticker = memo.get("ticker", "")
    company = memo.get("company", "")
    period = memo.get("period", "")
    lines: list[str] = [f"# {ticker} - {company}: {period}", ""]

    meta = [
        f"**Rating:** {str(memo.get('rating', '')).capitalize()}",
        f"**Conviction:** {str(memo.get('conviction', '')).capitalize()}",
    ]
    if memo.get("report_date"):
        meta.append(f"**Reported:** {memo['report_date']}")
    lines += [" | ".join(meta), ""]

    lines += [f"**{memo.get('headline', '')}**", "", str(memo.get("thesis", "")), ""]

    lines += ["## Key metrics", ""]
    metrics = memo.get("key_metrics") or []
    if metrics:
        lines += ["| Metric | Value | Change | Basis |", "| --- | --- | --- | --- |"]
        for m in metrics:
            change = m.get("change")
            lines.append(
                f"| {_cell(m.get('name'))} | {_cell(m.get('value'))} | "
                f"{_cell(change) if change is not None else '-'} | {_cell(m.get('basis'))} |"
            )
    else:
        lines.append("None reported.")
    lines.append("")

    lines += ["## Guidance", "", memo.get("guidance") or "No guidance given.", ""]
    lines += ["## Positives", "", *_bullets(memo.get("positives")), ""]
    lines += ["## Risks", "", *_bullets(memo.get("risks")), ""]
    lines += ["## What to watch", "", *_bullets(memo.get("watch_items")), ""]

    lines += ["## Sources", ""]
    sources = memo.get("sources") or []
    if sources:
        lines += [f"- [{s.get('title', '')}]({s.get('url', '')})" for s in sources]
    else:
        lines.append("- None listed.")

    return "\n".join(lines) + "\n"