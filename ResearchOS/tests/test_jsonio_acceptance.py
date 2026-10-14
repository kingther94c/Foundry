"""Acceptance tests for researchos.jsonio and --out, written from SPEC.md independently of the code
(held back from the agent that built it).

Each case traces to the "researchos.jsonio" section of SPEC.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

import pytest

from researchos import cases, jsonio, masking, playbook, sources
from researchos.cases import Document
from researchos.compare import compare, load_report

DATA = {"name": "İstanbul Ünlü € 日本", "values": [1, 2.5, None, True, "a\nb"], "nested": {"k": []}}

ENCODINGS = {
    "utf8": lambda s: s.encode("utf-8"),
    "utf8-bom": lambda s: b"\xef\xbb\xbf" + s.encode("utf-8"),
    "utf16-le-bom": lambda s: b"\xff\xfe" + s.encode("utf-16-le"),
    "utf16-be-bom": lambda s: b"\xfe\xff" + s.encode("utf-16-be"),
    "utf16-python": lambda s: s.encode("utf-16"),  # a mark in the machine's byte order
    "powershell-redirect": lambda s: b"\xff\xfe" + (s.replace("\n", "\r\n") + "\r\n").encode("utf-16-le"),
}


def as_powershell(path):
    """Rewrite a UTF-8 file the way Windows PowerShell 5.1's `>` would have written it."""
    text = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    path.write_bytes(ENCODINGS["powershell-redirect"](text))


def with_utf8_bom(path):
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


@pytest.mark.parametrize("encode", ENCODINGS.values(), ids=ENCODINGS.keys())
def test_read_json_decodes_by_byte_order_mark(tmp_path, encode):
    p = tmp_path / "f.json"
    p.write_bytes(encode(json.dumps(DATA, indent=2, ensure_ascii=False)))
    assert jsonio.read_json(p) == DATA
    assert jsonio.read_json(str(p)) == DATA


@pytest.mark.parametrize("raw", [
    "{}".encode("utf-16-le"),                # UTF-16 without a mark is read as UTF-8: not JSON
    '{"a": "é"}'.encode("latin-1"),          # not UTF-8
    b"\xff\xfe{",                            # a UTF-16 mark, then an odd number of bytes
    b"\xef\xbb\xbf{not json",
    b"\xef\xbb\xbf",
    b"",
], ids=["utf16-no-mark", "latin1", "truncated-utf16", "bom-not-json", "bom-only", "empty"])
def test_read_json_rejects_with_the_path(tmp_path, raw):
    p = tmp_path / "bad-file.json"
    p.write_bytes(raw)
    with pytest.raises(ValueError) as info:
        jsonio.read_json(p)
    assert "bad-file.json" in str(info.value)


def test_read_json_lets_os_errors_through(tmp_path):
    with pytest.raises(FileNotFoundError):
        jsonio.read_json(tmp_path / "missing.json")
    with pytest.raises(OSError):
        jsonio.read_json(tmp_path)


def test_write_json(tmp_path):
    p = tmp_path / "out.json"
    jsonio.write_json(p, DATA)
    raw = p.read_bytes()
    assert raw[:1] == b"{"  # no byte order mark
    assert raw.decode("utf-8").replace("\r\n", "\n") == json.dumps(DATA, indent=2, ensure_ascii=False) + "\n"
    assert jsonio.read_json(p) == DATA
    jsonio.write_json(str(p), [1])
    assert jsonio.read_json(p) == [1]


# --- every reader goes through read_json ----------------------------------------

def test_load_closes_and_case_spec(tmp_path):
    closes = tmp_path / "closes.json"
    closes.write_text(json.dumps([["2026-07-17", 92.0], ["2026-07-16", 100.0]]), encoding="utf-8")
    as_powershell(closes)
    assert sources.load_closes(closes) == [(date(2026, 7, 16), 100.0), (date(2026, 7, 17), 92.0)]
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"case_id": "acme-2026q2", "company": "Acme Corp", "ticker": "ACME",
                                "aliases": ["Acme"], "fiscal_period": "Q2 2026",
                                "report_date": "2026-07-16", "timing": "amc"}), encoding="utf-8")
    with_utf8_bom(spec)
    assert sources.load_case_spec(spec).case_id == "acme-2026q2"
    # The right encoding, the wrong shape: still the error it always was.
    closes.write_bytes(b"\xff\xfe" + '{"a": 1}'.encode("utf-16-le"))
    with pytest.raises(ValueError):
        sources.load_closes(closes)


def test_load_report_and_playbook(tmp_path):
    report = {"n": 1, "cases": [{"case_id": "x", "brier_judge": 0.2}]}
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"result": report}, indent=2), encoding="utf-8")
    as_powershell(p)
    assert load_report(p) == report
    p.write_bytes(b"\xff\xfe" + '{"n": 1}'.encode("utf-16-le"))
    with pytest.raises(ValueError):
        load_report(p)
    book = playbook.Playbook.initial()
    book.lessons.append(playbook.Lesson("Ünlü lesson", "when", "growth", "c1"))
    pb = tmp_path / "pb.json"
    playbook.save_playbook(pb, book)
    with_utf8_bom(pb)
    assert asdict(playbook.load_playbook(pb)) == asdict(book)
    as_powershell(pb)
    assert asdict(playbook.load_playbook(pb)) == asdict(book)


