import pytest
from tools.stats import combine_probabilities

from conftest import VIEW_PROBS, FakeLLM, default_answer

from researchos.cases import Document
from researchos.llm import LLMError
from researchos.masking import MaskedCase
from researchos.perspectives import PERSPECTIVES, JUDGE_SCHEMA, VIEW_SCHEMA, analyze
from researchos.playbook import Lesson, Playbook, render_for_prompt


def masked_case():
    docs = [Document("release", "the Company reports Q4", "Revenue rose 12% to $900.0 million."),
            Document("transcript", "Call", "We are confident.")]
    return MaskedCase("case-1", docs, "masked", 1, {"Acme": "the Company"}, 0.75, -5, [])


def agent():
    return FakeLLM(lambda s, p, schema, prefix: default_answer(schema, p))


def test_analyze_calls_each_perspective_with_shared_prefix():
    playbook = Playbook.initial()
    playbook.lessons.append(Lesson("Watch guidance.", "always", "guidance", "old-case"))
    analyst, judge = agent(), agent()
    a = analyze(masked_case(), playbook, analyst, judge)

    assert [c["prompt"].splitlines()[0] for c in analyst.calls] == [
        f"Perspective: {p}" for p in PERSPECTIVES]
    prefixes = {c["prefix"] for c in analyst.calls} | {judge.calls[0]["prefix"]}
    assert len(prefixes) == 1
    prefix = prefixes.pop()
    assert "release" in prefix and "the Company reports Q4" in prefix and "Call" in prefix
    assert "Revenue rose 12%" in prefix
    for c in analyst.calls:
        assert c["schema"] is VIEW_SCHEMA
        assert render_for_prompt(playbook) in c["prompt"]
        assert "Revenue rose" not in c["prompt"]  # documents live in the prefix
    assert len(judge.calls) == 1 and judge.calls[0]["schema"] is JUDGE_SCHEMA
    judge_prompt = judge.calls[0]["prompt"]
    assert render_for_prompt(playbook) in judge_prompt
    for p in PERSPECTIVES:
        assert f"{p} point" in judge_prompt

    assert list(a.views) == PERSPECTIVES
    assert a.case_id == "case-1"
    assert a.views["growth"].prob_up == 0.8 and a.views["growth"].stance == "bullish"
    assert a.prob_weighted == pytest.approx(sum(VIEW_PROBS.values()) / 7)
    assert a.prob_judge == 0.65 and a.thesis == "Beat and raise."


def test_weights_are_used_and_zero_weights_fall_back_to_mean():
    playbook = Playbook.initial()
    playbook.weights = {p: (1.0 if p == "growth" else 0.0) for p in PERSPECTIVES}
    assert analyze(masked_case(), playbook, agent(), agent()).prob_weighted == 0.8
    playbook.weights = {"growth": 3.0, "risks": 1.0}
    expected = combine_probabilities(VIEW_PROBS, playbook.weights)
    assert analyze(masked_case(), playbook, agent(), agent()).prob_weighted == expected
    playbook.weights = {p: 0.0 for p in PERSPECTIVES}
    assert analyze(masked_case(), playbook, agent(), agent()).prob_weighted == pytest.approx(
        sum(VIEW_PROBS.values()) / 7)


def test_probabilities_are_clamped_and_bad_enums_defaulted():
    analyst = FakeLLM(responses=[{"stance": "BULLISH", "prob_up": 1.7, "conviction": "huge",
                                  "key_points": ["a", 3]}])
    judge = FakeLLM(responses=[{"prob_up": -0.2, "thesis": "t"}])
    a = analyze(masked_case(), Playbook.initial(), analyst, judge)
    v = a.views["growth"]
    assert v.prob_up == 0.99 and v.stance == "bullish" and v.conviction == "low"
    assert v.key_points == ["a"]
    assert a.prob_judge == 0.01
    assert a.prob_weighted == pytest.approx(0.99)


@pytest.mark.parametrize("bad", [None, "0.6", float("nan"), True])
def test_invalid_probability_raises(bad):
    analyst = FakeLLM(responses=[{"stance": "neutral", "prob_up": bad, "conviction": "low",
                                  "key_points": []}])
    with pytest.raises(LLMError):
        analyze(masked_case(), Playbook.initial(), analyst, agent())


def test_analyst_receives_documents_only():
    m = masked_case()
    analyst, judge = agent(), agent()
    analyze(m, Playbook.initial(), analyst, judge)
    for c in analyst.calls + judge.calls:
        text = c["system"] + c["prompt"] + c["prefix"]
        assert "case-1" not in text
        assert "Acme" not in text
        assert "0.75" not in text
