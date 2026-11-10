"""`/image` with no path: the image on the clipboard goes with the next
message (D-065), as codex's Ctrl+V pastes a screenshot.

The clipboard is read by Windows PowerShell -- found on PATH by its absolute
path (D-049), single-threaded apartment, no profile, nothing on its input --
which writes the image as PNG in base64; it is then checked as a file's image
would be. The tests never touch the user's clipboard: the live ones swap the
clipboard read in the script for a bitmap PowerShell draws itself.
"""

from __future__ import annotations

import base64
import struct
import subprocess
import sys
import zlib

import pytest

from foundry.cli.attachments import (
    CLIPBOARD_NAME,
    CLIPBOARD_SCRIPT,
    CLIPBOARD_TIMEOUT_S,
    attach,
    attach_clipboard,
    clipboard_image,
)
from foundry.core.conversation import ImageBlock
from foundry.core.errors import ImageRejected
from foundry.core.images import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE
from foundry.core.winapi import CREATE_NO_WINDOW

from test_resume import _wire, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown

windows = pytest.mark.skipif(sys.platform != "win32", reason="the clipboard is read on Windows")
CLIPBOARD_READ = "[System.Windows.Forms.Clipboard]::GetImage()"


def _png(width: int = 1, height: int = 1) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    rows = b"".join(b"\0" + b"\0\0\0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


PNG_1x1 = _png()


def _drawn(monkeypatch, width=4, height=3):
    """The script as it is, reading a bitmap PowerShell draws, not the clipboard."""
    assert CLIPBOARD_SCRIPT.count(CLIPBOARD_READ) == 1
    monkeypatch.setattr("foundry.cli.attachments.CLIPBOARD_SCRIPT", CLIPBOARD_SCRIPT.replace(
        CLIPBOARD_READ, f"(New-Object System.Drawing.Bitmap {width}, {height})"))


class _Run:
    """subprocess.run, as the clipboard read calls it."""

    def __init__(self, returncode=0, stdout="", raises=None):
        self.returncode, self.stdout, self.raises = returncode, stdout, raises
        self.calls: list = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


# --- reading it, live ---------------------------------------------------------------------


@windows
def test_the_image_comes_back_as_png(monkeypatch):
    _drawn(monkeypatch)
    data = clipboard_image()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(data[16:20], "big") == 4 and int.from_bytes(data[20:24], "big") == 3


@windows
def test_a_clipboard_without_an_image_gives_none(monkeypatch):
    monkeypatch.setattr("foundry.cli.attachments.CLIPBOARD_SCRIPT",
                        CLIPBOARD_SCRIPT.replace(CLIPBOARD_READ, "$null"))
    assert clipboard_image() is None


@windows
def test_slash_image_alone_attaches_it_to_the_next_message(repo, tmp_path, monkeypatch):
    _drawn(monkeypatch)
    wiring = _wire(repo, tmp_path / "home", [turn(text="a tiny picture")])
    shown = _shown(_repl(wiring, "/image", "what is this?"))
    assert f"attached {CLIPBOARD_NAME} (PNG, 4x3, " in shown
    assert "it goes with your next message" in shown
    sent = wiring.runtime.backend.seen[0].messages[-1].blocks
    assert [type(b) for b in sent] == [ImageBlock, type(sent[-1])]
    assert sent[0].media_type == "image/png" and sent[0].name == CLIPBOARD_NAME


# --- how PowerShell is started ------------------------------------------------------------


def test_powershell_is_started_by_its_path_with_nothing_on_its_input(monkeypatch):
    run = _Run(stdout=base64.b64encode(PNG_1x1).decode())
    monkeypatch.setattr("foundry.cli.attachments.IS_WINDOWS", True)
    monkeypatch.setattr("foundry.cli.attachments.find_program",
                        lambda name: r"C:\Windows\System32\powershell.exe"
                        if name == "powershell.exe" else None)
    monkeypatch.setattr(subprocess, "run", run)
    assert clipboard_image() == PNG_1x1
    [(argv, kwargs)] = run.calls
    assert argv == [r"C:\Windows\System32\powershell.exe", "-NoProfile", "-NonInteractive",
                    "-Sta", "-Command", CLIPBOARD_SCRIPT]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == CLIPBOARD_TIMEOUT_S
    assert kwargs["creationflags"] == CREATE_NO_WINDOW


@pytest.mark.parametrize("run, said", [
    (_Run(returncode=1), "the clipboard could not be read (PowerShell exit 1)"),
    (_Run(stdout="aGVsbG8=!!"), "what the clipboard gave back is not an image"),
    (_Run(raises=subprocess.TimeoutExpired("powershell", 15)),
     "reading the clipboard took over 15s"),
    (_Run(raises=OSError("no such program")), "the clipboard could not be read (no such program)"),
])
def test_a_clipboard_that_cannot_be_read_says_why(monkeypatch, run, said):
    monkeypatch.setattr("foundry.cli.attachments.IS_WINDOWS", True)
    monkeypatch.setattr("foundry.cli.attachments.find_program", lambda name: "powershell.exe")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ImageRejected) as caught:
        clipboard_image()
    assert str(caught.value) == said


