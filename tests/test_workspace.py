"""Path containment: a table of the escapes this must refuse.

These are the cases that defeat a naive ``startswith`` check on Windows.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from foundry.core.workspace import PathRejected, Workspace


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    return Workspace(tmp_path)


def test_accepts_relative_paths(ws):
    resolved = ws.resolve("src/app.py")
    assert resolved.relative == "src/app.py"
    assert resolved.absolute.is_file()


def test_accepts_backslash_separator(ws):
    assert ws.resolve("src\\app.py").relative == "src/app.py"


def test_accepts_not_yet_existing_file(ws):
    assert ws.resolve("src/new.py", for_write=True).relative == "src/new.py"


@pytest.mark.parametrize(
    "raw",
    [
        "../outside.txt",
        "src/../../outside.txt",
        "C:/Windows/System32/drivers/etc/hosts",
        "C:foo",  # drive-relative
        "\\\\server\\share\\file",  # UNC
        "\\\\?\\C:\\Windows",  # extended-length
        "\\\\.\\PhysicalDrive0",  # device namespace
        "//server/share/file",
        "file.txt:hidden",  # alternate data stream
        "NUL",
        "nul.txt",
        "COM1",
        "src/aux.log",
        "src/trailing. ",
        "trailing.",
        "",
        "   ",
    ],
)
def test_rejects_escapes_and_device_names(ws, raw):
    with pytest.raises(PathRejected):
        ws.resolve(raw)


def test_rejects_write_to_git_directory(ws):
    with pytest.raises(PathRejected):
        ws.resolve(".git/config", for_write=True)


def test_rejects_write_to_foundry_config(ws):
    (ws.root / ".foundry").mkdir()
    with pytest.raises(PathRejected):
        ws.resolve(".foundry/settings.toml", for_write=True)


def test_case_insensitive_containment(ws):
    # NTFS folds case; the check must too, or SRC/ vs src/ becomes a bypass.
    assert ws.resolve("SRC/APP.PY").absolute.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_rejects_junction_pointing_outside(ws, tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = ws.root / "escape"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:  # pragma: no cover - environment dependent
        pytest.skip(f"could not create junction: {result.stderr.strip()}")

    with pytest.raises(PathRejected, match="reparse point"):
        ws.resolve("escape/secret.txt")


def test_contains_helper(ws, tmp_path):
    assert ws.contains(ws.root / "src" / "app.py")
    assert not ws.contains(tmp_path.parent / "elsewhere.txt")


def test_root_must_be_a_directory(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(PathRejected):
        Workspace(target)
