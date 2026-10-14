"""Acceptance tests for abstraction masking (v13), written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "Abstraction masking (v13)" section of SPEC.md.
"""

from __future__ import annotations

import json
import random
import types
from dataclasses import asdict, replace

import pytest
from test_acceptance import (IDENTITY, OUTCOME, Fake, ScriptedClient, everything_sent, make_case,
                             role_of, text)

from researchos import cases, loop, masking

BRIEFS = {
    "release": "The company grew revenue 18% to $10.0 billion in 2021; segment A grew 40%. "
               "Helix Engine shipments doubled.",
    "transcript": "Management sounded confident and guided margins to 45%.",
}


def abstractor_from(briefs=None):
    briefs = briefs or BRIEFS

    def reply(n, system, prompt, schema, prefix):
        kind = "release" if "release" in prompt else "transcript"
        return {"brief": briefs[kind]}
    return Fake(reply)


def silent_masker():
    return Fake(lambda n, *a: {"redactions": []})


def failing_guesser():
    return Fake(lambda n, *a: {"guesses": [{"company": "Somebody Else", "ticker": "", "confidence": 0.3}],
                               "clues": []})


def test_schema_and_system_prompt():
    schema = masking.ABSTRACT_SCHEMA
    assert schema["type"] == "object" and schema["required"] == ["brief"]
    assert schema["properties"]["brief"]["type"] == "string"
    assert schema.get("additionalProperties") is False
    assert isinstance(masking.ABSTRACT_SYSTEM, str) and len(masking.ABSTRACT_SYSTEM) > 200


def test_without_abstraction_the_abstractor_is_never_used():
    abstractor = abstractor_from()
    plain = masking.mask_case(make_case(), silent_masker(), failing_guesser(), random.Random(3))
    with_arg = masking.mask_case(make_case(), silent_masker(), failing_guesser(), random.Random(3),
                                 abstractor=abstractor)
    assert abstractor.calls == []
    assert asdict(plain) == asdict(with_arg)
    assert plain.abstracted is False


def test_abstraction_needs_an_abstractor():
    with pytest.raises(ValueError):
        masking.mask_case(make_case(), silent_masker(), failing_guesser(), random.Random(3),
                          masking.MaskConfig(abstract=True))


def test_one_call_per_pre_masked_document_in_order():
    abstractor = abstractor_from()
    case = make_case()
    masked = masking.mask_case(case, silent_masker(), failing_guesser(), random.Random(3),
                               masking.MaskConfig(abstract=True), abstractor=abstractor)
    assert len(abstractor.calls) == len(case.documents) == 2
    assert [c["system"] for c in abstractor.calls] == [masking.ABSTRACT_SYSTEM] * 2
    assert [c["schema"] for c in abstractor.calls] == [masking.ABSTRACT_SCHEMA] * 2
    assert "release" in abstractor.calls[0]["prompt"] and "transcript" in abstractor.calls[1]["prompt"]
    sent = everything_sent(abstractor)
    for term in IDENTITY:                         # the abstractor sees pre-masked text only
        assert term.lower() not in sent.lower()
    assert "$90.0 billion" not in sent            # money already scaled
    assert "revenue" in abstractor.calls[0]["prompt"] and "45%" in abstractor.calls[1]["prompt"]
    assert masked.abstracted is True and masked.status == "masked"
    assert [d.kind for d in masked.documents] == ["release", "transcript"]
    assert [d.title for d in masked.documents] == ["release brief", "transcript brief"]
    assert [d.source for d in masked.documents] == ["", ""]


def test_rounds_run_on_the_briefs_without_rescaling():
    masker = Fake(lambda n, *a: {"redactions": [{"term": "Helix Engine", "replacement": "Product B",
                                                 "reason": "product"}]})
    guesser = failing_guesser()
    masked = masking.mask_case(make_case(), masker, guesser, random.Random(3),
                               masking.MaskConfig(abstract=True), abstractor=abstractor_from())
    assert "segment A grew 40%" in masker.calls[0]["prompt"]           # the masker reads the briefs
    seen = guesser.calls[0]["prompt"]
    assert "$10.0 billion" in seen and "2021" in seen                  # not scaled or shifted again
    assert "Product B" in seen and "Helix Engine" not in seen          # the round's redaction applied
    assert "Management sounded confident" in seen
    assert masked.documents[0].text == BRIEFS["release"].replace("Helix Engine", "Product B")


