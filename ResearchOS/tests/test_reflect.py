import random

from tools.textmask import find_terms

from conftest import FakeLLM, make_case, make_outcome

from researchos.cases import identity_terms
from researchos.masking import mask_case
from researchos.perspectives import PERSPECTIVES, Analysis, View
from researchos.reflect import REFLECT_SCHEMA, reflect


def setup():
    case = make_case()
    masker = FakeLLM(responses=[{"redactions": [
        {"term": "Zephyr", "replacement": "the product line", "reason": "brand"}]}])
    guesser = FakeLLM(responses=[{"guesses": [], "clues": []}])
    masked = mask_case(case, masker, guesser, random.Random(0))
    views = {p: View("bullish", 0.7, "medium", [f"{p} kp"]) for p in PERSPECTIVES}
    analysis = Analysis(case.case_id, views, 0.7, 0.62, "thesis here")
    return case, masked, analysis


def test_reflect_builds_lessons_and_prompt():
    case, masked, analysis = setup()
    outcome = make_outcome(ar=-0.042, notes="Acme Widgets fell; Zephyr demand worried; ACMW.")
    reflector = FakeLLM(responses=[{
        "why_market_moved": "x", "where_we_were_wrong": "y",
        "lessons": [
            {"lesson": "Cash flow beats matter.", "applies_when": "fcf", "perspective": "cash_flow"},
            {"lesson": "Tone is noise.", "applies_when": "always", "perspective": "astrology"},
            {"lesson": "  ", "applies_when": "", "perspective": "risks"},
        ]}])
    lessons = reflect(case, masked, analysis, outcome, reflector)
    assert [(l.text, l.perspective, l.case_id) for l in lessons] == [
        ("Cash flow beats matter.", "cash_flow", case.case_id),
        ("Tone is noise.", "general", case.case_id),
    ]
    (call,) = reflector.calls
    assert call["schema"] is REFLECT_SCHEMA
    prompt = call["prompt"]
    assert "-0.042" in prompt and "0.700" in prompt and "0.620" in prompt
    assert "growth kp" in prompt
    assert masked.documents[0].text in prompt
    # the outcome notes are masked with the case's key: no identity, no masker terms
    assert "fell" in prompt
    assert not find_terms(prompt, identity_terms(case))
    assert "Zephyr" not in prompt and "news.example.com" not in prompt
