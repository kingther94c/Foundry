"""Abstraction masking (v13): abstractor briefs before the masker and guesser rounds."""

import json
import logging
import random

import pytest
from tools.textmask import find_terms, redact

from conftest import FakeLLM, make_case, make_outcome, make_roles, schema_client

from researchos.__main__ import main
from researchos.cases import identity_terms, save_case
from researchos.llm import ClaudeJSON, LLMError
from researchos.loop import evaluate, train
from researchos.masking import (ABSTRACT_SCHEMA, ABSTRACT_SYSTEM, MaskConfig, guard_documents,
                                load_masked, mask_case, pre_mask, save_masked)

KINDS = ["release", "transcript", "expectations"]
BRIEFS = {
    "release": ("Revenue rose 12% from the prior year and came in 4% above expectations; "
                "the Zephyr line grew fastest. Revenue reached $100.0 million in 2025."),
    "transcript": ("Management sounded confident: gross margin widened 2 points to 41% and "
                   "next year's revenue guidance implies growth of about 8%."),
    "expectations": "Consensus expected revenue growth of 8% and earnings growth of 5%.",
}
GUESS_MISS = {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.3}],
              "clues": []}
NO_REDACTIONS = {"redactions": []}


def abstractor_fake():
    """Answers the three documents of make_case() in order, one brief each."""
    return FakeLLM(responses=[{"brief": BRIEFS[kind]} for kind in KINDS])


def hit(confidence, clue="segment A sells widgets"):
    return {"guesses": [{"company": "Acme Widgets", "ticker": "", "confidence": confidence}],
            "clues": [clue]}


def all_text(docs):
    return "\n".join(d.title + "\n" + d.text + "\n" + d.source for d in docs)


def abstract_case(case, masker=None, guesser=None, abstractor=None, **config):
    masker = masker or FakeLLM(responses=[NO_REDACTIONS])
    guesser = guesser or FakeLLM(responses=[GUESS_MISS])
    abstractor = abstractor or abstractor_fake()
    m = mask_case(case, masker, guesser, random.Random(1), MaskConfig(abstract=True, **config),
                  abstractor=abstractor)
    return m, masker, guesser, abstractor


# ---- masking.mask_case ---------------------------------------------------------------

def test_abstract_schema_is_strict_with_a_single_brief():
    assert ABSTRACT_SCHEMA == {"type": "object", "properties": {"brief": {"type": "string"}},
                               "required": ["brief"], "additionalProperties": False}
    assert ABSTRACT_SYSTEM.isascii() and "segment A" in ABSTRACT_SYSTEM


def test_abstract_is_off_by_default_and_never_uses_the_abstractor(case):
    assert MaskConfig().abstract is False
    abstractor = abstractor_fake()
    with_it = mask_case(case, FakeLLM(responses=[NO_REDACTIONS]),
                        FakeLLM(responses=[GUESS_MISS]), random.Random(1), abstractor=abstractor)
    without = mask_case(case, FakeLLM(responses=[NO_REDACTIONS]),
                        FakeLLM(responses=[GUESS_MISS]), random.Random(1))
    assert not abstractor.calls
    assert with_it == without
    assert with_it.abstracted is False


def test_abstract_without_an_abstractor_is_a_value_error(case):
    masker, guesser = FakeLLM(responses=[NO_REDACTIONS]), FakeLLM(responses=[GUESS_MISS])
    with pytest.raises(ValueError):
        mask_case(case, masker, guesser, random.Random(1), MaskConfig(abstract=True))
    assert not masker.calls and not guesser.calls


def test_abstractor_rewrites_each_pre_masked_document_in_order(case):
    m, masker, guesser, abstractor = abstract_case(case)
    pre, _, _, _ = pre_mask(case, random.Random(1), MaskConfig())
    pre = guard_documents(pre, identity_terms(case))
    assert len(abstractor.calls) == len(case.documents) == len(pre)
    for call, doc in zip(abstractor.calls, pre):
        assert call["system"] == ABSTRACT_SYSTEM
        assert call["schema"] == ABSTRACT_SCHEMA
        assert call["prefix"] is None
        assert doc.kind in call["prompt"] and doc.text in call["prompt"]
        assert not find_terms(call["prompt"], identity_terms(case))
    assert m.abstracted is True
    assert m.status == "masked" and m.rounds == 1
    assert [d.kind for d in m.documents] == KINDS
    assert [d.title for d in m.documents] == [f"{kind} brief" for kind in KINDS]
    assert [d.text for d in m.documents] == [BRIEFS[kind] for kind in KINDS]
    assert all(d.source == "" for d in m.documents)


