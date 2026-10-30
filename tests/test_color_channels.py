"""Int colors are bytes. Float colors from 0 to 1 are fractions of full scale."""

import pytest

from aicad.animation import _parse_color
from aicad.assembly import _byte_channels
from aicad.document import Document
from aicad.errors import GeometryError


def test_an_int_color_stays_bytes_and_a_float_color_is_a_fraction():
    assert _byte_channels((1, 1, 0)) == (1.0, 1.0, 0.0)
    assert _byte_channels((0, 0, 0)) == (0.0, 0.0, 0.0)
    assert _byte_channels((10, 20, 30)) == (10.0, 20.0, 30.0)
    assert _byte_channels((1.0, 1.0, 0.0)) == (255.0, 255.0, 0.0)
    assert _byte_channels((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    assert _byte_channels((0.75, 0.78, 0.82)) == pytest.approx((0.75 * 255, 0.78 * 255, 0.82 * 255))
    assert _byte_channels((10.0, 20.0, 30.0)) == (10.0, 20.0, 30.0)
    assert _parse_color((1, 1, 0), "Color for link") == (1.0, 1.0, 0.0)
    assert _parse_color((1.0, 0.0, 0.0), "Color for link") == (255.0, 0.0, 0.0)
    with pytest.raises(GeometryError, match="three ints"):
        _byte_channels((1, 1.0, 0))
    with pytest.raises(GeometryError, match="three ints"):
        _parse_color([1, 0.5, 0], "Color for link")
    with pytest.raises(GeometryError, match="byte from 0 to 255"):
        _byte_channels((1, 1, 256))


def test_a_saved_byte_color_is_still_a_byte_after_reload(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 0]}],
    )
    document.create_assembly(
        {
            "name": "painted",
            "instances": [{"name": "block", "part": "block", "color": [1, 1, 0]}],
        }
    )
    reopened = Document(tmp_path)
    color = reopened.get_assembly("painted")["assembly"]["instances"][0]["color"]
    assert color == [1, 1, 0]
    assert all(type(channel) is int for channel in color)
    mesh = reopened.assembly_mesh("painted")
    assert mesh["colors"][0:3] == [1.0, 1.0, 0.0]
