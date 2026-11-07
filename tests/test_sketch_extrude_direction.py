"""An extrude goes up the sketch normal whichever way the profile was drawn.

A rectangle drawn from its top-left corner is a clockwise loop. Its face
normal points down, and the extrude followed that normal, so the solid grew
below the sketch plane, and a clockwise cut went the wrong way.
"""

import pytest

from aicad import Sketch
from aicad.kernel import solid_from_sketch
from aicad.sketch import solve_sketch
from aicad.sketch_plane import sketch_frame

ANTICLOCKWISE = ["a", "b", "c", "d"]
CLOCKWISE = ["a", "d", "c", "b"]


def _square(plane="xy", size=10):
    return (
        Sketch(plane)
        .point("a", 0, 0).fix("a")
        .point("b", size, 0).fix("b")
        .point("c", size, size).fix("c")
        .point("d", 0, size).fix("d")
        .line("ab", "a", "b").line("bc", "b", "c").line("cd", "c", "d").line("da", "d", "a")
    )


def _box(plane, steps):
    report = solve_sketch(_square(plane))
    shape = solid_from_sketch(report.geometry, steps, frame=sketch_frame(plane, "square"))
    box = shape.impl.bounding_box()
    return (box.min.X, box.min.Y, box.min.Z, box.max.X, box.max.Y, box.max.Z), shape.impl.volume


@pytest.mark.parametrize("plane", ["xy", "yz", "xz"])
def test_a_clockwise_profile_extrudes_the_same_way_as_an_anticlockwise_one(plane):
    up, up_volume = _box(plane, [{"op": "extrude", "points": ANTICLOCKWISE, "height": 4, "name": "pad"}])
    drawn, drawn_volume = _box(plane, [{"op": "extrude", "points": CLOCKWISE, "height": 4, "name": "pad"}])
    assert drawn == pytest.approx(up, abs=1e-6)
    assert drawn_volume == pytest.approx(up_volume)


def test_on_xy_the_solid_is_above_the_plane_and_starts_at_z():
    box, _volume = _box("xy", [{"op": "extrude", "points": CLOCKWISE, "height": 4, "z": 1, "name": "pad"}])
    assert box[2] == pytest.approx(1)
    assert box[5] == pytest.approx(5)


def test_a_clockwise_cut_removes_material():
    report = solve_sketch(_square("xy"))
    plain = solid_from_sketch(report.geometry, [{"op": "extrude", "points": ANTICLOCKWISE, "height": 4, "name": "pad"}])
    cut = solid_from_sketch(
        report.geometry,
        [
            {"op": "extrude", "points": ANTICLOCKWISE, "height": 4, "name": "pad"},
            {"op": "cut", "points": CLOCKWISE, "height": 2, "z": 2, "name": "pocket"},
        ],
    )
    assert cut.impl.volume == pytest.approx(plain.impl.volume / 2)
