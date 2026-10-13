"""After the reveal: compare prediction and outcome, write general lessons."""

from __future__ import annotations

from tools.textmask import redact, scale_money, shift_years

from researchos.cases import Document, identity_terms, render_documents
from researchos.masking import COMPANY_REPLACEMENT, guard_documents
from researchos.perspectives import PERSPECTIVES, render_view
from researchos.playbook import Lesson

REFLECTOR_SYSTEM = """\
You are the reflector in a research pipeline that trains an analyst to judge \
how a stock reacts to quarterly earnings. The company's identity is hidden \
(names replaced, money scaled, years shifted). The prediction was sealed \
before the outcome was revealed; now you see both. Explain why the market \
moved the way it did, where the analysis went wrong or right, and write \
lessons that would generalize to other companies' earnings. A lesson must \
never name or hint at this company, its products, people or figures: it is \
shown to the analyst on every later case. Each lesson says when it applies \
and which perspective it belongs to (one of: """ + ", ".join(PERSPECTIVES) + """, \
or "general")."""

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "why_market_moved": {"type": "string"},
        "where_we_were_wrong": {"type": "string"},
        "lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lesson": {"type": "string"},
                    "applies_when": {"type": "string"},
                    "perspective": {"type": "string"},
                },
                "required": ["lesson", "applies_when", "perspective"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["why_market_moved", "where_we_were_wrong", "lessons"],
    "additionalProperties": False,
}


def _num(value) -> str:
    return f"{value:+g}"


def _masked_notes(case, masked, notes: str) -> str:
    """Outcome notes masked with the case's mask key, so the reflector never sees the name."""
    if not notes:
        return ""
    terms = identity_terms(case)
    mapping = {t: COMPANY_REPLACEMENT for t in terms}
    mapping.update(masked.mapping or {})
    text = redact(notes, mapping)
    factor = masked.factor
    if isinstance(factor, (int, float)) and factor > 0:
        text = scale_money(text, factor)
    if isinstance(masked.year_offset, int):
        text = shift_years(text, masked.year_offset)
    return guard_documents([Document("notes", "", text)], terms)[0].text


def render_outcome(case, masked, outcome) -> str:
    lines = [f"Abnormal return (stock minus market), first reacting session: "
             f"{_num(outcome.abnormal_return_1d)}"]
    if outcome.stock_return_1d is not None:
        lines.append(f"Stock return: {_num(outcome.stock_return_1d)}")
    if outcome.market_return_1d is not None:
        lines.append(f"Market return: {_num(outcome.market_return_1d)}")
    notes = _masked_notes(case, masked, outcome.notes)
    if notes:
        lines.append(f"Notes: {notes}")
    direction = "beat" if outcome.abnormal_return_1d > 0 else "did not beat"
    lines.append(f"The stock {direction} the market.")
    return "\n".join(lines)


def reflect(case, masked, analysis, outcome, reflector) -> list[Lesson]:
    """One reflector call after the reveal; returns general lessons tagged with case_id."""
    prompt = (
        "Sealed prediction\n\n"
        + "\n\n".join(render_view(name, view) for name, view in analysis.views.items())
        + f"\n\nPlaybook-weighted probability the stock beats the market: "
          f"{analysis.prob_weighted:.3f}\n"
        + f"Judge's probability: {analysis.prob_judge:.3f}\n"
        + f"Judge's thesis: {analysis.thesis}\n\n"
        + "Outcome\n\n" + render_outcome(case, masked, outcome) + "\n\n"
        + "Masked documents\n\n" + render_documents(masked.documents) + "\n"
        + "Why did the market move the way it did? Where was the analysis wrong, "
          "and where right? Write lessons that would generalize to other "
          "companies; never name this company or anything specific to it."
    )
    output = reflector.json(REFLECTOR_SYSTEM, prompt, REFLECT_SCHEMA)
    items = output.get("lessons") if isinstance(output, dict) else None
    lessons: list[Lesson] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = item.get("lesson")
        if not isinstance(text, str) or not text.strip():
            continue
        applies_when = item.get("applies_when")
        perspective = item.get("perspective")
        perspective = perspective.strip().lower() if isinstance(perspective, str) else ""
        if perspective not in PERSPECTIVES:
            perspective = "general"
        lessons.append(Lesson(
            text=text.strip(),
            applies_when=applies_when.strip() if isinstance(applies_when, str) else "",
            perspective=perspective,
            case_id=case.case_id,
        ))
    return lessons