def test_the_identity_guard_covers_a_leaky_brief():
    leaky = {"release": "Zephyrex grew 18%; Dana Whitfield was upbeat.", "transcript": "Aurora Cloud grew."}
    guesser = failing_guesser()
    masked = masking.mask_case(make_case(), silent_masker(), guesser, random.Random(3),
                               masking.MaskConfig(abstract=True), abstractor=abstractor_from(leaky))
    sent = everything_sent(guesser).lower()
    for term in IDENTITY:
        assert term.lower() not in sent
    assert all(t.lower() not in (d.title + d.text).lower() for d in masked.documents for t in IDENTITY)


def test_keeps_masking_until_the_guesser_fails():
    def guesser_reply(n, *a):
        company = "Zephyrex" if n == 1 else "Somebody Else"
        return {"guesses": [{"company": company, "ticker": "", "confidence": 0.6}], "clues": ["segment A"]}
    masker = silent_masker()
    abstractor = abstractor_from()
    masked = masking.mask_case(make_case(), masker, Fake(guesser_reply), random.Random(3),
                               masking.MaskConfig(abstract=True, max_rounds=3), abstractor=abstractor)
    assert masked.status == "masked" and masked.rounds == 2
    assert len(abstractor.calls) == 2                 # abstraction happens once, not per round
    assert "segment A" in masker.calls[1]["prompt"]   # the clue goes back to the masker


def test_abstracted_survives_save_and_load(tmp_path):
    masked = masking.mask_case(make_case(), silent_masker(), failing_guesser(), random.Random(3),
                               masking.MaskConfig(abstract=True), abstractor=abstractor_from())
    masking.save_masked(tmp_path, masked)
    assert json.loads((tmp_path / "c1" / masking.MASKED_FILE).read_text(encoding="utf-8"))["abstracted"] is True
    assert asdict(masking.load_masked(tmp_path, "c1")) == asdict(masked)
    public = tmp_path / "c1" / masking.MASKED_FILE
    data = json.loads(public.read_text(encoding="utf-8"))
    del data["abstracted"]                            # a mask saved before v13
    public.write_text(json.dumps(data), encoding="utf-8")
    assert masking.load_masked(tmp_path, "c1").abstracted is False


def test_training_passes_the_abstractor(tmp_path):
    cases.save_case(tmp_path, make_case("c1"), OUTCOME)
    abstractor = abstractor_from()
    roles = loop.Roles(masker=silent_masker(), guesser=failing_guesser(),
                       analyst=Fake(lambda n, system, prompt, *a: {
                           "stance": "bullish", "prob_up": 0.6, "conviction": "low", "key_points": []}),
                       judge=Fake(lambda *a: {"prob_up": 0.6, "thesis": "t"}),
                       reflector=Fake(lambda *a: {"why_market_moved": "m", "where_we_were_wrong": "w",
                                                  "lessons": []}),
                       abstractor=abstractor)
    loop.train(tmp_path, ["c1"], tmp_path / "pb.json", roles, mask_config=masking.MaskConfig(abstract=True))
    assert len(abstractor.calls) == 2
    assert masking.load_masked(tmp_path, "c1").abstracted is True
    assert "segment A grew 40%" in everything_sent(roles.analyst)
    assert loop.Roles(masker=1, guesser=2, analyst=3, judge=4, reflector=5).abstractor is None


class AbstractingClient(ScriptedClient):
    """ScriptedClient, plus the abstractor's schema."""

    def create(self, **kwargs):
        props = kwargs["output_config"]["format"]["schema"]["properties"]
        if "brief" in props:
            self.kwargs.append(kwargs)
            usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                          cache_creation_input_tokens=0)
            return types.SimpleNamespace(content=[text(json.dumps({"brief": "Revenue grew 18%; segment A grew 40%."}))],
                                         stop_reason="end_turn", stop_details=None, usage=usage)
        return super().create(**kwargs)


def test_cli_mask_abstract(tmp_path, capsys):
    from researchos.__main__ import main
    cases.save_case(tmp_path, make_case("c1"), OUTCOME)
    client = AbstractingClient()
    assert main(["train", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"), "--ids", "c1",
                 "--mask-abstract", "--mask-give-up", "0.95"], client_factory=lambda: client) == 0
    capsys.readouterr()
    briefs = [kw for kw in client.kwargs if "brief" in kw["output_config"]["format"]["schema"]["properties"]]
    assert len(briefs) == 2 and {kw["output_config"]["effort"] for kw in briefs} == {"high"}
    assert masking.load_masked(tmp_path, "c1").abstracted is True
    cases.save_case(tmp_path, replace(make_case("c2"), event_date="2026-07-31"), OUTCOME)
    plain = ScriptedClient()                         # without the flag no abstractor is asked
    assert main(["evaluate", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb.json"), "--ids", "c2"],
                client_factory=lambda: plain) == 0
    assert all(role_of(kw) != "abstractor" for kw in plain.kwargs)
