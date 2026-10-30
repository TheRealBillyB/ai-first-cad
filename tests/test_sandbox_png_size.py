"""A session picture is measured before it is decoded.

A picture past the pixel limit is refused from its header. The marker file
outside the project stays as it was written. Nothing is sent to another machine.
"""

from __future__ import annotations

import struct
import zlib

import pytest
from PIL import Image

from aicad.errors import GeometryError
from aicad.session_record import _load_png


def _png(width: int, height: int) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + header

    def block(body: bytes) -> bytes:
        return struct.pack(">I", len(body) - 4) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    end = block(b"IEND")
    return signature + block(chunk) + end


def test_a_large_picture_is_refused_before_it_is_decoded(tmp_path, monkeypatch):
    project = tmp_path / "project"
    frames = project / "frames"
    frames.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    (frames / "huge.png").write_bytes(_png(100_000, 100_000))

    def opened(*_args, **_kwargs):
        marker.write_text("CANARY_HIT\n", encoding="utf-8")
        raise AssertionError("decoded")

    monkeypatch.setattr("aicad.session_record.Image.open", opened)
    with pytest.raises(GeometryError, match="too large"):
        _load_png(project, "frames/huge.png")
    assert marker.read_text(encoding="utf-8") == "untouched\n"


def test_a_small_png_is_still_read(tmp_path):
    project = tmp_path / "project"
    frames = project / "frames"
    frames.mkdir(parents=True)
    Image.new("RGB", (4, 3), "red").save(frames / "small.png", format="PNG")
    picture = _load_png(project, "frames/small.png")
    assert picture.size == (4, 3)
