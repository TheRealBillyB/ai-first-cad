"""A file in a helper's private folder keeps its exact bytes.

On Windows a descriptor opens in text mode unless asked otherwise, so a write
turned each LF byte into CR LF. Every PNG frame given to ffmpeg arrived with
a broken signature, and no video was written. The binary STL given to the
slicer grew by a byte for each LF byte in it, and the slice failed.
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


def test_a_slicer_stl_keeps_its_size():
    # Ten triangles: the count field is 0A 00 00 00, so a text-mode write grew
    # the file by a byte and Bambu Studio refused it as the wrong size.
    import numpy as np

    from aicad.printprep import _stl_bytes

    vertices = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]] * 10)
    triangles = np.arange(30).reshape(10, 3)
    stl = _stl_bytes(vertices, triangles)
    assert b"\n" in stl
    with held_subprocess_directory() as folder:
        held_write_bytes(folder, "oriented.stl", stl)
        written = (folder.original / "oriented.stl").read_bytes()
    assert len(written) == 84 + 50 * 10
    assert written == stl