def test_load_case_outcome_and_masked(tmp_path):
    from test_acceptance import OUTCOME, make_case
    case = make_case("c1")
    cases.save_case(tmp_path, case, OUTCOME)
    for name in (cases.CASE_FILE, cases.OUTCOME_FILE):
        as_powershell(tmp_path / "c1" / name)
    assert cases.load_case(tmp_path, "c1") == case
    assert cases.load_outcome(tmp_path, "c1") == OUTCOME
    masked = masking.MaskedCase(case_id="c1", documents=[Document("release", "T", "Company A grew.", "")],
                                status="masked", rounds=2, mapping={"Zephyrex": "Company A"},
                                factor=1.7, year_offset=-3,
                                final_guesses=[{"company": "Nobody", "ticker": "", "confidence": 0.1}])
    masking.save_masked(tmp_path, masked)
    as_powershell(tmp_path / "c1" / masking.MASKED_FILE)
    with_utf8_bom(tmp_path / "c1" / masking.MASK_KEY_FILE)
    assert asdict(masking.load_masked(tmp_path, "c1")) == asdict(masked)


# --- the CLI's --out ------------------------------------------------------------

A = {"n": 2, "cases": [{"case_id": "c1", "brier_judge": 0.2}, {"case_id": "c2", "brier_judge": 0.4}]}
B = {"n": 2, "cases": [{"case_id": "c2", "brier_judge": 0.1}, {"case_id": "c1", "brier_judge": 0.3}]}


def test_cli_compare_out(tmp_path, capsys):
    from researchos.__main__ import main
    a, b, out = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "cmp.json"
    a.write_text(json.dumps({"result": A}, indent=2), encoding="utf-8")
    as_powershell(a)
    b.write_text(json.dumps(B), encoding="utf-8")
    with_utf8_bom(b)
    assert main(["compare", str(a), str(b), "--resamples", "50", "--out", str(out)]) == 0
    assert capsys.readouterr().out == ""
    assert out.read_bytes()[:1] == b"{"
    assert jsonio.read_json(out) == json.loads(json.dumps(compare(A, B, resamples=50)))
    assert main(["compare", str(a), str(b), "--resamples", "50"]) == 0
    assert json.loads(capsys.readouterr().out) == jsonio.read_json(out)
    assert main(["compare", str(a), str(b), "--out", str(tmp_path / "no" / "such" / "dir" / "x.json")]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()


def test_cli_train_and_evaluate_out(tmp_path, capsys):
    from researchos.__main__ import main
    from test_acceptance import OUTCOME, ScriptedClient, make_case
    cases.save_case(tmp_path, make_case("c1"), OUTCOME)
    # c2 reacted after c1 (2026-07-30): a playbook trained on c1 may be evaluated on it
    c2 = make_case("c2")
    c2.event_date = "2026-10-29"
    cases.save_case(tmp_path, c2, cases.Outcome(abnormal_return_1d=-0.03))
    pb, out = tmp_path / "pb.json", tmp_path / "train-out.json"
    assert main(["train", "--root", str(tmp_path), "--playbook", str(pb), "--ids", "c1",
                 "--out", str(out)], client_factory=ScriptedClient) == 0
    assert capsys.readouterr().out == ""
    trained = jsonio.read_json(out)
    assert [r["case_id"] for r in trained] == ["c1"] and trained[0]["actual_up"] is True
    as_powershell(pb)  # the playbook, opened and saved again on Windows
    out = tmp_path / "eval-out.json"
    assert main(["evaluate", "--root", str(tmp_path), "--playbook", str(pb), "--ids", "c2",
                 "--out", str(out)], client_factory=ScriptedClient) == 0
    assert capsys.readouterr().out == ""
    assert jsonio.read_json(out)["n"] == 1
    code = main(["train", "--root", str(tmp_path), "--playbook", str(tmp_path / "pb2.json"),
                 "--ids", "c2", "--out", str(tmp_path)], client_factory=ScriptedClient)
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip()
    assert (tmp_path / "pb2.json").is_file()  # what the command did before failing stands


def test_cli_build_out(tmp_path, capsys):
    from researchos.__main__ import main
    from test_sources_acceptance import MARKET, STOCK, ScriptedClient
    spec, closes, market = tmp_path / "spec.json", tmp_path / "s.json", tmp_path / "m.json"
    spec.write_text(json.dumps({"case_id": "acme-2026q2", "company": "Acme Corp", "ticker": "ACME",
                                "aliases": ["Acme"], "fiscal_period": "Q2 2026",
                                "report_date": "2026-07-16", "timing": "amc"}), encoding="utf-8")
    closes.write_text(json.dumps([[d.isoformat(), c] for d, c in STOCK]), encoding="utf-8")
    market.write_text(json.dumps([[d.isoformat(), c] for d, c in MARKET]), encoding="utf-8")
    for p in (spec, closes, market):
        as_powershell(p)
    out = tmp_path / "built.json"
    root = tmp_path / "cases"
    assert main(["build", "--root", str(root), "--spec", str(spec), "--closes", str(closes),
                 "--market-closes", str(market), "--out", str(out)], client_factory=ScriptedClient) == 0
    assert capsys.readouterr().out == ""
    built = jsonio.read_json(out)
    assert built["case_id"] == "acme-2026q2"
    assert built["outcome"]["abnormal_return_1d"] == pytest.approx(cases.load_outcome(root, "acme-2026q2").abnormal_return_1d)
