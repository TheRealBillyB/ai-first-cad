"""A fraction of full scale stays a shaded color in an assembly picture."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aicad.document import Document
from aicad.render import _PAPER


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


def test_a_fractional_color_is_shaded_and_a_byte_color_stays_a_byte(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "palm", [40, 24, 16], [0, 0, 8])
    _box(document, "link", [18, 10, 8], [9, 0, 4])
    document.create_assembly(
        {
            "name": "mechanism",
            "instances": [
                {"name": "palm", "part": "palm", "color": [0.75, 0.78, 0.82], "material": "aluminum_6061"},
                {"name": "link", "part": "link", "color": [10, 20, 30], "material": "steel"},
            ],
            "joints": [
                {
                    "name": "hinge",
                    "type": "revolute",
                    "parent": "palm",
                    "child": "link",
                    "parent_anchor": [20, 0, 8],
                    "child_anchor": [-9, 0, 4],
                    "axis": [0, 1, 0],
                    "value": 35,
                }
            ],
        }
    )
    mesh = document.assembly_mesh("mechanism")
    triples = {
        (round(red, 1), round(green, 1), round(blue, 1))
        for red, green, blue in zip(mesh["colors"][0::3], mesh["colors"][1::3], mesh["colors"][2::3])
    }
    assert (10.0, 20.0, 30.0) in triples
    assert any(abs(red - 0.75 * 255) < 0.2 and abs(blue - 0.82 * 255) < 0.2 for red, _green, blue in triples)

    picture = Path(document.render_assembly("mechanism")["pictures"]["frames"][0]["path"])
    image = np.asarray(Image.open(picture).convert("RGB")).astype(int)
    paper = np.array(_PAPER)
    pixels = image[np.abs(image - paper).sum(axis=2) > 25]
    assert len(pixels) > 1000
    exact_black = np.all(pixels == 0, axis=1).mean()
    luminance = pixels.mean(axis=1)
    assert exact_black < 0.2
    assert float(luminance.mean()) > 40
    assert float(luminance.std()) > 8
