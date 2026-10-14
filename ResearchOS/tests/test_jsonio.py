import codecs
import json
from datetime import date

import pytest

from conftest import make_case, make_outcome

from researchos.cases import CASE_FILE, OUTCOME_FILE, load_case, load_outcome, save_case
from researchos.compare import load_report
from researchos.jsonio import read_json, write_json
from researchos.masking import MASK_KEY_FILE, MASKED_FILE, MaskedCase, load_masked, save_masked
from researchos.playbook import Lesson, Playbook, load_playbook, save_playbook
from researchos.sources import CaseSpec, load_case_spec, load_closes

# Non-ASCII on purpose, including a character outside the BMP (a UTF-16 surrogate pair).
DATA = {"company": "Soci\xe9t\xe9 G\xe9n\xe9rale \N{EN DASH} \xc9tats", "symbol": "\U0001F4C8",
        "closes": [["2026-07-16", 1234.5], ["2026-07-17", -0.25]], "ok": True, "none": None,
        "nested": {"empty": [], "n": 3}}
TEXT = json.dumps(DATA, indent=2, ensure_ascii=False)

# The byte order marks Windows writes, with the codec of the bytes behind each.
MARKED = [
    pytest.param(codecs.BOM_UTF8, "utf-8", id="utf-8-bom"),
    pytest.param(codecs.BOM_UTF16_LE, "utf-16-le", id="utf-16-le-bom"),
    pytest.param(codecs.BOM_UTF16_BE, "utf-16-be", id="utf-16-be-bom"),
]


def reencode(path, mark, codec):
    """Rewrite the UTF-8 file at path in codec, behind mark."""
    path.write_bytes(mark + path.read_bytes().decode("utf-8").encode(codec))


# ---- read_json

def test_read_json_plain_utf8(tmp_path):
    path = tmp_path / "data.json"
    path.write_bytes(TEXT.encode("utf-8"))
    assert read_json(path) == DATA
    assert read_json(str(path)) == DATA


@pytest.mark.parametrize("mark, codec", MARKED)
def test_read_json_decodes_by_byte_order_mark(tmp_path, mark, codec):
    path = tmp_path / "data.json"
    path.write_bytes(mark + TEXT.encode(codec))
    assert read_json(path) == DATA
    assert read_json(str(path)) == DATA


def test_read_json_reads_what_windows_powershell_writes(tmp_path):
    path = tmp_path / "ps.json"
    crlf = TEXT.replace("\n", "\r\n") + "\r\n"
    path.write_bytes(codecs.BOM_UTF16_LE + crlf.encode("utf-16-le"))  # `... > ps.json`
    assert read_json(path) == DATA
    path.write_bytes(crlf.encode("utf-8-sig"))  # Out-File -Encoding utf8
    assert read_json(path) == DATA
    path.write_bytes(crlf.encode("utf-16"))  # native byte order behind its mark
    assert read_json(path) == DATA


@pytest.mark.parametrize("raw", [
    pytest.param(b"", id="empty"),
    pytest.param(b" \r\n\t", id="whitespace"),
    pytest.param(codecs.BOM_UTF8, id="utf-8-mark-only"),
    pytest.param(codecs.BOM_UTF16_LE, id="utf-16-le-mark-only"),
    pytest.param(codecs.BOM_UTF16_BE, id="utf-16-be-mark-only"),
    pytest.param(b"{not json", id="not-json"),
    pytest.param(b'{"a": 1} {"b": 2}', id="extra-data"),
    pytest.param('{"a": "Soci\xe9t\xe9"}'.encode("cp1252"), id="cp1252"),
    pytest.param(codecs.BOM_UTF8 + '{"a": "\xe9"}'.encode("cp1252"), id="utf-8-mark-cp1252"),
    pytest.param(codecs.BOM_UTF16_LE + b"{\x00}", id="utf-16-odd-length"),
    pytest.param('{"a": 1}'.encode("utf-16-le"), id="utf-16-without-mark"),
    pytest.param(codecs.BOM_UTF16_LE + '{"a": 1}'.encode("utf-16-be"), id="utf-16-wrong-order"),
    pytest.param(codecs.BOM_UTF8 + codecs.BOM_UTF8 + b"{}", id="two-marks"),
])
def test_read_json_rejects_undecodable_or_non_json_naming_the_path(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError) as info:
        read_json(path)
    assert str(path) in str(info.value)


def test_read_json_lets_os_errors_through(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "missing.json")
    with pytest.raises(OSError):  # IsADirectoryError, or PermissionError on Windows
        read_json(tmp_path)


# ---- write_json