def test_rounds_run_on_the_briefs_redacted_but_not_rescaled(case):
    masker = FakeLLM(responses=[{"redactions": [
        {"term": "Zephyr", "replacement": "the product line", "reason": "brand"}]}])
    m, masker, guesser, _ = abstract_case(case, masker=masker)
    # the masker sees the briefs, not the pre-masked documents
    assert BRIEFS["release"] in masker.calls[0]["prompt"]
    assert "record quarter" not in masker.calls[0]["prompt"]
    assert m.mapping["Zephyr"] == "the product line"
    for doc, kind in zip(m.documents, KINDS):
        assert doc.text == redact(BRIEFS[kind], m.mapping)
    assert "Zephyr" not in all_text(m.documents)
    assert "the product line" in m.documents[0].text
    # money and years in a brief are neither scaled nor shifted again
    assert "$100.0 million" in m.documents[0].text and "2025" in m.documents[0].text
    assert "the product line" in guesser.calls[0]["prompt"]
    assert "Zephyr" not in guesser.calls[0]["prompt"]


def test_identity_guard_runs_on_the_briefs(case):
    leaky = FakeLLM(responses=[{"brief": "Acme Widgets grew 12%; ACMW raised guidance; "
                                         "jane roe was upbeat about WidgetPro."}])
    m, masker, guesser, _ = abstract_case(case, abstractor=leaky)
    terms = identity_terms(case)
    for call in masker.calls + guesser.calls:
        assert not find_terms(call["system"] + call["prompt"] + (call["prefix"] or ""), terms)
    assert not find_terms(all_text(m.documents), terms)
    assert "grew 12%" in m.documents[0].text


def test_abstractor_runs_once_however_many_rounds(case):
    masker = FakeLLM(responses=[NO_REDACTIONS])
    m, masker, guesser, abstractor = abstract_case(
        case, masker=masker, guesser=FakeLLM(responses=[hit(0.9)]), max_rounds=3)
    assert m.status == "identifiable" and m.rounds == 3 and m.abstracted is True
    assert len(abstractor.calls) == len(case.documents)
    assert len(masker.calls) == 3 and len(guesser.calls) == 3
    assert "segment A sells widgets" not in masker.calls[0]["prompt"]
    assert "segment A sells widgets" in masker.calls[1]["prompt"]


def test_give_up_applies_with_abstraction(case):
    m, masker, guesser, _ = abstract_case(case, guesser=FakeLLM(responses=[hit(0.97)]),
                                          give_up_confidence=0.95)
    assert m.status == "identifiable" and m.rounds == 1
    assert len(masker.calls) == 1 and len(guesser.calls) == 1
    assert m.abstracted is True


def test_zero_rounds_returns_the_briefs(case):
    m, masker, guesser, abstractor = abstract_case(case, max_rounds=0)
    assert m.status == "identifiable" and m.rounds == 0 and m.abstracted is True
    assert not masker.calls and not guesser.calls
    assert len(abstractor.calls) == len(case.documents)
    assert [d.text for d in m.documents] == [BRIEFS[kind] for kind in KINDS]


@pytest.mark.parametrize("output", [{"brief": 5}, {"brief": "   "}, {}, ["brief"], None])
def test_abstractor_without_a_brief_raises(case, output):
    masker = FakeLLM(responses=[NO_REDACTIONS])
    with pytest.raises(LLMError):
        abstract_case(case, masker=masker, abstractor=FakeLLM(responses=[output]))
    assert not masker.calls


def test_save_and_load_masked_keep_abstracted(tmp_path, case):
    m, _, _, _ = abstract_case(case)
    save_masked(tmp_path, m)
    public = json.loads((tmp_path / case.case_id / "masked.json").read_text(encoding="utf-8"))
    key = json.loads((tmp_path / case.case_id / "mask_key.json").read_text(encoding="utf-8"))
    assert public["abstracted"] is True and "abstracted" not in key
    assert load_masked(tmp_path, case.case_id) == m


def test_load_masked_without_abstracted_reads_false(tmp_path, case):
    m, _, _, _ = abstract_case(case)
    save_masked(tmp_path, m)
    path = tmp_path / case.case_id / "masked.json"
    public = json.loads(path.read_text(encoding="utf-8"))
    del public["abstracted"]
    path.write_text(json.dumps(public), encoding="utf-8")
    assert load_masked(tmp_path, case.case_id).abstracted is False


