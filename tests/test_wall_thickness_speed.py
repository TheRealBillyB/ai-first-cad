"""The ray wall-thickness estimate classifies points against one loaded solid.

Inspect on an 18-tooth spur gear (1,568 faces) ran for more than 8 minutes,
and every other command in the app waited behind it. Each inside test built a
new OpenCascade classifier from the whole solid, about 52 ms on that gear,
where a loaded classifier answers in about 3 ms. The estimate also asked each
point twice and checked every aligned hit when only the nearest that passes
is kept.
"""

from __future__ import annotations

from aicad import kernel


def _tube():
    return kernel.make_from_ops(
        [
            {"op": "add", "shape": "cylinder", "radius": 10, "height": 20},
            {"op": "cut", "shape": "cylinder", "radius": 8.5, "height": 30},
        ]
    )


def test_one_wall_estimate_builds_the_classifier_once(monkeypatch):
    built = []
    real = kernel.BRepClass3d_SolidClassifier

    def counting(*args):
        built.append(len(args))
        return real(*args)

    monkeypatch.setattr(kernel, "BRepClass3d_SolidClassifier", counting)
    minimum, accepted, _used, _point, capped = kernel._ray_wall_thickness(_tube(), 4)
    assert accepted > 50
    assert minimum is not None and abs(minimum - 1.5) < 0.05 and not capped
    assert built == [1], f"{len(built)} classifiers were built for one estimate"


def test_the_loaded_test_agrees_with_the_one_point_test():
    shape = _tube()
    inside = kernel._InsideTest(shape.impl)
    from build123d import Vector

    points = [Vector(x * 0.5, y * 0.5, z) for x in range(-24, 25, 3) for y in range(-24, 25, 3) for z in (-1, 0, 5, 10, 20, 21)]
    assert [inside(point) for point in points] == [kernel._strictly_inside(shape.impl, point) for point in points]
    assert any(inside(point) for point in points) and not all(inside(point) for point in points)