def test_without_powershell_it_says_so(monkeypatch):
    monkeypatch.setattr("foundry.cli.attachments.IS_WINDOWS", True)
    monkeypatch.setattr("foundry.cli.attachments.find_program", lambda name: None)
    with pytest.raises(ImageRejected, match="powershell.exe was not found on PATH"):
        clipboard_image()


def test_elsewhere_it_says_windows_only(monkeypatch):
    monkeypatch.setattr("foundry.cli.attachments.IS_WINDOWS", False)
    with pytest.raises(ImageRejected, match="read on Windows only"):
        clipboard_image()


# --- what /image says ---------------------------------------------------------------------


def _clipboard(monkeypatch, data):
    reads: list = []

    def read():
        reads.append(1)
        return data

    monkeypatch.setattr("foundry.cli.attachments.clipboard_image", read)
    return reads


def test_an_empty_clipboard_attaches_nothing(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch, None)
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, "/image"))
    assert "the clipboard holds no image; /image PATH attaches a file" in shown
    assert wiring.images == []


def test_an_image_too_large_is_refused_as_a_file_s_would_be(repo, tmp_path, monkeypatch):
    _clipboard(monkeypatch, PNG_1x1 + b"\0" * MAX_IMAGE_BYTES)
    wiring = _wire(repo, tmp_path / "home", [])
    shown = _shown(_repl(wiring, "/image"))
    assert f"[red]cannot attach {CLIPBOARD_NAME}: it is" in shown and wiring.images == []


def test_a_backend_without_images_does_not_read_it(repo, tmp_path, monkeypatch):
    reads = _clipboard(monkeypatch, PNG_1x1)
    wiring = _wire(repo, tmp_path / "home", [], overrides={"images": False})
    shown = _shown(_repl(wiring, "/image"))
    assert "set up without images" in shown and reads == []


def test_a_full_message_does_not_read_it(repo, tmp_path, monkeypatch):
    reads = _clipboard(monkeypatch, PNG_1x1)
    wiring = _wire(repo, tmp_path / "home", [])
    for _ in range(MAX_IMAGES_PER_MESSAGE):
        assert attach_clipboard(wiring, _Silent())
    assert not attach_clipboard(wiring, _Silent())
    assert len(reads) == MAX_IMAGES_PER_MESSAGE


def test_an_empty_dash_i_is_still_a_usage_error(repo, tmp_path, monkeypatch):
    reads = _clipboard(monkeypatch, PNG_1x1)
    wiring = _wire(repo, tmp_path / "home", [])
    console = _Silent()
    assert not attach(wiring, console, "")
    assert console.printed == ["[yellow]usage: /image PATH[/yellow]"] and reads == []


def test_help_shows_the_path_is_optional(repo, tmp_path):
    assert "/image [PATH]" in _shown(_repl(_wire(repo, tmp_path / "home", []), "/help"))


class _Silent:
    def __init__(self):
        self.printed: list[str] = []

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))
