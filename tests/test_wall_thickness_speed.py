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


def _drilled_disc():
    """A brake-rotor-like disc: three rings of cross-drilled holes."""
    import math

    ops = [
        {"op": "add", "shape": "cylinder", "radius": 40, "height": 5},
        {"op": "cut", "shape": "cylinder", "radius": 18, "height": 20},
    ]
    for ring, (radius, count) in enumerate([(24, 12), (30, 16), (36, 20)]):
        for index in range(count):
            angle = 2 * math.pi * (index + 0.5 * ring) / count
            ops.append(
                {
                    "op": "cut",
                    "shape": "cylinder",
                    "radius": 1.5,
                    "height": 20,
                    "center": [radius * math.cos(angle), radius * math.sin(angle), 0],
                }
            )
    return kernel.make_from_ops(ops)


def test_the_sections_build_one_classifier_for_all_slices(monkeypatch):
    """A rotor's wall check took minutes: each section hit built a classifier of its own."""
    built = []
    real = kernel.BRepClass3d_SolidClassifier

    def counting(*args):
        built.append(len(args))
        return real(*args)

    monkeypatch.setattr(kernel, "BRepClass3d_SolidClassifier", counting)
    minimum, count, capped = kernel._section_wall_thickness(_tube())
    assert count == 9 and minimum is not None and abs(minimum - 1.5) < 0.05 and not capped
    assert built == [1], f"{len(built)} classifiers were built for nine slices"


def test_skipping_edges_a_probe_cannot_reach_gives_the_same_reading(monkeypatch):
    """Each probe asked every other edge of the slice for a hit, through build123d."""
    from build123d import Edge

    shape = _drilled_disc()
    calls = {"count": 0}
    real_intersect = Edge.intersect

    def counting(self, *args, **kwargs):
        calls["count"] += 1
        return real_intersect(self, *args, **kwargs)

    monkeypatch.setattr(Edge, "intersect", counting)
    fast = kernel._section_wall_thickness(shape)
    fast_calls = calls["count"]

    calls["count"] = 0
    monkeypatch.setattr(kernel, "_ray_meets_box", lambda *_args: True)
    every_edge = kernel._section_wall_thickness(shape)
    assert fast == every_edge
    assert fast[0] is not None and not fast[2]
    assert fast_calls * 5 < calls["count"], (fast_calls, calls["count"])


def test_a_ray_meets_only_the_boxes_ahead_of_it():
    from build123d import Vector

    box = ((1.0, -1.0, -1.0), (2.0, 1.0, 1.0))
    assert kernel._ray_meets_box(Vector(0, 0, 0), Vector(1, 0, 0), box)
    assert not kernel._ray_meets_box(Vector(0, 0, 0), Vector(-1, 0, 0), box)
    assert not kernel._ray_meets_box(Vector(0, 2, 0), Vector(1, 0, 0), box)
    assert kernel._ray_meets_box(Vector(0, 0, 0), Vector(1, 0.9, 0).normalized(), box)
    # A ray that starts inside the box meets it.
    assert kernel._ray_meets_box(Vector(1.5, 0, 0), Vector(0, 0, 1), box)
    # On the box's face, parallel to it.
    assert kernel._ray_meets_box(Vector(0, 1.0, 0), Vector(1, 0, 0), box)
