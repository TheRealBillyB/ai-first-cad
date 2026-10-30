"""Overhang pictures color each face, and full red is the support angle."""

from types import SimpleNamespace

import numpy as np
import pytest

from aicad.printprep import _render
from aicad.render import _colormap, scalar_range


def _capture(monkeypatch):
    captured = {}

    def grab(vertices, triangles, scalars, *args, maximum=None, minimum=None, **kwargs):
        captured["vertices"] = np.array(vertices)
        captured["triangles"] = np.array(triangles)
        captured["scalars"] = np.array(scalars, dtype=float)
        captured["maximum"] = maximum
        captured["minimum"] = minimum
        return []

    monkeypatch.setattr("aicad.printprep.render_scalar_views", grab)
    return captured


def test_each_face_keeps_its_angle_and_full_red_is_45_degrees(tmp_path, monkeypatch):
    captured = _capture(monkeypatch)
    # A downward face and a vertical wall share two vertices. Averaging those
    # vertices would paint both faces with a blend. Straight down is 90 degrees
    # from vertical; the picture's red end is the 45 degree support angle.
    vertices = np.array(
        [
            [0.0, 0.0, 10.0],
            [0.0, 1.0, 10.0],
            [1.0, 0.0, 10.0],
            [0.0, 1.0, 0.0],
        ]
    )
    triangles = np.array([[0, 1, 2], [0, 1, 3]])
    _render(SimpleNamespace(vertices=vertices, triangles=triangles), "z_up", 45.0, 5.0, tmp_path, "block")
    scalars = captured["scalars"]
    assert captured["maximum"] == 45.0
    assert captured["minimum"] == 0.0
    assert scalars[0:3] == pytest.approx([45.0, 45.0, 45.0])
    assert scalars[3:6] == pytest.approx([0.0, 0.0, 0.0])
    low, high = scalar_range(scalars, maximum=captured["maximum"], minimum=captured["minimum"])
    assert (low, high) == (0.0, 45.0)
    assert np.allclose(_colormap(1.0), _colormap((45.0 - low) / (high - low)))

    lean = np.array(
        [
            [0.0, 0.0, 10.0],
            [1.0, 0.0, 10.0],
            [0.0, -0.5, 10.86602540378],
        ]
    )
    _render(
        SimpleNamespace(vertices=lean, triangles=np.array([[0, 1, 2]])),
        "z_up",
        45.0,
        5.0,
        tmp_path,
        "lean",
    )
    assert captured["scalars"] == pytest.approx([30.0, 30.0, 30.0], abs=0.05)
    assert captured["maximum"] == 45.0

    _render(SimpleNamespace(vertices=vertices, triangles=triangles), "z_up", 60.0, 5.0, tmp_path, "block")
    assert captured["scalars"][0:3] == pytest.approx([60.0, 60.0, 60.0])
    assert captured["maximum"] == 60.0