def test_write_json_writes_indented_utf8_without_a_mark(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, DATA)
    assert path.read_bytes() == (TEXT + "\n").encode("utf-8")
    assert read_json(path) == DATA
    write_json(str(path), [1, "\xe9"])  # replaces the file; a str path works too
    assert path.read_bytes() == b'[\n  1,\n  "\xc3\xa9"\n]\n'


def test_write_json_failures_leave_an_existing_file_alone(tmp_path):
    with pytest.raises(OSError):
        write_json(tmp_path, {})  # a directory
    path = tmp_path / "keep.json"
    write_json(path, {"a": 1})
    before = path.read_bytes()
    with pytest.raises(ValueError):  # a lone surrogate has no UTF-8 encoding
        write_json(path, {"a": "\ud800"})
    with pytest.raises(TypeError):
        write_json(path, {"a": object()})
    assert path.read_bytes() == before


# ---- every JSON reader goes through read_json

@pytest.mark.parametrize("mark, codec", MARKED)
def test_every_loader_reads_windows_encodings(tmp_path, mark, codec):
    root = tmp_path / "cases"
    case, outcome = make_case("acme"), make_outcome()
    save_case(root, case, outcome)
    masked = MaskedCase(case_id="acme", documents=case.documents[:1], status="masked", rounds=2,
                        mapping={"Acme Widgets Inc.": "the Company"}, factor=1.5, year_offset=-4,
                        final_guesses=[{"company": "Globex", "ticker": "GBX", "confidence": 0.2}])
    save_masked(root, masked)
    playbook = Playbook(weights={"growth": 0.6, "risks": 0.4},
                        lessons=[Lesson("Guidance dominates \N{EN DASH} always.", "guidance is raised",
                                        "guidance", "acme")],
                        history=[{"case_id": "acme", "brier_judge": 0.1}])
    pb = tmp_path / "pb.json"
    save_playbook(pb, playbook)
    case_dir = root / "acme"
    for path in (case_dir / CASE_FILE, case_dir / OUTCOME_FILE, case_dir / MASKED_FILE,
                 case_dir / MASK_KEY_FILE, pb):
        reencode(path, mark, codec)
    assert load_case(root, "acme") == case
    assert load_outcome(root, "acme") == outcome
    assert load_masked(root, "acme") == masked
    assert load_playbook(pb) == playbook

    closes = tmp_path / "closes.json"
    closes.write_bytes(mark + '[["2026-07-16", 101.5], ["2026-07-15", 100]]'.encode(codec))
    assert load_closes(closes) == [(date(2026, 7, 15), 100.0), (date(2026, 7, 16), 101.5)]

    spec = {"case_id": "acme-q2", "company": "Acme \xc9tats", "ticker": "ACMW",
            "aliases": ["Acme"], "fiscal_period": "Q2 2026", "report_date": "2026-07-14",
            "timing": "amc"}
    spec_path = tmp_path / "spec.json"
    spec_path.write_bytes(mark + json.dumps(spec, ensure_ascii=False).encode(codec))
    assert load_case_spec(spec_path) == CaseSpec(**spec)

    rep = {"cases": [{"case_id": "acme", "brier_judge": 0.1}], "n": 1}
    rep_path = tmp_path / "report.json"
    rep_path.write_bytes(mark + json.dumps({"result": rep}).encode(codec))
    assert load_report(rep_path) == rep


def test_wrong_shape_still_raises_what_it_raised_before(tmp_path):
    def utf16(name, data):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(codecs.BOM_UTF16_LE + json.dumps(data).encode("utf-16-le"))
        return path

    with pytest.raises(ValueError, match="expected a JSON list"):
        load_closes(utf16("closes.json", {"2026-07-16": 1.0}))
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_case_spec(utf16("spec.json", ["acme"]))
    with pytest.raises(ValueError, match="not an evaluation report"):
        load_report(utf16("report.json", [1, 2]))
    utf16(f"cases/x/{CASE_FILE}", {"company": "Acme"})
    utf16(f"cases/x/{OUTCOME_FILE}", {"notes": ""})
    with pytest.raises(KeyError):
        load_case(tmp_path / "cases", "x")
    with pytest.raises(KeyError):
        load_outcome(tmp_path / "cases", "x")


def test_loaders_name_the_file_they_cannot_read(tmp_path):
    root = tmp_path / "cases"
    save_case(root, make_case("acme"), make_outcome())
    (root / "acme" / CASE_FILE).write_bytes(codecs.BOM_UTF16_LE + b"{\x00}")  # odd length
    with pytest.raises(ValueError, match="case.json"):
        load_case(root, "acme")
    closes = tmp_path / "closes.json"
    closes.write_bytes(b"")
    with pytest.raises(ValueError, match="closes.json"):
        load_closes(closes)
