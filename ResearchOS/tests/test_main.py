import json

from conftest import FakeClient, make_case, make_outcome, schema_client, text_response

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


def test_bad_arguments_exit_2(tmp_path, capsys):
    assert main([]) == 2
    assert main(["fly", "--root", "x", "--playbook", "p", "--ids", "a"]) == 2
    assert main(["train", "--root", "x", "--playbook", "p"]) == 2
    assert main(["train", "--root", "x", "--playbook", "p", "--ids", "a",
                 "--seed", "nope"]) == 2
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
