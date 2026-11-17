"""A file in a helper's private folder keeps its exact bytes.

On Windows a descriptor opens in text mode unless asked otherwise, so a write
turned each LF byte into CR LF. Every PNG frame given to ffmpeg arrived with
a broken signature, and no video was written.
"""

import io

from PIL import Image

from aicad.tool_directory import held_read_bytes, held_subprocess_directory, held_write_bytes

_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) + b"\n\r\n\r"


def test_bytes_written_and_read_back_are_unchanged():
    with held_subprocess_directory() as folder:
        held_write_bytes(folder, "data.bin", _BYTES)
        assert (folder.original / "data.bin").read_bytes() == _BYTES
        assert held_read_bytes(folder, "data.bin", maximum=10_000) == _BYTES


def test_a_bytes_file_written_outside_reads_back_unchanged():
    with held_subprocess_directory() as folder:
        (folder.original / "video.mp4").write_bytes(_BYTES)
        assert held_read_bytes(folder, "video.mp4", maximum=10_000) == _BYTES


def test_a_png_frame_still_opens():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    with held_subprocess_directory() as folder:
        held_write_bytes(folder, "frame_0000.png", buffer.getvalue())
        with Image.open(folder.original / "frame_0000.png") as picture:
            assert picture.size == (8, 8)