# ---- loop ----------------------------------------------------------------------------

def test_roles_abstractor_defaults_to_none():
    assert make_roles().abstractor is None


def test_train_and_evaluate_pass_the_abstractor(tmp_path):
    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    later = make_case("b")
    later.event_date = "2026-05-01"
    save_case(root, later, make_outcome(ar=-0.02))
    abstractor = FakeLLM(lambda system, prompt, schema, prefix: {"brief": BRIEFS["release"]})
    roles = make_roles(abstractor=abstractor)
    config = MaskConfig(abstract=True)
    pb = tmp_path / "pb.json"

    records = train(root, ["a"], pb, roles, mask_config=config)
    assert len(records) == 1
    assert len(abstractor.calls) == 3
    assert load_masked(root, "a").abstracted is True

    result = evaluate(root, ["b"], pb, roles, mask_config=config)
    assert result["n"] == 1
    assert len(abstractor.calls) == 6
    assert load_masked(root, "b").abstracted is True

    prefixes = [c["prefix"] for c in roles.analyst.calls]
    assert prefixes and all("Revenue rose 12%" in p for p in prefixes)
    assert not any("record quarter" in p for p in prefixes)


def test_saved_masking_is_reused_and_a_mismatch_is_logged(tmp_path, caplog):
    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    evaluate(root, ["a"], tmp_path / "pb.json", make_roles())
    assert load_masked(root, "a").abstracted is False
    abstractor = abstractor_fake()
    with caplog.at_level(logging.WARNING, logger="researchos"):
        result = evaluate(root, ["a"], tmp_path / "pb.json", make_roles(abstractor=abstractor),
                          mask_config=MaskConfig(abstract=True))
    assert result["n"] == 1
    assert not abstractor.calls
    assert any("without abstraction" in r.getMessage() for r in caplog.records)


# ---- CLI -----------------------------------------------------------------------------

def test_cli_mask_abstract_is_passed_on(tmp_path, capsys, monkeypatch):
    import researchos.__main__ as main_mod

    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    seen = []

    def fake_run(root_, ids, playbook, roles, seed=0, mask_config=None, knowledge_cutoff=None):
        seen.append((mask_config, roles.abstractor))
        return {"n": 0}

    monkeypatch.setattr(main_mod, "train", fake_run)
    monkeypatch.setattr(main_mod, "evaluate", fake_run)
    base = ["--root", str(root), "--playbook", str(tmp_path / "pb.json"), "--ids", "a"]
    for argv in (["train", *base, "--mask-abstract"],
                 ["evaluate", *base, "--mask-abstract", "--mask-give-up", "0.9"],
                 ["train", *base, "--mask-give-up", "0.9"],
                 ["evaluate", *base]):
        assert main(argv, client_factory=schema_client) == 0
        assert json.loads(capsys.readouterr().out) == {"n": 0}
    assert seen[0][0] == MaskConfig(abstract=True)
    assert seen[1][0] == MaskConfig(give_up_confidence=0.9, abstract=True)
    assert isinstance(seen[0][1], ClaudeJSON) and isinstance(seen[1][1], ClaudeJSON)
    assert seen[2] == (MaskConfig(give_up_confidence=0.9), None)
    assert seen[3] == (None, None)


def test_cli_mask_abstract_runs_the_abstractor_at_high_effort(tmp_path, capsys):
    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    client = schema_client()
    code = main(["evaluate", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a", "--mask-abstract"], client_factory=lambda: client)
    assert code == 0
    assert json.loads(capsys.readouterr().out)["n"] == 1
    abstract_requests = [r for r in client.requests
                         if "brief" in r["output_config"]["format"]["schema"]["properties"]]
    assert len(abstract_requests) == 3
    for r in abstract_requests:
        assert r["output_config"]["effort"] == "high"
        assert r["system"] == ABSTRACT_SYSTEM
    masked = json.loads((root / "a" / "masked.json").read_text(encoding="utf-8"))
    assert masked["abstracted"] is True
    assert [d["title"] for d in masked["documents"]] == [f"{kind} brief" for kind in KINDS]


def test_cli_without_mask_abstract_never_abstracts(tmp_path, capsys):
    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    client = schema_client()
    assert main(["evaluate", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a"], client_factory=lambda: client) == 0
    capsys.readouterr()
    assert not any("brief" in r["output_config"]["format"]["schema"]["properties"]
                   for r in client.requests)
    masked = json.loads((root / "a" / "masked.json").read_text(encoding="utf-8"))
    assert masked["abstracted"] is False
