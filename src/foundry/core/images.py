"""Images the model is shown (D-045): checked, sized and encoded here.

Standard library only, so nothing is resized: an image is sent as it is or not
at all. What is checked is what a provider refuses -- a type it does not take,
a file over its size limit, a side over its pixel limit -- so a bad image is a
clear error here rather than a 400 that ends the turn.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path

from foundry.core.conversation import ImageBlock
from foundry.core.errors import ImageRejected

# Anthropic's limit is 5 MB of base64 per image; this many raw bytes encode to
# just under it, and the OpenAI-family limits are larger.
MAX_IMAGE_BYTES = 3_750_000
MAX_IMAGE_SIDE = 8000
# What one image is counted as when sizing the context: about what a provider
# charges for an image near its recommended size.
IMAGE_TOKEN_ESTIMATE = 1600
# At most this many images ride on one message.
MAX_IMAGES_PER_MESSAGE = 8

_LABELS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF",
           "image/webp": "WebP"}


@dataclass(frozen=True, slots=True)
class Picture:
    """An image ready to send, with what is worth saying about it."""

    block: ImageBlock
    width: int
    height: int
    size: int

    def describe(self) -> str:
        label = _LABELS.get(self.block.media_type, self.block.media_type)
        return f"{label}, {self.width}x{self.height}, {_size(self.size)}"


def _size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{max(1, round(size / 1000))} KB"


def _png(data: bytes) -> tuple[int, int] | None:
    if data[12:16] != b"IHDR" or len(data) < 24:
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _gif(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")


def _jpeg(data: bytes) -> tuple[int, int] | None:
    """The frame header's size, walking the segments to the first SOF."""
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:              # fill byte
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            i += 2                      # markers without a length
            continue
        if marker == 0xD9:              # the end, and no frame header
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return (int.from_bytes(data[i + 7:i + 9], "big"),
                    int.from_bytes(data[i + 5:i + 7], "big"))
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2:
            return None
        i += 2 + length
    return None


def _webp(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF)
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return (int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1)
    return None


def sniff(data: bytes) -> tuple[str, tuple[int, int] | None]:
    """The media type from the file's own first bytes, and its size in pixels
    if the header could be read. The name or a declared type is not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", _png(data)
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", _jpeg(data)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", _gif(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", _webp(data)
    raise ImageRejected("it is not a PNG, JPEG, GIF or WebP image")


def from_bytes(data: bytes, name: str) -> Picture:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRejected(f"it is {_size(len(data))}; images over "
                            f"{_size(MAX_IMAGE_BYTES)} are not sent")
    media_type, dimensions = sniff(data)
    if dimensions is None or min(dimensions) < 1:
        raise ImageRejected(f"its {_LABELS[media_type]} header could not be read; "
                            "it may be damaged")
    width, height = dimensions
    if max(width, height) > MAX_IMAGE_SIDE:
        raise ImageRejected(f"it is {width}x{height}; a side over {MAX_IMAGE_SIDE} "
                            "pixels is refused by the providers")
    block = ImageBlock(media_type=media_type,
                       data=base64.b64encode(data).decode("ascii"), name=name)
    return Picture(block=block, width=width, height=height, size=len(data))


def load(path: Path, name: str) -> Picture:
    """A file as an image, refused before it is read if it is too large."""
    try:
        if not path.is_file():
            raise ImageRejected("there is no such file")
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise ImageRejected(f"it is {_size(size)}; images over "
                                f"{_size(MAX_IMAGE_BYTES)} are not sent")
        data = path.read_bytes()
    except OSError as exc:
        raise ImageRejected(f"it could not be read ({exc.strerror or exc})") from exc
    return from_bytes(data, name)


def from_base64(data: object, name: str) -> Picture:
    """An image a tool returned as base64 (an MCP server's image content)."""
    if not isinstance(data, str) or not data:
        raise ImageRejected("it carried no data")
    if len(data) * 3 // 4 > MAX_IMAGE_BYTES + 3:
        raise ImageRejected(f"it is over {_size(MAX_IMAGE_BYTES)}")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageRejected("its data is not valid base64") from exc
    return from_bytes(raw, name)


def data_url(block: ImageBlock) -> str:
    """The form the OpenAI-family protocols take an inline image in."""
    return f"data:{block.media_type};base64,{block.data}"
