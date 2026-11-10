"""scripts/build_wheelhouse.py: the wheelhouse's dependencies pinned by hash
(D-002, OQ-20, D-164).

``--lock`` writes requirements.lock from the wheels downloaded; with a lock,
the download asks pip to check every wheel against it, and ``--verify`` fails
on a wheel the lock does not pin, a wheel whose bytes changed, and a pin with
no wheel. Nothing here downloads: the wheels are made-up files.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_wheelhouse.py"
_spec = importlib.util.spec_from_file_location("build_wheelhouse_script", SCRIPT)
wh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wh)

RICH = "rich-14.3.4-py3-none-any.whl"
MD = "markdown_it_py-4.2.0-py3-none-any.whl"
FOUNDRY = "foundry-0.1.0-py3-none-any.whl"


def _wheelhouse(tmp_path, *names):
    house = tmp_path / "wheelhouse"
    house.mkdir(exist_ok=True)
    for name in names:
        (house / name).write_bytes(f"the bytes of {name}".encode())
    return house


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_lock_pins_each_dependency_by_version_and_hash(tmp_path):
    house = _wheelhouse(tmp_path, RICH, MD, FOUNDRY)
    lock = tmp_path / "requirements.lock"
    assert wh.write_lock(house, lock) == 2
    text = lock.read_text(encoding="utf-8")
    assert (f"markdown-it-py==4.2.0 \\\n    --hash=sha256:{_digest(house / MD)}\n"
            in text)
    assert f"rich==14.3.4 \\\n    --hash=sha256:{_digest(house / RICH)}\n" in text
    assert "foundry==" not in text                  # built here, not downloaded
    assert wh.read_lock(lock) == {("markdown-it-py", "4.2.0"): {_digest(house / MD)},
                                  ("rich", "14.3.4"): {_digest(house / RICH)}}
    assert wh.verify(house, lock) == 0


def test_a_lock_with_crlf_line_ends_reads_the_same(tmp_path):
    house = _wheelhouse(tmp_path, RICH, FOUNDRY)
    lock = tmp_path / "requirements.lock"
    wh.write_lock(house, lock)
    lock.write_bytes(lock.read_bytes().replace(b"\n", b"\r\n"))   # a Windows checkout
    assert wh.read_lock(lock) == {("rich", "14.3.4"): {_digest(house / RICH)}}


def test_no_lock_without_dependency_wheels(tmp_path):
    house = _wheelhouse(tmp_path, FOUNDRY)
    with pytest.raises(ValueError, match="no dependency wheels"):
        wh.write_lock(house, tmp_path / "requirements.lock")


def test_no_lock_from_two_versions_of_one_project(tmp_path):
    house = _wheelhouse(tmp_path, RICH, "rich-13.9.0-py3-none-any.whl")
    with pytest.raises(ValueError, match="more than one version of rich"):
        wh.write_lock(house, tmp_path / "requirements.lock")


def test_verify_fails_on_a_wheel_whose_bytes_changed(tmp_path, capsys):
    house = _wheelhouse(tmp_path, RICH, MD, FOUNDRY)
    lock = tmp_path / "requirements.lock"
    wh.write_lock(house, lock)
    (house / RICH).write_bytes(b"something else")
    assert wh.verify(house, lock) == 1
    assert f"{RICH} is not the wheel requirements.lock pins: its sha256 differs" in \
        capsys.readouterr().out


def test_verify_fails_on_a_wheel_the_lock_does_not_pin(tmp_path, capsys):
    house = _wheelhouse(tmp_path, RICH, FOUNDRY)
    lock = tmp_path / "requirements.lock"
    wh.write_lock(house, lock)
    _wheelhouse(tmp_path, MD)
    assert wh.verify(house, lock) == 1
    assert f"{MD} is not pinned in requirements.lock" in capsys.readouterr().out


def test_verify_fails_on_a_pin_with_no_wheel(tmp_path, capsys):
    house = _wheelhouse(tmp_path, RICH, MD, FOUNDRY)
    lock = tmp_path / "requirements.lock"
    wh.write_lock(house, lock)
    (house / MD).unlink()
    assert wh.verify(house, lock) == 1
    assert ("requirements.lock pins markdown-it-py==4.2.0; no wheel of it is in the "
            "wheelhouse") in capsys.readouterr().out


@pytest.mark.parametrize("line", [
    "rich>=13.7 --hash=sha256:" + "0" * 64,          # not pinned to a version
    "rich==14.3.4",                                   # no hash
    "rich==14.3.4 --hash=md5:" + "0" * 32,            # not sha256
])
def test_a_lock_pip_would_refuse_is_refused(tmp_path, line, capsys):
    house = _wheelhouse(tmp_path, RICH, FOUNDRY)
    lock = tmp_path / "requirements.lock"
    lock.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="is not name==version with --hash=sha256"):
        wh.read_lock(lock)
    assert wh.verify(house, lock) == 1


def test_without_a_lock_verify_passes_and_says_it_is_not_pinned(tmp_path, capsys):
    house = _wheelhouse(tmp_path, RICH, FOUNDRY)
    assert wh.verify(house, tmp_path / "requirements.lock") == 0
    out = capsys.readouterr().out
    assert "not pinned by hash: requirements.lock does not exist (OQ-20)" in out
    assert "every dependency pinned by hash" not in out


def test_the_download_checks_hashes_only_with_a_lock():
    pinned = wh.download_argv(True, platform=True)
    assert pinned[pinned.index("-r") + 1] == "requirements.lock"
    assert "--require-hashes" in pinned and "--only-binary" in pinned
    loose = wh.download_argv(False, platform=False)
    assert loose[loose.index("-r") + 1] == "requirements.in"
    assert "--require-hashes" not in loose and "--platform" not in loose


def test_lock_mode_writes_and_verifies(tmp_path, monkeypatch, capsys):
    house = _wheelhouse(tmp_path, RICH, MD, FOUNDRY)
    monkeypatch.setattr(wh, "WHEELHOUSE", house)
    monkeypatch.setattr(wh, "LOCK", tmp_path / "requirements.lock")
    assert wh.main(["--lock"]) == 0
    out = capsys.readouterr().out
    assert "requirements.lock pins 2 projects; read it, then commit it" in out
    assert "installable offline, every dependency pinned by hash" in out
    assert (tmp_path / "requirements.lock").is_file()


def test_lock_mode_with_an_empty_wheelhouse_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wh, "WHEELHOUSE", _wheelhouse(tmp_path))
    monkeypatch.setattr(wh, "LOCK", tmp_path / "requirements.lock")
    assert wh.main(["--lock"]) == 1
    assert "no dependency wheels" in capsys.readouterr().out
    assert not (tmp_path / "requirements.lock").exists()
