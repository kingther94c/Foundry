"""The macro run scores no snapshot whose case does not match inputs.sha256 (PLAN.md).

runs/2026-11-macro-calibration-run keeps its cases out of git; a later scoring restores
them from a private copy or rebuilds them at the same commit, and PLAN.md says a rebuilt
snapshot that does not match inputs.sha256 is not scored. `forecast_arm.py restore`
stopped at the first that did not match, so one snapshot whose series had posted since
would have kept every other from being scored. Now restore leaves it out and names it,
`secondary.py` leaves it out of both arms and lists it under not_scored, and `setup`,
which makes the arms, still takes all of the snapshots or none. The scripts are loaded by
path and pointed at a temporary copy of the run's layout.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-macro-calibration-run"
IDS = ("macro-2026-07-03", "macro-2026-07-10", "macro-2026-07-17")


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


forecast_arm = _load("forecast_arm")
secondary = _load("secondary")


def _case(case_id, version=""):
    return json.dumps({"id": case_id, "as_of": case_id[6:], "v": version}).encode()


@pytest.fixture()
def run(tmp_path, monkeypatch):
    """The run's layout in tmp_path: cases/ as built, inputs.sha256 for them, and arms
    holding only the committed prediction.json of each snapshot, as a fresh clone has."""
    monkeypatch.setattr(forecast_arm, "HERE", tmp_path)
    monkeypatch.setattr(secondary, "HERE", tmp_path)
    lines = []
    for case_id in IDS:
        folder = tmp_path / "cases" / case_id
        folder.mkdir(parents=True)
        (folder / "case.json").write_bytes(_case(case_id))
        (folder / "documents.json").write_text("[]", encoding="utf-8")
        lines.append(f"{hashlib.sha256(_case(case_id)).hexdigest()}  cases/{case_id}/case.json")
        for arm in forecast_arm.ARMS:
            sealed = tmp_path / f"arm-{arm}" / case_id
            sealed.mkdir(parents=True)
            (sealed / "prediction.json").write_text(f'{{"arm": "{arm}"}}', encoding="utf-8")
    (tmp_path / "inputs.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def _restored(run, arm, case_id):
    return (run / f"arm-{arm}" / case_id / "case.json").exists()


def test_restore_leaves_out_a_snapshot_that_does_not_match(run, capsys):
    (run / "cases" / IDS[1] / "case.json").write_bytes(_case(IDS[1], "posted since"))
    predictions = {p: p.read_bytes() for p in run.glob("arm-*/*/prediction.json")}

    assert forecast_arm.restore() == 0

    for arm in forecast_arm.ARMS:
        assert _restored(run, arm, IDS[0]) and _restored(run, arm, IDS[2])
        assert (run / f"arm-{arm}" / IDS[0] / "documents.json").exists()
        assert not _restored(run, arm, IDS[1])
    assert {p: p.read_bytes() for p in run.glob("arm-*/*/prediction.json")} == predictions
    out = capsys.readouterr().out
    assert "8 files restored into arm-holistic and arm-isolated" in out
    assert f"not restored, as it does not match inputs.sha256, and so not scored: {IDS[1]}" in out


def test_restore_leaves_out_a_snapshot_not_rebuilt(run, capsys):
    for path in (run / "cases" / IDS[2]).iterdir():
        path.unlink()
    (run / "cases" / IDS[2]).rmdir()

    assert forecast_arm.restore() == 0

    assert all(_restored(run, arm, IDS[0]) for arm in forecast_arm.ARMS)
    assert not any(_restored(run, arm, IDS[2]) for arm in forecast_arm.ARMS)
    assert f"and so not scored: {IDS[2]}" in capsys.readouterr().out


def test_restore_never_overwrites_an_arm_s_case_that_differs(run, capsys):
    wrong = run / "arm-holistic" / IDS[0] / "case.json"
    wrong.write_bytes(_case(IDS[0], "edited"))

    assert forecast_arm.restore() == 0

    assert wrong.read_bytes() == _case(IDS[0], "edited")
    # Left out of both arms, so the two are scored on the same snapshots.
    assert not _restored(run, "isolated", IDS[0])
    assert all(_restored(run, arm, IDS[1]) for arm in forecast_arm.ARMS)
    assert f"and so not scored: {IDS[0]}" in capsys.readouterr().out


def test_restored_arms_need_no_copy_again(run, capsys):
    forecast_arm.restore()
    for case_id in IDS:
        (run / "cases" / case_id / "case.json").write_bytes(_case(case_id, "lost and rebuilt"))
    capsys.readouterr()

    assert forecast_arm.restore() == 0

    out = capsys.readouterr().out
    assert out.startswith("0 files restored") and "not scored" not in out


def test_setup_still_takes_every_snapshot_or_none(run):
    for arm in forecast_arm.ARMS:
        for path in sorted((run / f"arm-{arm}").rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (run / f"arm-{arm}").rmdir()
    (run / "cases" / IDS[1] / "case.json").write_bytes(_case(IDS[1], "posted since"))

    with pytest.raises(SystemExit, match=f"cases/{IDS[1]}/case.json does not match inputs"):
        forecast_arm.setup()

    assert not any((run / f"arm-{arm}").exists() for arm in forecast_arm.ARMS)


def test_the_scoring_leaves_a_snapshot_out_of_both_arms(run, monkeypatch, capsys):
    forecast_arm.restore()
    (run / "arm-isolated" / IDS[1] / "case.json").write_bytes(_case(IDS[1], "posted since"))
    (run / "arm-isolated" / IDS[2] / "prediction.json").unlink()   # failed in that arm

    assert secondary.scoring_sets() == ({"holistic": [IDS[0], IDS[2]], "isolated": [IDS[0]]},
                                        [IDS[1]])

    # The whole scoring, with nothing fetched and each command recorded.
    calls = []

    def cli(args):
        calls.append(args)
        if args[0] == "score":
            Path(args[args.index("--out") + 1]).write_text(
                json.dumps({"n": 0, "pending": [], "cases": []}), encoding="utf-8")
        return {}

    monkeypatch.setattr(secondary, "_cli", cli)
    monkeypatch.setattr(secondary, "finished", lambda arm: True)
    monkeypatch.setattr(secondary.prepare, "fetch_fred", lambda *a, **k: None)

    assert secondary.main() == 0

    today = datetime.now(timezone.utc).date().isoformat()
    written = json.loads((run / f"secondary-{today}.json").read_text(encoding="utf-8"))
    assert written["not_scored"] == {
        "snapshots": [IDS[1]], "reason": "case.json does not match inputs.sha256 (PLAN.md)"}
    assert written["arms"]["holistic"]["snapshots"] == 2
    assert written["arms"]["isolated"]["snapshots"] == 1
    scored = {(c[0], c[2]): c[c.index("--ids") + 1] for c in calls if c[0] in ("resolve", "score")}
    assert scored == {("resolve", str(run / "scores" / today / "arm-holistic")):
                      f"{IDS[0]},{IDS[2]}",
                      ("score", str(run / "scores" / today / "arm-holistic")):
                      f"{IDS[0]},{IDS[2]}",
                      ("resolve", str(run / "scores" / today / "arm-isolated")): IDS[0],
                      ("score", str(run / "scores" / today / "arm-isolated")): IDS[0]}
    assert f"not scored, as not matching inputs.sha256: {IDS[1]}" in capsys.readouterr().out
