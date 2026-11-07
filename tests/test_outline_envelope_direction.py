"""An outline envelope runs along its axis whichever way its points are listed.

A clockwise outline with length 5 built its prism from z = 0 to -5 instead of
0 to 5, because the extrude followed the face normal. A revolve does not
depend on winding; that is checked here too.
"""

import pytest

from aicad import Sketch
from aicad.kernel import make_envelope, solid_from_sketch
from aicad.sketch import solve_sketch

SQUARE_CCW = [[0, 0], [10, 0], [10, 8], [0, 8]]
SQUARE_CW = list(reversed(SQUARE_CCW))


def _box(shape):
    box = shape.impl.bounding_box()
    return [box.min.X, box.min.Y, box.min.Z, box.max.X, box.max.Y, box.max.Z]


@pytest.mark.parametrize("outline", [SQUARE_CCW, SQUARE_CW], ids=["anticlockwise", "clockwise"])
def test_an_outline_runs_up_its_axis_from_the_start(outline):
    box = _box(make_envelope({"outline": outline, "length": 5}))
    assert box == pytest.approx([0, 0, 0, 10, 8, 5], abs=1e-6)


@pytest.mark.parametrize("outline", [SQUARE_CCW, SQUARE_CW], ids=["anticlockwise", "clockwise"])
def test_an_outline_limit_on_a_tilted_axis_is_the_same_either_way(outline):
    spec = {"outline": outline, "axis": [1, 0, 0], "limit": [2, 7]}
    other = {**spec, "outline": SQUARE_CCW if outline is SQUARE_CW else SQUARE_CW}
    assert _box(make_envelope(spec)) == pytest.approx(_box(make_envelope(other)), abs=1e-6)
    box = _box(make_envelope(spec))
    assert box[0] == pytest.approx(2) and box[3] == pytest.approx(7)


@pytest.mark.parametrize("angle", [90, 180, 360])
def test_a_revolve_does_not_depend_on_winding(angle):
    sketch = (
        Sketch("xy")
        .point("a", 5, 0).fix("a").point("b", 10, 0).fix("b").point("c", 10, 8).fix("c").point("d", 5, 8).fix("d")
        .line("ab", "a", "b").line("bc", "b", "c").line("cd", "c", "d").line("da", "d", "a")
    )
    geometry = solve_sketch(sketch).geometry
    shapes = [
        solid_from_sketch(geometry, [{"op": "revolve", "points": loop, "axis": "y", "angle": angle, "name": "r"}])
        for loop in (["a", "b", "c", "d"], ["a", "d", "c", "b"])
    ]
    assert _box(shapes[0]) == pytest.approx(_box(shapes[1]), abs=1e-6)
    assert shapes[0].impl.volume == pytest.approx(shapes[1].impl.volume)
