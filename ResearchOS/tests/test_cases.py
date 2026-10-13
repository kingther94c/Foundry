import builtins
import json
from pathlib import Path

from conftest import make_case, make_outcome

from researchos.cases import (Case, identity_terms, list_cases, load_case, load_outcome,
                              save_case)


def test_save_and_load_round_trip(tmp_path):
    case, outcome = make_case(), make_outcome()
    save_case(tmp_path, case, outcome)
    assert load_case(tmp_path, case.case_id) == case
    assert load_outcome(tmp_path, case.case_id) == outcome
    raw = (tmp_path / case.case_id / "case.json").read_text(encoding="utf-8")
    assert raw.startswith("{\n  ")  # indent=2
    assert "abnormal_return_1d" not in raw
    assert json.loads(raw)["documents"][0]["kind"] == "release"


def test_load_case_never_opens_outcome(tmp_path, monkeypatch):
    case = make_case()
    save_case(tmp_path, case, make_outcome())
    opened = []
    real_read_text = Path.read_text
    real_open = Path.open

    def read_text(self, *a, **k):
        opened.append(self.name)
        return real_read_text(self, *a, **k)

    def path_open(self, *a, **k):
        opened.append(self.name)
        return real_open(self, *a, **k)

    real_builtin_open = builtins.open

    def builtin_open(file, *a, **k):
        opened.append(Path(str(file)).name)
        return real_builtin_open(file, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(builtins, "open", builtin_open)
    assert load_case(tmp_path, case.case_id).company == case.company
    monkeypatch.undo()
    assert "case.json" in opened
    assert "outcome.json" not in opened


def test_list_cases(tmp_path):
    for cid in ["b", "a", "c"]:
        save_case(tmp_path, make_case(cid), make_outcome())
    (tmp_path / "not-a-case").mkdir()
    (tmp_path / "stray.json").write_text("{}", encoding="utf-8")
    assert list_cases(tmp_path) == ["a", "b", "c"]
    assert list_cases(tmp_path / "missing") == []


def test_identity_terms_order_and_dedupe():
    case = Case("x", "Acme Corp", "ACME", ["acme corp", "", "Acme", "Bob", "ACME", "bob"],
                "Q1", "2026-01-01", [])
    assert identity_terms(case) == ["Acme Corp", "ACME", "Bob"]
    assert identity_terms(make_case()) == ["Acme Widgets Inc.", "ACMW", "Acme", "Jane Roe",
                                           "WidgetPro"]
