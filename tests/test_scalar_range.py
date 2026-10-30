"""The color bar of a stress picture uses the same peak the title states."""

from pathlib import Path

import numpy as np
from PIL import ImageDraw

from aicad.render import render_scalar_views, scalar_range


def test_scale_reaches_a_peak_that_is_not_in_the_samples():
    low, high = scalar_range(np.array([0.0, 2.0, 57.48]), maximum=78.03)
    assert low == 0.0
    assert high == 78.03


def test_a_higher_drawn_sample_is_not_clipped():
    low, high = scalar_range(np.array([0.0, 8.0]), maximum=5.0)
    assert (low, high) == (0.0, 8.0)


def test_an_empty_mesh_still_has_a_span():
    low, high = scalar_range(np.array([]), maximum=None)
    assert high > low


def test_picture_footer_names_the_reported_peak(tmp_path: Path, monkeypatch):
    texts: list[str] = []
    real_draw = ImageDraw.Draw

    class _Capture:
        def __init__(self, image, mode=None):
            self._draw = real_draw(image, mode)

        def text(self, xy, text, *args, **kwargs):
            texts.append(str(text))
            return self._draw.text(xy, text, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._draw, name)

    monkeypatch.setattr(ImageDraw, "Draw", _Capture)
    vertices = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]], dtype=float)
    triangles = np.array([[0, 1, 2]], dtype=int)
    render_scalar_views(
        vertices,
        triangles,
        np.array([0.0, 1.0, 2.0]),
        tmp_path,
        "sample",
        "stress",
        "von Mises  max 9 MPa",
        "MPa",
        "von Mises",
        maximum=9.0,
    )
    joined = "\n".join(texts)
    assert "0 to 9 MPa" in joined
    assert "0 to 2 MPa" not in joined
