"""STEP export must write a solid, not an empty shell.

A flat-bottomed spline loft fused with side bumps and a cylinder tangent to
that face is a valid solid. Writing it and reading it back used to produce a
shell with no volume.
"""

from __future__ import annotations

import math

from build123d import (
    BuildLine,
    BuildSketch,
    Cylinder,
    Line,
    Plane,
    Spline,
    loft,
    make_face,
)

from aicad.kernel import adopt, export_step_file, import_step_file, make_from_ops


def _oval(half_w: float, height: float, n: int = 12) -> list[tuple[float, float]]:
    points = []
    for index in range(n + 1):
        angle = math.pi * index / n
        cosine = math.cos(angle)
        sine = math.sin(angle)
        wide = math.copysign(abs(cosine) ** 0.72, cosine)
        points.append((half_w * wide, height * (sine ** 0.82)))
    return points


def _face_at(x: float, points: list[tuple[float, float]]):
    with BuildSketch(Plane.YZ.offset(x)) as sketch:
        with BuildLine():
            Spline(*points, tangents=((0, 1), (0, -1)), tangent_scalars=(1.4, 1.4))
            Line(points[-1], points[0])
        make_face()
    return sketch.sketch.face()


def _bar():
    stations = ((1.4, 3.5, 3.3), (7.0, 6.0, 5.4), (14.0, 7.0, 6.2), (19.8, 7.0, 5.7))
    return loft([_face_at(x, _oval(half_w, height)) for x, half_w, height in stations])


def _bump_section(x: float, y_lo: float, y_hi: float, height: float):
    mid = (y_lo + y_hi) / 2.0
    half = (y_hi - y_lo) / 2.0
    points = []
    for index in range(11):
        angle = math.pi * index / 10
        points.append((mid + half * math.cos(angle), height * (math.sin(angle) ** 0.78)))
    return _face_at(x, points)


def _bumps():
    stations = ((17.0, 4.0), (21.0, 5.5), (25.0, 7.0), (27.4, 5.5), (29.4, 4.0))
    positive = loft([_bump_section(x, 4.4, 7.0, height) for x, height in stations])
    negative = loft([_bump_section(x, -7.0, -4.4, height) for x, height in stations])
    return positive + negative


def _tangent_pin():
    # Radius 2, center 2 mm above the flat face, so the cylinder is tangent to it.
    return Plane(origin=(26.0, 0.0, 2.0), z_dir=(0, 1, 0)) * Cylinder(2.0, 12.8)


def _fused():
    return _bar() + _bumps() + _tangent_pin()


def test_loft_fused_with_a_tangent_cylinder_exports_a_solid(tmp_path):
    solid = _fused()
    source = float(solid.volume)
    first = tmp_path / "first.step"
    export_step_file(adopt(solid), first)
    loaded = import_step_file(first)
    # The imported solid is still valid, and a second write used to drop it to a shell.
    second = tmp_path / "second.step"
    export_step_file(loaded, second)
    again = import_step_file(second)
    assert abs(again.impl.volume - source) / source < 0.02


def test_box_step_export_is_unchanged(tmp_path):
    shape = make_from_ops([{"op": "add", "shape": "box", "size": [10, 8, 4]}])
    path = tmp_path / "box.step"
    export_step_file(shape, path)
    loaded = import_step_file(path)
    assert abs(loaded.impl.volume - 320.0) < 1e-6
