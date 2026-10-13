import json

from conftest import (FakeClient, default_answer, make_case, make_outcome, schema_client,
                      text_response)

from researchos.__main__ import main
from researchos.cases import save_case
from researchos.playbook import load_playbook


def setup_root(tmp_path):
    root = tmp_path / "cases"
    save_case(root, make_case("a"), make_outcome(ar=0.02))
    save_case(root, make_case("b"), make_outcome(ar=-0.02))
    return root


def test_train_then_evaluate(tmp_path, capsys):
    root = setup_root(tmp_path)
    pb = tmp_path / "pb.json"
    client = schema_client()
    code = main(["train", "--root", str(root), "--playbook", str(pb), "--ids", "a,b",
                 "--seed", "4"], client_factory=lambda: client)
    out = capsys.readouterr().out
    assert code == 0
    scores = json.loads(out)
    assert [s["case_id"] for s in scores] == ["a", "b"]
    assert len(load_playbook(pb).history) == 2
    efforts = {r["output_config"]["effort"] for r in client.requests}
    assert efforts == {"medium", "high"}
    for r in client.requests:
        schema_props = set(r["output_config"]["format"]["schema"]["properties"])
        expected = "medium" if schema_props & {"redactions", "guesses"} else "high"
        assert r["output_config"]["effort"] == expected

    code = main(["evaluate", "--root", str(root), "--playbook", str(pb), "--ids", "a"],
                client_factory=schema_client)
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["n"] == 1


def test_knowledge_cutoff_is_passed_on(tmp_path, capsys, monkeypatch):
    import researchos.__main__ as main_mod

    root = setup_root(tmp_path)
    seen = []

    def fake_run(root_, ids, playbook, roles, seed=0, knowledge_cutoff=None):
        seen.append(knowledge_cutoff)
        return {"n": 0}

    monkeypatch.setattr(main_mod, "train", fake_run)
    monkeypatch.setattr(main_mod, "evaluate", fake_run)
    for cmd in ("train", "evaluate"):
        assert main([cmd, "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                     "--ids", "a", "--knowledge-cutoff", "2026-01-31"],
                    client_factory=schema_client) == 0
        assert json.loads(capsys.readouterr().out) == {"n": 0}
    assert main(["train", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a"], client_factory=schema_client) == 0
    assert seen == ["2026-01-31", "2026-01-31", None]


def test_knowledge_cutoff_scores_identifiable_case(tmp_path, capsys):
    root = setup_root(tmp_path)  # event_date 2026-02-01

    def responder(kwargs):
        schema = kwargs["output_config"]["format"]["schema"]
        prompt = kwargs["messages"][0]["content"][-1]["text"]
        if "guesses" in schema["properties"]:
            body = {"guesses": [{"company": "Acme", "ticker": "ACMW", "confidence": 1.0}],
                    "clues": []}
        else:
            body = default_answer(schema, prompt)
        return text_response(json.dumps(body))

    code = main(["evaluate", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a,b", "--knowledge-cutoff", "2026-01-01"],
                client_factory=lambda: FakeClient(responder))
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["n"] == 2 and result["n_identifiable"] == 2
    assert all(c["identifiable"] is True for c in result["cases"])


def test_bad_arguments_exit_2(tmp_path, capsys):
    assert main([]) == 2
    assert main(["fly", "--root", "x", "--playbook", "p", "--ids", "a"]) == 2
    assert main(["train", "--root", "x", "--playbook", "p"]) == 2
    assert main(["train", "--root", "x", "--playbook", "p", "--ids", "a",
                 "--seed", "nope"]) == 2
    for bad in ("nope", "2026-02-30", "01/02/2026", ""):
        assert main(["evaluate", "--root", "x", "--playbook", "p", "--ids", "a",
                     "--knowledge-cutoff", bad]) == 2
    assert main(["train", "--root", "x", "--playbook", "p", "--ids", " , "]) == 2
    assert capsys.readouterr().out == ""


def test_missing_case_exits_1(tmp_path, capsys):
    root = setup_root(tmp_path)
    called = []
    code = main(["evaluate", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a,zzz"], client_factory=lambda: called.append(1))
    captured = capsys.readouterr()
    assert code == 1 and "zzz" in captured.err and captured.out == ""


def test_llm_error_exits_1(tmp_path, capsys):
    root = setup_root(tmp_path)
    client = FakeClient(lambda kw: text_response("{}", stop_reason="refusal"))
    code = main(["train", "--root", str(root), "--playbook", str(tmp_path / "pb.json"),
                 "--ids", "a"], client_factory=lambda: client)
    captured = capsys.readouterr()
    assert code == 1 and "refused" in captured.err and captured.out == ""
